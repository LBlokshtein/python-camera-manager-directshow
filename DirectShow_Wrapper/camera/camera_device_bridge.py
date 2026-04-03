import os
import sys
import clr
import ctypes
import numpy as np
import threading
import time
from types import SimpleNamespace
from enum import Enum, IntEnum
from typing import Optional, Callable, Tuple, Sequence, Union, TYPE_CHECKING

if TYPE_CHECKING:
    from .camera_manager import CameraFormat

DebugLoggingConfig = Union[bool, int, Sequence[bool]]

try:
    import cv2
except Exception:
    cv2 = None

_turbojpeg_status_message = None
_turbojpeg_status_tier = None

try:
    from turbojpeg import TurboJPEG as _TurboJPEG

    # Try common Windows libjpeg-turbo DLL locations first, then TURBOJPEG_LIB_PATH.
    # Native DLL is required for PyTurboJPEG acceleration.
    # You can install libjpeg-turbo from - https://github.com/libjpeg-turbo/libjpeg-turbo/releases
    _turbojpeg_decoder = None
    _turbojpeg_init_error = None
    _turbojpeg_candidate_paths = [
        r"C:\libjpeg-turbo-gcc64\bin\libturbojpeg.dll",
        r"C:\libjpeg-turbo64\bin\turbojpeg.dll",
        r"C:\libjpeg-turbo64\bin\libturbojpeg.dll",
        os.environ.get("TURBOJPEG_LIB_PATH"),
    ]

    for _dll_path in _turbojpeg_candidate_paths:
        if not _dll_path:
            continue
        if not os.path.isfile(_dll_path):
            continue
        try:
            _turbojpeg_decoder = _TurboJPEG(lib_path=_dll_path)
            _turbojpeg_status_message = f"turbojpeg loaded from {_dll_path}"
            _turbojpeg_status_tier = 1
            break
        except Exception as _explicit_err:
            _turbojpeg_init_error = _explicit_err

    if _turbojpeg_decoder is None:
        try:
            _turbojpeg_decoder = _TurboJPEG()
            _turbojpeg_status_message = "turbojpeg loaded via auto-discovery"
            _turbojpeg_status_tier = 1
        except Exception as _auto_err:
            _turbojpeg_init_error = _auto_err
            _turbojpeg_status_message = f"turbojpeg unavailable: {_turbojpeg_init_error}"
            _turbojpeg_status_tier = 2
except Exception as _import_err:
    _turbojpeg_status_message = f"turbojpeg import failed: {_import_err}"
    _turbojpeg_status_tier = 2
    _turbojpeg_decoder = None


class DebugTier(IntEnum):
    VERBOSE = 1
    ERROR = 2


class CaptureMode(str, Enum):
    NONE = "none"
    EVENT_DRIVEN = "event_driven"

class CameraDeviceBridge:
    """
    Python wrapper for the .NET CameraDevice class.
    Provides access to DirectShow camera streaming functionality.
    """

    # Debug tiers:
    # 1 = verbose/everything
    # 2 = errors only
    DEBUG_TIER_VERBOSE = DebugTier.VERBOSE
    DEBUG_TIER_ERROR = DebugTier.ERROR
    DebugTier = DebugTier
    CaptureMode = CaptureMode
    _turbojpeg_status_reported = False

    def __init__(
        self,
        device_path: str,
        camera_format: Optional['CameraFormat'] = None,
        debug_logging: DebugLoggingConfig = False,
        request_rgb24_conversion: bool = False
    ):
        """
        ==========================================
        Initialize the camera device bridge.
        
        Args:
            device_path: The device path from CameraDeviceInfo
            camera_format: Optional CameraFormat NamedTuple with width, height, fps, pixel_format
            debug_logging: Enable verbose bridge debug logs
            request_rgb24_conversion: Request RGB24 conversion in .NET SampleGrabber path
        ==========================================
        """
        self._device = None                 # Instance of the .NET CameraDevice
        self._camera_device_class = None    # Reference to the .NET CameraDevice type/class
        self._dotnet_log_file_path = None   # Optional .NET-side log file destination
        self._dotnet_log_limits = None      # Optional cached .NET log retention/size settings
        self._frame_callback = None         # User callback invoked when a frame is processed
        self._format = camera_format        # Selected CameraFormat (width, height, fps, pixel format)
        self._pixel_format_name = ""
        self._bytes_per_pixel = 0
        self._frame_width = int(getattr(camera_format, "width", 0) or 0)
        self._frame_height = int(getattr(camera_format, "height", 0) or 0)
        self._yuy2_buffer_size = 0
        self._raw_buffer_size = 0
        self._shape_yuy2 = None
        self._shape_gray = None
        self._shape_rgb = None
        self._shape_raw = None
        self._request_rgb24_conversion = bool(request_rgb24_conversion)

        # Boolean flags per debug tier index.
        # Index 0 is unused for readability, tier IDs start at 1.
        self.debug_tiers_enabled = [False, False, False]

        # Backward-compatible setup:
        # - False/0: no debug tiers
        # - True: verbose + errors
        # - int tier: enable only that tier
        # - list/tuple of bools: explicit per-tier control (copied into internal array)
        if isinstance(debug_logging, (list, tuple)):
            for i in range(1, min(len(self.debug_tiers_enabled), len(debug_logging))):
                self.debug_tiers_enabled[i] = bool(debug_logging[i])
        elif isinstance(debug_logging, int) and debug_logging in (self.DEBUG_TIER_VERBOSE, self.DEBUG_TIER_ERROR):
            self.debug_tiers_enabled[debug_logging] = True
        elif bool(debug_logging):
            self.debug_tiers_enabled[self.DEBUG_TIER_VERBOSE] = True
            self.debug_tiers_enabled[self.DEBUG_TIER_ERROR] = True

        self._log_turbojpeg_status_once()

        self._current_frame = None
        self._frame_state_lock = threading.Lock()
        self._event_subscription = None  # Keep reference to event handler
        self._capture_mode = self.CaptureMode.NONE  # Current frame acquisition mode
        self._warned_unsupported_pixel_format = False
        self._warned_mjpg_decoder_unavailable = False
        self._refresh_cached_format_metadata()
        self._initialize_bridge(device_path, camera_format)

    def _log_turbojpeg_status_once(self):
        """
        ==========================================
        Emit one-time TurboJPEG initialization status using bridge debug tiers.
        ==========================================
        """
        if CameraDeviceBridge._turbojpeg_status_reported:
            return

        if _turbojpeg_status_message is None:
            return

        tier = int(_turbojpeg_status_tier or self.DEBUG_TIER_VERBOSE)
        self.debug_print(_turbojpeg_status_message, tier)
        CameraDeviceBridge._turbojpeg_status_reported = True

    def _refresh_cached_format_metadata(self):
        """
        ==========================================
        Cache normalized pixel-format metadata used in frame hot paths.
        ==========================================
        """
        if self._format is None:
            self._pixel_format_name = ""
            self._bytes_per_pixel = 0
            self._frame_width = 0
            self._frame_height = 0
            self._yuy2_buffer_size = 0
            self._raw_buffer_size = 0
            self._shape_yuy2 = None
            self._shape_gray = None
            self._shape_rgb = None
            self._shape_raw = None
            return

        raw = str(getattr(self._format, "pixel_format", "") or "")
        self._pixel_format_name = raw.strip().upper()

        # .NET RGB24 conversion typically lands in BI_RGB byte order (B, G, R) in memory.
        # Treat converted frames as BGR24 so the GUI path (which expects BGR input) stays correct.
        if bool(self._request_rgb24_conversion):
            self._pixel_format_name = "BGR24"
        self._frame_width = int(getattr(self._format, "width", 0) or 0)
        self._frame_height = int(getattr(self._format, "height", 0) or 0)

        if self._pixel_format_name in ("RGB24", "BGR24"):
            self._bytes_per_pixel = 3
        elif self._pixel_format_name in ("RGB32", "BGR32", "ARGB32", "XRGB32"):
            self._bytes_per_pixel = 4
        elif self._pixel_format_name in ("GRAY8", "Y8"):
            self._bytes_per_pixel = 1
        else:
            self._bytes_per_pixel = 0

        self._yuy2_buffer_size = int(self._frame_width) * int(self._frame_height) * 2
        self._raw_buffer_size = int(self._frame_width) * int(self._frame_height) * int(self._bytes_per_pixel)
        self._shape_yuy2 = (self._frame_height, self._frame_width, 2)
        self._shape_gray = (self._frame_height, self._frame_width, 1)
        self._shape_rgb = (self._frame_height, self._frame_width, 3)
        self._shape_raw = (self._frame_height, self._frame_width, self._bytes_per_pixel)

    def _normalized_pixel_format_name(self) -> str:
        """
        ==========================================
        Return selected pixel format as an uppercase, trimmed string.
        ==========================================
        """
        return self._pixel_format_name

    def _bytes_per_pixel_for_current_format(self) -> int:
        """
        ==========================================
        Map current uncompressed pixel format to bytes-per-pixel.
        ==========================================
        """
        return int(self._bytes_per_pixel)

    def _build_dotnet_log_settings_from_tiers(self):
        """
        ==========================================
        Build .NET CameraDevice log-level settings from Python debug tiers.

        Returns:
            list[tuple[str, bool]]: [(log_type_name, enabled), ...]
        ==========================================
        """
        verbose_enabled = bool(self.debug_tiers_enabled[self.DEBUG_TIER_VERBOSE])
        error_enabled = bool(self.debug_tiers_enabled[self.DEBUG_TIER_ERROR])

        return [
            ("Error", bool(error_enabled or verbose_enabled)),
            ("Warning", bool(verbose_enabled)),
            ("Info", bool(verbose_enabled)),
            ("Debug", bool(verbose_enabled)),
        ]

    def _to_dotnet_log_settings_array(self, log_levels):
        """
        ==========================================
        Convert Python log-level settings to .NET ValueTuple[] for SetLogLevels.

        Args:
            log_levels: list[tuple[str, bool]] where names are Error/Warning/Info/Debug

        Returns:
            System.Array[ValueTuple[CameraDevice.LogType, bool]]
        ==========================================
        """
        if self._camera_device_class is None:
            raise RuntimeError("CameraDevice .NET type is not available")

        system_module = __import__("System")
        log_type_enum = self._camera_device_class.LogType
        tuple_type = system_module.ValueTuple[log_type_enum, system_module.Boolean]

        dotnet_items = []
        for log_type_name, enabled in log_levels:
            if not hasattr(log_type_enum, str(log_type_name)):
                continue
            enum_value = getattr(log_type_enum, str(log_type_name))
            dotnet_items.append(tuple_type(enum_value, bool(enabled)))

        return system_module.Array[tuple_type](dotnet_items)

    def _apply_dotnet_logging_configuration(self):
        """
        ==========================================
        Apply bridge logging configuration to the .NET CameraDevice instance.
        ==========================================
        """
        if self._device is None:
            return

        try:
            tier_settings = self._build_dotnet_log_settings_from_tiers()
            self.set_dotnet_log_levels(tier_settings)
        except Exception as e:
            self.debug_print(f"Failed to apply .NET log levels: {e}", self.DEBUG_TIER_ERROR)

        if self._dotnet_log_file_path is not None:
            try:
                self.set_dotnet_log_file_location(self._dotnet_log_file_path)
            except Exception as e:
                self.debug_print(f"Failed to apply .NET log file path: {e}", self.DEBUG_TIER_ERROR)

        if self._dotnet_log_limits is not None:
            try:
                self.set_dotnet_log_limits(**self._dotnet_log_limits)
            except Exception as e:
                self.debug_print(f"Failed to apply .NET log limits: {e}", self.DEBUG_TIER_ERROR)

    def set_dotnet_log_levels(self, log_levels):
        """
        ==========================================
        Set .NET CameraDevice log levels.

        Args:
            log_levels: list[tuple[str, bool]] where names are Error/Warning/Info/Debug

        Returns:
            bool: True if applied, False otherwise.
        ==========================================
        """
        allowed_log_level_names = {"Error", "Warning", "Info", "Debug"}
        normalized_levels = []

        if log_levels is not None:
            try:
                for item in log_levels:
                    if not isinstance(item, (tuple, list)) or len(item) != 2:
                        self.debug_print(
                            f"Skipping invalid log-level entry: {item}",
                            self.DEBUG_TIER_ERROR
                        )
                        continue

                    log_type_name, enabled = item

                    if isinstance(log_type_name, Enum):
                        parsed_name = str(log_type_name.value)
                    elif hasattr(log_type_name, "ToString"):
                        parsed_name = str(log_type_name.ToString())
                    else:
                        parsed_name = str(log_type_name)

                    if parsed_name not in allowed_log_level_names:
                        self.debug_print(
                            f"Skipping unknown log-level name: {parsed_name}",
                            self.DEBUG_TIER_ERROR
                        )
                        continue

                    normalized_levels.append((parsed_name, bool(enabled)))
            except Exception as e:
                self.debug_print(f"Invalid log-levels payload: {e}", self.DEBUG_TIER_ERROR)

        if len(normalized_levels) == 0:
            normalized_levels = self._build_dotnet_log_settings_from_tiers()

        if self._device is None:
            return False

        try:
            settings_array = self._to_dotnet_log_settings_array(normalized_levels)
            self._device.SetLogLevels(settings_array)
            return True
        except Exception as e:
            self.debug_print(f"Failed to set .NET log levels: {e}", self.DEBUG_TIER_ERROR)
            return False

    def get_dotnet_log_levels(self):
        """
        ==========================================
        Get .NET CameraDevice log levels.

        Returns:
            dict[str, bool]: {"Error": bool, "Warning": bool, "Info": bool, "Debug": bool}
        ==========================================
        """
        if self._device is None:
            return {}

        try:
            raw_levels = self._device.GetLogLevels()
            parsed = {}
            if raw_levels is None:
                return parsed

            for item in raw_levels:
                try:
                    parsed[str(item.Item1)] = bool(item.Item2)
                except Exception:
                    continue

            return parsed
        except Exception as e:
            self.debug_print(f"Failed to get .NET log levels: {e}", self.DEBUG_TIER_ERROR)
            return {}

    def set_dotnet_log_limits(
        self,
        max_log_size_bytes=None,
        max_log_age_milliseconds=None,
        target_log_age_milliseconds=None,
        limit_log_size=None,
        limit_log_time=None
    ):
        """
        ==========================================
        Configure .NET CameraDevice log limits.

        Returns:
            tuple or None: Result from .NET SetLogLimits
        ==========================================
        """
        self._dotnet_log_limits = {
            "max_log_size_bytes": max_log_size_bytes,
            "max_log_age_milliseconds": max_log_age_milliseconds,
            "target_log_age_milliseconds": target_log_age_milliseconds,
            "limit_log_size": limit_log_size,
            "limit_log_time": limit_log_time,
        }

        if self._device is None:
            return None

        try:
            return self._device.SetLogLimits(
                max_log_size_bytes,
                max_log_age_milliseconds,
                target_log_age_milliseconds,
                limit_log_size,
                limit_log_time,
            )
        except Exception as e:
            self.debug_print(f"Failed to set .NET log limits: {e}", self.DEBUG_TIER_ERROR)
            return None

    def get_dotnet_log_limits(self):
        """
        ==========================================
        Get current .NET CameraDevice log limits.

        Returns:
            dict: Parsed limit settings.
        ==========================================
        """
        if self._device is None:
            return {}

        try:
            raw_limits = self._device.GetLogLimits()
            if raw_limits is None:
                return {}

            return {
                "max_log_size_bytes": int(raw_limits.Item1),
                "max_log_age_milliseconds": int(raw_limits.Item2),
                "target_log_age_milliseconds": int(raw_limits.Item3),
                "limit_log_size": bool(raw_limits.Item4),
                "limit_log_time": bool(raw_limits.Item5),
            }
        except Exception as e:
            self.debug_print(f"Failed to get .NET log limits: {e}", self.DEBUG_TIER_ERROR)
            return {}

    def set_dotnet_log_file_location(self, log_file_path: str):
        """
        ==========================================
        Set .NET CameraDevice log file location.

        Returns:
            bool: True if accepted by .NET, False otherwise.
        ==========================================
        """
        self._dotnet_log_file_path = log_file_path

        if self._device is None:
            return False

        try:
            return bool(self._device.SetLogFileLocation(str(log_file_path)))
        except Exception as e:
            self.debug_print(f"Failed to set .NET log file location: {e}", self.DEBUG_TIER_ERROR)
            return False

    def clean_dotnet_log(self):
        """
        ==========================================
        Trigger .NET CameraDevice log cleanup immediately.

        Returns:
            bool: True if call succeeded, False otherwise.
        ==========================================
        """
        if self._device is None:
            return False

        try:
            self._device.CleanLog()
            return True
        except Exception as e:
            self.debug_print(f"Failed to clean .NET log: {e}", self.DEBUG_TIER_ERROR)
            return False

    def configure_bridge_logging(self, debug_tiers_enabled=None, log_file_path=None, log_limits=None):
        """
        ==========================================
        Configure bridge and .NET logging in one call.

        Args:
            debug_tiers_enabled: Optional list/tuple of bools by tier index.
            log_file_path: Optional .NET log file path.
            log_limits: Optional dict for SetLogLimits args.

        Returns:
            bool: True if configuration accepted.
        ==========================================
        """
        if debug_tiers_enabled is not None:
            for i in range(1, min(len(self.debug_tiers_enabled), len(debug_tiers_enabled))):
                self.debug_tiers_enabled[i] = bool(debug_tiers_enabled[i])

        if log_file_path is not None:
            self._dotnet_log_file_path = str(log_file_path)

        if isinstance(log_limits, dict):
            self._dotnet_log_limits = {
                "max_log_size_bytes": log_limits.get("max_log_size_bytes"),
                "max_log_age_milliseconds": log_limits.get("max_log_age_milliseconds"),
                "target_log_age_milliseconds": log_limits.get("target_log_age_milliseconds"),
                "limit_log_size": log_limits.get("limit_log_size"),
                "limit_log_time": log_limits.get("limit_log_time"),
            }

        self._apply_dotnet_logging_configuration()
        return True

    def debug_print(self, text: str, tier: int):
        """
        ==========================================
        Print debug text only if the requested tier is enabled.

        Args:
            text (str): Message text to print.
            tier (int): Debug tier ID.
        ==========================================
        """
        if 0 <= int(tier) < len(self.debug_tiers_enabled) and self.debug_tiers_enabled[int(tier)]:
            print(text)

    def _initialize_bridge(self, device_path: str, camera_format: Optional['CameraFormat']):
        """
        ==========================================
        Loads the DLLs and initializes the CameraDevice.
        ==========================================
        """
        current_dir = os.path.dirname(os.path.abspath(__file__))
        project_root = os.path.dirname(current_dir)
        dotnet_runtime_dir = os.path.join(project_root, "runtime", "dotnet")

        # Prefer runtime/dotnet layout, but keep camera/ fallback for compatibility.
        candidate_paths = [dotnet_runtime_dir, current_dir]
        for candidate in candidate_paths:
            if os.path.isdir(candidate) and candidate not in sys.path:
                sys.path.append(candidate)
        
        try:
            # Add references to .NET DLLs via dynamic lookup so type checkers
            # do not flag pythonnet's runtime-provided attributes.
            add_reference = getattr(clr, "AddReference")
            add_reference("DirectShowLib")
            add_reference("DirectShowLibWrapper")
            
            # Import the CameraDevice class and CameraFormat struct
            dotnet_wrapper = __import__("DirectShowLibWrapper")
            CameraDevice = getattr(dotnet_wrapper, "CameraDevice")
            DotNetCameraFormat = getattr(dotnet_wrapper, "CameraFormat")
            self._camera_device_class = CameraDevice
            
            # Create the device with or without format specification
            if camera_format:
                # Create a .NET CameraFormat struct
                dotnet_format = DotNetCameraFormat()
                dotnet_format.Width = camera_format.width
                dotnet_format.Height = camera_format.height
                dotnet_format.FrameRate = camera_format.fps
                dotnet_format.PixelFormat = camera_format.pixel_format
                
                # Create device with format
                self._device = CameraDevice(device_path, dotnet_format, bool(self._request_rgb24_conversion))
                
                try:
                    self._event_subscription = self._on_frame_ready_event
                    self._device.FrameReady += self._event_subscription
                    self._debug("FrameReady event subscribed successfully")
                except Exception as e:
                    self._debug(f"Event subscription failed. Frame callbacks unavailable: {e}")
                    self._event_subscription = None
            else:
                # Create device with default format
                self._device = CameraDevice(device_path)
                self._debug("Warning: Device created without dimensions. Frame reading not supported.")

            self._apply_dotnet_logging_configuration()
            
        except Exception as e:
            self.debug_print(f"CRITICAL: Failed to initialize CameraDevice: {e}", self.DEBUG_TIER_ERROR)
            self._device = None
    
    def start(self):
        """
        ==========================================
        Start the camera streaming.
        ==========================================
        """
        if not self._device:
            raise RuntimeError("Camera device not initialized")
        
        try:
            # Check available methods on the .NET device for debugging
            self._debug(f"[DeviceBridge] .NET device type: {type(self._device)}")
            if self.debug_tiers_enabled[int(self.DEBUG_TIER_VERBOSE)]:
                available_methods = [m for m in dir(self._device) if not m.startswith('_')]
                self._debug(f"[DeviceBridge] Available methods: {available_methods}")
            
            self._device.Start()
            self._debug(f"[DeviceBridge] .NET Start() completed")

            # Wait briefly for graph readiness but do not always block full 100 ms.
            self._wait_for_frame_pointer_ready(max_wait_seconds=0.10)

            # Align decode metadata with negotiated output format/dimensions.
            self._sync_cached_format_from_device()

            if self._event_subscription is not None:
                self._capture_mode = self.CaptureMode.EVENT_DRIVEN
                self._debug("[DeviceBridge] Event-driven mode active")
            else:
                self._capture_mode = self.CaptureMode.NONE
                self._debug("[DeviceBridge] No FrameReady subscription. Frame callbacks unavailable.")
            
        except Exception as e:
            self.debug_print(f"Failed to start camera: {e}", self.DEBUG_TIER_ERROR)
            import traceback
            self.debug_print(traceback.format_exc(), self.DEBUG_TIER_ERROR)
            raise RuntimeError(f"Failed to start camera: {e}")

    def _wait_for_frame_pointer_ready(self, max_wait_seconds: float = 0.10):
        """
        ==========================================
        Poll frame pointer for a short time and return when buffer becomes available.
        ==========================================
        """
        deadline = time.perf_counter() + max(0.0, float(max_wait_seconds))
        while time.perf_counter() < deadline:
            if self.get_frame_pointer() != 0:
                return
            time.sleep(0.005)

    def _sync_cached_format_from_device(self):
        """
        ==========================================
        Refresh cached decode metadata from the actual negotiated device format.
        ==========================================
        """
        actual_format = self.get_actual_camera_format()
        if actual_format is None:
            return

        width, height, fps, pixel_format = actual_format
        self._format = SimpleNamespace(
            width=int(width),
            height=int(height),
            fps=float(fps),
            pixel_format=str(pixel_format),
        )
        self._refresh_cached_format_metadata()
    
    def stop(self):
        """
        ==========================================
        Stop the camera streaming.
        ==========================================
        """
        self._capture_mode = self.CaptureMode.NONE

        # Unsubscribe from event first
        if self._event_subscription and self._device:
            try:
                self._device.FrameReady -= self._event_subscription
                self._debug("FrameReady event unsubscribed")
            except Exception as e:
                self._debug(f"Error unsubscribing from event: {e}")
        
        # Then stop the device
        if self._device:
            try:
                self._device.Stop()
            except Exception as e:
                self.debug_print(f"Error stopping camera: {e}", self.DEBUG_TIER_ERROR)
    
    def get_frame_pointer(self) -> int:
        """
        ==========================================
        Get the pointer to the current frame buffer.
        
        Returns:
            int: Memory address of the frame buffer (IntPtr as Python int)
        ==========================================
        """
        if not self._device:
            return 0
        
        try:
            # GetFramePointer returns System.IntPtr.
            # pythonnet does not always support int(IntPtr), so convert explicitly.
            ptr = self._device.GetFramePointer()

            if ptr is None:
                return 0

            if hasattr(ptr, "ToInt64"):
                return int(ptr.ToInt64())

            if hasattr(ptr, "ToInt32"):
                return int(ptr.ToInt32())

            # Fallback for runtimes where direct int conversion works
            return int(ptr)
        except Exception as e:
            self.debug_print(f"Error getting frame pointer: {e}", self.DEBUG_TIER_ERROR)
            return 0
    
    def get_current_fps(self) -> float:
        """
        ==========================================
        Get the current FPS reported by the .NET camera device.

        Returns:
            float: Current measured FPS or 0.0 on failure.
        ==========================================
        """
        if not self._device:
            return 0.0

        try:
            return float(self._device.GetCurrentFps())
        except Exception as e:
            self.debug_print(f"Error getting current FPS: {e}", self.DEBUG_TIER_ERROR)
            return 0.0

    def get_active_mjpg_decoder_name(self) -> Optional[str]:
        """
        ==========================================
        Report which decoder is currently used for MJPG/MJPEG frames.

        Returns:
            Optional[str]: "TurboJPEG", "OpenCV", "Unavailable", or None when
            current format is not MJPG/MJPEG.
        ==========================================
        """
        pixel_format = self._normalized_pixel_format_name()
        if pixel_format not in ("MJPG", "MJPEG"):
            return None

        if _turbojpeg_decoder is not None:
            return "TurboJPEG"
        if cv2 is not None:
            return "OpenCV"
        return "Unavailable"

    def get_actual_camera_format(self):
        """
        ==========================================
        Get the actual negotiated camera format from the .NET camera device.

        Returns:
            tuple | None: (width, height, fps, pixel_format) or None on failure.
        ==========================================
        """
        if not self._device:
            return None

        if not hasattr(self._device, "GetActualCameraFormat"):
            return None

        try:
            actual = self._device.GetActualCameraFormat()
            if actual is None:
                return None

            width = int(getattr(actual, "Width"))
            height = int(getattr(actual, "Height"))
            fps = float(getattr(actual, "FrameRate"))
            pixel_format = str(getattr(actual, "PixelFormat"))
            return (width, height, fps, pixel_format)
        except Exception as e:
            self.debug_print(f"Error getting actual camera format: {e}", self.DEBUG_TIER_ERROR)
            return None

    def set_property_auto_mode(self, property_name: str, auto_on: bool) -> Tuple[bool, bool]:
        """
        ==========================================
        Toggle auto/manual mode for a camera property.

        Args:
            property_name: Camera property name (e.g. Exposure, Brightness, Focus).
            auto_on: True for Auto mode, False for Manual mode.

        Returns:
            Tuple[bool, bool]: (success, is_auto_enabled)
        ==========================================
        """
        if not self._device:
            return False, False

        try:
            result = self._device.SetPropertyAutoMode(property_name, bool(auto_on))

            # pythonnet often returns (returnValue, outParam) as tuple/list.
            if isinstance(result, (tuple, list)) and len(result) >= 2:
                return bool(result[0]), bool(result[1])

            # Fallback if runtime returns only bool return value.
            success = bool(result)
            if success:
                read_success, is_auto_enabled = self.get_property_auto_mode(property_name)
                if read_success:
                    return True, bool(is_auto_enabled)

            return success, bool(auto_on)
        except Exception as e:
            self.debug_print(f"Error setting auto mode for '{property_name}': {e}", self.DEBUG_TIER_ERROR)
            return False, False

    def get_property_auto_mode(self, property_name: str) -> Tuple[bool, bool]:
        """
        ==========================================
        Get the current auto/manual mode for a camera property.

        Args:
            property_name: Camera property name (e.g. Exposure, Brightness, Focus).

        Returns:
            Tuple[bool, bool]: (success, is_auto_enabled)
        ==========================================
        """
        if not self._device:
            return False, False

        try:
            result = self._device.GetPropertyAutoMode(property_name)

            # pythonnet often returns (returnValue, outParam) as tuple/list.
            if isinstance(result, (tuple, list)) and len(result) >= 2:
                return bool(result[0]), bool(result[1])

            # If only bool is returned, we don't have out value reliably.
            return bool(result), False
        except Exception as e:
            self.debug_print(f"Error getting auto mode for '{property_name}': {e}", self.DEBUG_TIER_ERROR)
            return False, False

    def set_property_value(self, property_name: str, value: int) -> Tuple[bool, int]:
        """
        ==========================================
        Set a numeric value for a camera property.

        Args:
            property_name: Camera property name (e.g. Exposure, Brightness, Focus).
            value: Target numeric value.

        Returns:
            Tuple[bool, int]: (success, actual_value_applied)
        ==========================================
        """
        if not self._device:
            return False, int(value)

        try:
            result = self._device.SetPropertyValue(property_name, int(value))

            # pythonnet often returns (returnValue, outParam) as tuple/list.
            if isinstance(result, (tuple, list)) and len(result) >= 2:
                return bool(result[0]), int(result[1])

            # Fallback if runtime returns only bool return value.
            return bool(result), int(value)
        except Exception as e:
            self.debug_print(f"Error setting value for '{property_name}': {e}", self.DEBUG_TIER_ERROR)
            return False, int(value)

    def set_property_values(self, properties: list[Tuple[str, int]]) -> Tuple[bool, list[Tuple[str, bool, int]]]:
        """
        ==========================================
        Set multiple numeric camera properties and return per-property results.

        Returns:
            Tuple[bool, list[Tuple[str, bool, int]]]:
                (all_success, [(property_name, success, actual_value), ...])
        ==========================================
        """
        if not self._device or not properties:
            return False, []

        try:
            system_module = __import__("System")
            tuple_type = system_module.ValueTuple[system_module.String, system_module.Int32]
            values_array = system_module.Array[tuple_type]([
                tuple_type(str(property_name), int(value))
                for property_name, value in properties
            ])

            result = self._device.SetPropertyValues(values_array)

            if isinstance(result, (tuple, list)) and len(result) >= 2:
                out_results = result[1]
                parsed_by_name: dict[str, list[Tuple[str, bool, int]]] = {}
                if out_results is not None:
                    for item in out_results:
                        try:
                            parsed_name = str(item.Item1)
                            parsed_tuple = (parsed_name, bool(item.Item2), int(item.Item3))
                            parsed_key = parsed_name.lower()
                            parsed_by_name.setdefault(parsed_key, []).append(parsed_tuple)
                        except Exception as parse_error:
                            self._debug(f"[SetPropertyValues] Failed to parse one batch result item: {parse_error}")

                normalized_results = []
                for requested_name, requested_value in properties:
                    request_key = str(requested_name).lower()
                    parsed_list = parsed_by_name.get(request_key)
                    if parsed_list:
                        normalized_results.append(parsed_list.pop(0))
                    else:
                        self._debug(
                            f"[SetPropertyValues] Missing/invalid result for '{requested_name}'. "
                            "Marking as failed in normalized output."
                        )
                        normalized_results.append((str(requested_name), False, int(requested_value)))

                all_success = bool(result[0]) and all(
                    bool(success) for _name, success, _value in normalized_results
                ) and len(normalized_results) > 0

                return all_success, normalized_results

            # Fallback if runtime returns only bool return value.
            all_success = bool(result)
            fallback_results = [(str(name), all_success, int(value)) for name, value in properties]
            return all_success, fallback_results
        except Exception as e:
            self._debug(f"[SetPropertyValues] Batch call failed, falling back to per-property set: {e}")
            fallback_results = []
            for property_name, value in properties:
                success, actual_value = self.set_property_value(property_name, value)
                fallback_results.append((str(property_name), bool(success), int(actual_value)))
            all_success = all(bool(success) for _name, success, _value in fallback_results) and len(fallback_results) > 0
            return all_success, fallback_results

    def get_property_values(self, property_names: list[str]) -> Tuple[bool, list[Tuple[str, bool, int]]]:
        """
        ==========================================
        Get multiple numeric camera property values and return per-property results.

        Args:
            property_names: list of camera property names.

        Returns:
            Tuple[bool, list[Tuple[str, bool, int]]]:
                (all_success, [(property_name, success, current_value), ...])
        ==========================================
        """
        if not self._device or not property_names:
            return False, []

        if not hasattr(self._device, "GetPropertyValues"):
            return False, []

        try:
            system_module = __import__("System")
            names_array = system_module.Array[system_module.String]([str(name) for name in property_names])
            result = self._device.GetPropertyValues(names_array)

            parsed_by_name: dict[str, list[Tuple[str, bool, int]]] = {}
            all_success = False

            if isinstance(result, (tuple, list)) and len(result) >= 2:
                all_success = bool(result[0])
                out_values = result[1]

                if out_values is not None:
                    for item in out_values:
                        try:
                            parsed_name = str(item.Item1)
                            parsed_tuple = (parsed_name, bool(item.Item2), int(item.Item3))
                            parsed_key = parsed_name.lower()
                            parsed_by_name.setdefault(parsed_key, []).append(parsed_tuple)
                        except Exception as parse_error:
                            self._debug(f"[GetPropertyValues] Failed to parse one batch result item: {parse_error}")

            normalized_results = []
            for requested_name in property_names:
                request_key = str(requested_name).lower()
                parsed_list = parsed_by_name.get(request_key)
                if parsed_list:
                    normalized_results.append(parsed_list.pop(0))
                else:
                    normalized_results.append((str(requested_name), False, 0))

            if len(normalized_results) > 0:
                all_success = bool(all_success) and all(bool(success) for _name, success, _value in normalized_results)

            return all_success, normalized_results
        except Exception as e:
            self.debug_print(f"Error getting property values: {e}", self.DEBUG_TIER_ERROR)
            return False, []

    def reset_all_properties_to_default_values(self) -> Tuple[bool, list[Tuple[str, bool, int]]]:
        """
        ==========================================
        Reset all supported capabilities to numeric defaults and return per-property results.

        Returns:
            Tuple[bool, list[Tuple[str, bool, int]]]:
                (all_success, [(property_name, success, actual_value), ...])
        ==========================================
        """
        if not self._device:
            return False, []

        try:
            capabilities = self._device.GetCachedControlCapabilities()
        except Exception as e:
            self.debug_print(f"Error getting cached capabilities: {e}", self.DEBUG_TIER_ERROR)
            return False, []

        if capabilities is None:
            return False, []

        seen_property_names = set()
        default_properties = []

        for capability in capabilities:
            try:
                if not bool(capability.PropertySupported):
                    continue

                property_name = str(capability.PropertyName)
                if not property_name:
                    continue

                property_key = property_name.lower()
                if property_key in seen_property_names:
                    continue
                seen_property_names.add(property_key)

                default_value = int(capability.Default)
                default_properties.append((property_name, default_value))
            except Exception as e:
                self._debug(f"[ResetDefaults] Error while processing capability: {e}")

        if not default_properties:
            return False, []

        return self.set_property_values(default_properties)

    def reset_all_property_flags(self) -> Tuple[bool, list[Tuple[str, bool, bool]]]:
        """
        ==========================================
        Reset all auto/manual property flags to Auto mode and return per-property results.

        Returns:
            Tuple[bool, list[Tuple[str, bool, bool]]]:
                (all_success, [(property_name, success, is_auto_enabled), ...])
        ==========================================
        """
        if not self._device:
            return False, []

        try:
            capabilities = self._device.GetCachedControlCapabilities()
        except Exception as e:
            self.debug_print(f"Error getting cached capabilities: {e}", self.DEBUG_TIER_ERROR)
            return False, []

        if capabilities is None:
            return False, []

        seen_property_names = set()
        flag_results = []

        for capability in capabilities:
            try:
                if not bool(capability.PropertySupported):
                    continue
                if not bool(capability.AutoSupported):
                    continue

                property_name = str(capability.PropertyName)
                if not property_name:
                    continue

                property_key = property_name.lower()
                if property_key in seen_property_names:
                    continue
                seen_property_names.add(property_key)

                success, is_auto_enabled = self.set_property_auto_mode(property_name, True)
                flag_results.append((property_name, bool(success), bool(is_auto_enabled)))
                if not success:
                    self._debug(f"[ResetFlags] Failed to set '{property_name}' to Auto")
            except Exception as e:
                self._debug(f"[ResetFlags] Error while processing capability: {e}")

        all_success = all(bool(success) for _name, success, _is_auto in flag_results) and len(flag_results) > 0
        return all_success, flag_results
    
    def _on_frame_ready_event(self, frame_count, buffer_len=None):
        """
        ==========================================
        Event handler fired by DirectShow when FrameReady event occurs.
        
        Args:
            frame_count: Current frame count from the .NET event
            buffer_len: Optional frame buffer length from the .NET event
        ==========================================
        """
        if self._capture_mode != self.CaptureMode.EVENT_DRIVEN:
            return
        
        try:
            if self.debug_tiers_enabled[int(self.DEBUG_TIER_VERBOSE)]:
                event_time = time.strftime("%Y-%m-%d %H:%M:%S") + f".{int((time.time() % 1) * 1000):03d}"
                if buffer_len is None:
                    self.debug_print(f"[{event_time}] [Event] Frame {frame_count} arrived", self.DEBUG_TIER_VERBOSE)
                else:
                    self.debug_print(
                        f"[{event_time}] [Event] Frame {frame_count} arrived (buffer_len={int(buffer_len)})",
                        self.DEBUG_TIER_VERBOSE
                    )
            self._process_frame(frame_count, buffer_len)
        except Exception as e:
            self.debug_print(f"Error in frame ready event handler: {e}", self.DEBUG_TIER_ERROR)
    
    def _process_frame(self, frame_count, buffer_len=None):
        """
        ==========================================
        Process a new frame by reading it from memory.
        
        Args:
            frame_count: The current frame count
            buffer_len: Optional frame buffer size from event callback
        ==========================================
        """
        try:
            pixel_format = self._pixel_format_name
            # MJPG path: decode compressed JPEG payload using the exact buffer length
            # reported by the frame callback.
            if pixel_format in ("MJPG", "MJPEG"):
                if _turbojpeg_decoder is None and cv2 is None:
                    if not self._warned_mjpg_decoder_unavailable:
                        self.debug_print(
                            "MJPG decode requires turbojpeg or OpenCV (cv2), but neither is available. Frame skipped.",
                            self.DEBUG_TIER_ERROR
                        )
                        self._warned_mjpg_decoder_unavailable = True
                    return

                if buffer_len is None or int(buffer_len) <= 0:
                    self.debug_print(
                        f"MJPG frame #{frame_count} missing valid buffer_len; frame skipped.",
                        self.DEBUG_TIER_ERROR
                    )
                    return

                ptr = self.get_frame_pointer()
                self._debug(f"[Process] MJPG frame #{frame_count}, pointer: 0x{ptr:X}, buffer_len={int(buffer_len)}")
                if ptr == 0:
                    self._debug("[Process] ERROR: Null pointer!")
                    return

                encoded_length = int(buffer_len)
                encoded_buffer = (ctypes.c_ubyte * encoded_length).from_address(ptr)

                decoded_frame = None
                if _turbojpeg_decoder is not None:
                    # TurboJPEG is faster than cv2.imdecode (SIMD-accelerated libjpeg-turbo).
                    # decode() returns a new BGR array — no extra copy needed.
                    try:
                        self._debug(f"[Process] TurboJPEG decode")
                        decoded_frame = _turbojpeg_decoder.decode(bytes(encoded_buffer))
                    except Exception as tj_err:
                        self.debug_print(f"[Process] TurboJPEG decode failed, falling back to cv2: {tj_err}", self.DEBUG_TIER_ERROR)

                if decoded_frame is None and cv2 is not None:
                    encoded_array = np.ctypeslib.as_array(encoded_buffer)
                    decoded_frame = cv2.imdecode(encoded_array, cv2.IMREAD_COLOR)

                if decoded_frame is None:
                    self.debug_print(
                        f"Failed to decode MJPG frame #{frame_count} (buffer_len={encoded_length}).",
                        self.DEBUG_TIER_ERROR
                    )
                    return

                with self._frame_state_lock:
                    self._current_frame = decoded_frame
                    callback = self._frame_callback

                if callback:
                    callback(frame_count, decoded_frame)
                return

            # YUY2/YUYV path: decode packed YUV422 to BGR.
            if pixel_format in ("YUY2", "YUYV", "YUNV"):
                if cv2 is None:
                    self.debug_print(
                        "YUY2 decode requires OpenCV (cv2), but cv2 is not available. Frame skipped.",
                        self.DEBUG_TIER_ERROR
                    )
                    return

                ptr = self.get_frame_pointer()
                self._debug(f"[Process] {pixel_format} frame #{frame_count}, pointer: 0x{ptr:X}")
                if ptr == 0:
                    self._debug("[Process] ERROR: Null pointer!")
                    return

                expected_size = int(self._yuy2_buffer_size)
                source_size = expected_size
                if buffer_len is not None:
                    source_size = min(int(buffer_len), expected_size)

                if source_size < expected_size:
                    self.debug_print(
                        f"{pixel_format} frame #{frame_count} buffer too small "
                        f"({source_size} < {expected_size}); frame skipped.",
                        self.DEBUG_TIER_ERROR
                    )
                    return

                raw_buffer = (ctypes.c_ubyte * expected_size).from_address(ptr)
                yuy2_array = np.frombuffer(raw_buffer, dtype=np.uint8)

                try:
                    yuy2_array = yuy2_array.reshape(self._shape_yuy2)
                    bgr_frame = cv2.cvtColor(yuy2_array, cv2.COLOR_YUV2BGR_YUY2)
                except Exception as e:
                    self.debug_print(
                        f"Failed to decode {pixel_format} frame #{frame_count}: {e}",
                        self.DEBUG_TIER_ERROR
                    )
                    return

                # YUY2 decode path is already top-down on this device; keep orientation as-is.
                stable_frame = bgr_frame.copy()
                with self._frame_state_lock:
                    self._current_frame = stable_frame
                    callback = self._frame_callback

                if callback:
                    callback(frame_count, stable_frame)
                return

            bytes_per_pixel = self._bytes_per_pixel
            if bytes_per_pixel == 0:
                if not self._warned_unsupported_pixel_format:
                    pixel_format = pixel_format or "UNKNOWN"
                    self.debug_print(
                        "Unsupported pixel format for raw-pointer frame decode: "
                        f"'{pixel_format}'. Expected uncompressed RGB/BGR/GRAY format. "
                        "Frame skipped to avoid unsafe memory access.",
                        self.DEBUG_TIER_ERROR
                    )
                    self._warned_unsupported_pixel_format = True
                return

            # Get the pointer to the frame buffer
            ptr = self.get_frame_pointer()
            self._debug(f"[Process] Frame #{frame_count}, pointer: 0x{ptr:X}")
            if ptr == 0:
                self._debug(f"[Process] ERROR: Null pointer!")
                return
            
            # Calculate expected buffer size for current uncompressed pixel format.
            buffer_size = int(self._raw_buffer_size)

            if buffer_len is not None and int(buffer_len) < buffer_size:
                self.debug_print(
                    f"{pixel_format} frame #{frame_count} buffer too small "
                    f"({int(buffer_len)} < {buffer_size}); frame skipped.",
                    self.DEBUG_TIER_ERROR
                )
                return
            
            # Read raw bytes from memory using ctypes
            frame_buffer = (ctypes.c_ubyte * buffer_size).from_address(ptr)
            
            # Convert to numpy array and reshape
            frame_array = np.frombuffer(frame_buffer, dtype=np.uint8)
            if bytes_per_pixel == 1:
                frame_array = frame_array.reshape(self._shape_gray)
                frame_array = np.repeat(frame_array, 3, axis=2)
            elif bytes_per_pixel == 3:
                frame_array = frame_array.reshape(self._shape_rgb)
            else:
                frame_array = frame_array.reshape(self._shape_raw)
                frame_array = frame_array[:, :, :3]
            
            # DirectShow typically gives bottom-up images, flip vertically.
            # We copy after flip because frame_array points to unmanaged camera memory
            # that can be overwritten by the next frame. Keeping a view here risks
            # tearing/corrupted reads in callbacks or get_latest_frame().
            # Tradeoff: extra memory bandwidth per frame for data safety/stability.
            stable_frame = np.flipud(frame_array).copy()
            with self._frame_state_lock:
                self._current_frame = stable_frame
                callback = self._frame_callback
            self._debug(f"[Process] Frame ready: {self._current_frame.shape}, dtype={self._current_frame.dtype}")
            
            # Call user callback if set
            if callback:
                self._debug(f"[Process] Calling callback...")
                callback(frame_count, stable_frame)
            else:
                self._debug(f"[Process] WARNING: No callback registered!")
                
        except Exception as e:
            self.debug_print(f"Error processing frame: {e}", self.DEBUG_TIER_ERROR)
    
    def get_latest_frame(self):
        """
        ==========================================
        Get the most recent frame captured by the camera.
        
        Returns:
            numpy.ndarray: The latest frame as a numpy array (height, width, 3) or None
        ==========================================
        """
        with self._frame_state_lock:
            return self._current_frame
    
    def set_frame_callback(self, callback: Callable[[int, np.ndarray], None]):
        """
        ==========================================
        Set a callback to be called when a new frame is ready.
        
        Args:
            callback: Function to call with (frame_count: int, frame: np.ndarray)
        ==========================================
        """
        with self._frame_state_lock:
            self._frame_callback = callback
        self._debug(f"[DeviceBridge] Frame callback registered: {callback.__name__ if hasattr(callback, '__name__') else 'anonymous'}")
    
    def dispose(self):
        """
        ==========================================
        Dispose of the camera device and release resources.
        ==========================================
        """
        if self._device:
            try:
                self.stop()
            except Exception as e:
                self._debug(f"Error stopping camera during dispose: {e}")

            try:
                self._device.Dispose()
            except Exception as e:
                self.debug_print(f"Error disposing camera device: {e}", self.DEBUG_TIER_ERROR)
            finally:
                self._device = None
                self._event_subscription = None
                self._capture_mode = self.CaptureMode.NONE
                with self._frame_state_lock:
                    self._current_frame = None
                    self._frame_callback = None
    
    def __del__(self):
        """
        ==========================================
        Destructor to ensure cleanup.
        ==========================================
        """
        self.dispose()
    
    def __enter__(self):
        """
        ==========================================
        Context manager entry.
        ==========================================
        """
        return self
    
    def __exit__(self, exc_type, exc_val, exc_tb):
        """
        ==========================================
        Context manager exit.
        ==========================================
        """
        self.dispose()

    def _debug(self, message: str):
        """
        ==========================================
        Print debug messages only when debug logging is enabled.
        =====================
        """
        self.debug_print(message, self.DEBUG_TIER_VERBOSE)