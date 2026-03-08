from .camera_device_bridge import CameraDeviceBridge
from typing import NamedTuple, List, Optional, Dict, Sequence, Union
import threading
from enum import Enum

DebugLoggingConfig = Union[bool, int, Sequence[bool]]


class DotNetLogType(str, Enum):
    Error = "Error"
    Warning = "Warning"
    Info = "Info"
    Debug = "Debug"

class CameraFormat(NamedTuple):
    width: int
    height: int
    fps: float
    pixel_format: str

class CameraRange(NamedTuple):
    min: float
    max: float
    step: float
    default: float
    current: float
    property_supported: bool
    is_auto: bool
    auto_supported: bool

class CameraDeviceInfo(NamedTuple):
    name: str
    path: str
    formats: Optional[List[CameraFormat]]
    ranges: Optional[Dict[str, CameraRange]]    


class Camera:
    """
    Encapsulates camera operations including opening, capturing frames,
    and adjusting camera properties (exposure, contrast, brightness, etc.).
    """
    
    # The camera bridge is shared across all instances of Camera, and is initialized only once.
    # This ensures that the .NET DLL is loaded only once, improving performance and avoiding redundant loads.
    # The camera bridge provides access to the .NET functions for camera discovery and property retrieval.
    _camera_bridge = None

    # Debug tiers:
    # 1 = verbose/everything
    # 2 = errors only
    DEBUG_TIER_VERBOSE = 1
    DEBUG_TIER_ERROR = 2
    DotNetLogType = DotNetLogType

    def __init__(self, debug_logging: DebugLoggingConfig = False):
        """
        ==========================================
        Initialize camera object (not yet connected).
        ==========================================
        """
        self.device_bridge = None
        self.is_open = False
        self.frame_callback = None
        self.property_ranges = {}
        self._property_key_index = {}
        self.available_formats = []
        self._ranges_cache = {}
        self._formats_cache = {}
        self._property_cache_lock = threading.Lock()
        self.device_path = None
        self.current_format = None
        self._request_rgb24_conversion = False
        self._dotnet_log_file_path = None
        self._dotnet_log_limits = None
        self._dotnet_log_levels = None

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

        self._dotnet_log_levels = self._build_dotnet_log_settings_from_tiers()

        # Initialize the camera bridge ONLY once if it's not already there
        Camera._ensure_bridge()

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

    def _apply_dotnet_logging_configuration(self):
        """
        ==========================================
        Apply cached logging configuration to the active CameraDeviceBridge.
        ==========================================
        """
        if self.device_bridge is None:
            return

        effective_log_levels = self._dotnet_log_levels
        if effective_log_levels is None:
            effective_log_levels = self._build_dotnet_log_settings_from_tiers()

        try:
            self.device_bridge.set_dotnet_log_levels(effective_log_levels)
        except Exception as e:
            self.debug_print(f"Failed to apply .NET log levels: {e}", self.DEBUG_TIER_ERROR)

        if self._dotnet_log_file_path is not None:
            try:
                self.device_bridge.set_dotnet_log_file_location(self._dotnet_log_file_path)
            except Exception as e:
                self.debug_print(f"Failed to apply .NET log file path: {e}", self.DEBUG_TIER_ERROR)

        if self._dotnet_log_limits is not None:
            try:
                self.device_bridge.set_dotnet_log_limits(**self._dotnet_log_limits)
            except Exception as e:
                self.debug_print(f"Failed to apply .NET log limits: {e}", self.DEBUG_TIER_ERROR)

    def set_dotnet_log_levels(self, log_levels):
        """
        ==========================================
        Set .NET CameraDevice log levels.

        Args:
            log_levels: list[tuple[str, bool]] where names are Error/Warning/Info/Debug

        Returns:
            bool: True if accepted/applied, False otherwise.
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

        self._dotnet_log_levels = normalized_levels

        if self.device_bridge is None:
            return True

        try:
            return bool(self.device_bridge.set_dotnet_log_levels(self._dotnet_log_levels))
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
        if self.device_bridge is not None:
            try:
                return self.device_bridge.get_dotnet_log_levels()
            except Exception as e:
                self.debug_print(f"Failed to get .NET log levels: {e}", self.DEBUG_TIER_ERROR)

        levels = self._dotnet_log_levels or self._build_dotnet_log_settings_from_tiers()
        return {str(name): bool(enabled) for name, enabled in levels}

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
            tuple or None: Result from .NET SetLogLimits when bridge is open.
        ==========================================
        """
        self._dotnet_log_limits = {
            "max_log_size_bytes": max_log_size_bytes,
            "max_log_age_milliseconds": max_log_age_milliseconds,
            "target_log_age_milliseconds": target_log_age_milliseconds,
            "limit_log_size": limit_log_size,
            "limit_log_time": limit_log_time,
        }

        if self.device_bridge is None:
            return None

        try:
            return self.device_bridge.set_dotnet_log_limits(
                max_log_size_bytes=max_log_size_bytes,
                max_log_age_milliseconds=max_log_age_milliseconds,
                target_log_age_milliseconds=target_log_age_milliseconds,
                limit_log_size=limit_log_size,
                limit_log_time=limit_log_time,
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
        if self.device_bridge is not None:
            try:
                return self.device_bridge.get_dotnet_log_limits()
            except Exception as e:
                self.debug_print(f"Failed to get .NET log limits: {e}", self.DEBUG_TIER_ERROR)

        return dict(self._dotnet_log_limits) if isinstance(self._dotnet_log_limits, dict) else {}

    def set_dotnet_log_file_location(self, log_file_path: str):
        """
        ==========================================
        Set .NET CameraDevice log file location.

        Returns:
            bool: True if accepted/applied, False otherwise.
        ==========================================
        """
        self._dotnet_log_file_path = str(log_file_path)

        if self.device_bridge is None:
            return True

        try:
            return bool(self.device_bridge.set_dotnet_log_file_location(self._dotnet_log_file_path))
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
        if self.device_bridge is None:
            return False

        try:
            return bool(self.device_bridge.clean_dotnet_log())
        except Exception as e:
            self.debug_print(f"Failed to clean .NET log: {e}", self.DEBUG_TIER_ERROR)
            return False

    def configure_bridge_logging(self, debug_tiers_enabled=None, log_file_path=None, log_limits=None):
        """
        ==========================================
        Configure camera and .NET bridge logging in one call.

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
            self._dotnet_log_levels = self._build_dotnet_log_settings_from_tiers()

            # Keep Python-side bridge debug behavior in sync while open.
            if self.device_bridge is not None:
                try:
                    self.device_bridge.debug_tiers_enabled = self.debug_tiers_enabled.copy()
                except Exception as e:
                    self.debug_print(f"Failed to sync debug tiers to bridge: {e}", self.DEBUG_TIER_ERROR)

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
    
    # !!! ============ {WRAPPER METHODS FOR CAMERA INSPECTOR - START} ============ !!!
    # Why wrap the camera inspector methods? This allows us the centralize camera handling
    # into a single class, now we can use one class from the main, or from the GUI, and 
    # it will handle both the .NET bridge calls and the OpenCV capture.
    # This will make the code cleaner.
    @staticmethod
    def get_camera_ranges(device_path):
        """
        ==========================================
        Provides the user with the ranges for the various camera parameters, 
        such as exposure, contrast, brightness, etc.
        For example some cameras may have exposure between 0 to 255 with a step of 1, 
        while others may have a different range and step.

        Returns a dict mapping property names to CameraRange NamedTuples.
        ==========================================
        """
        Camera._ensure_bridge()
        raw_ranges = Camera._camera_bridge.get_camera_ranges(device_path) or []
        
        # Consistent with get_connected_cameras
        return {
            str(raw_range.PropertyName): CameraRange(
                min=float(raw_range.Min),
                max=float(raw_range.Max),
                step=float(raw_range.Step),
                default=float(raw_range.Default),
                current=float(raw_range.CurrentValue),
                property_supported=bool(raw_range.PropertySupported),
                is_auto=bool(raw_range.IsAuto),
                auto_supported=bool(raw_range.AutoSupported)
            ) for raw_range in raw_ranges
        }

    @staticmethod
    def get_camera_formats(device_path):
        """
        ==========================================
        Different cameras support different stream formats
        (combinations of resolution, frame rate, and pixel format).
        Get the list of unique camera formats for a specific device.
        ==========================================
        """
        Camera._ensure_bridge()
        raw_formats = Camera._camera_bridge.get_camera_formats(device_path) or []
        
        python_formats = []
        seen_signatures = set() 

        for raw_f in raw_formats:
            # Clean up and standardize the data
            width = int(raw_f.Width)
            height = int(raw_f.Height)
            raw_fps = float(raw_f.FrameRate)
            pixel_format = str(raw_f.PixelFormat)

            # "Snap" to common integers if they are extremely close (Epsilon check)
            # This turns 30.00003000003 into 30.0, but keeps 29.97 as 29.97
            if abs(raw_fps - round(raw_fps)) < 0.001:
                fps = float(round(raw_fps))
            else:
                fps = round(raw_fps, 2)

            # Create a unique 'signature' for this format
            # If a format has the same W, H, FPS, and PixelFormat, it's a duplicate
            signature = (width, height, fps, pixel_format)

            if signature not in seen_signatures:
                format = CameraFormat(
                    width=width,
                    height=height,
                    fps=fps,
                    pixel_format=pixel_format
                )
                python_formats.append(format)
                seen_signatures.add(signature)

        # Sort by Resolution (Width * Height) descending, then by FPS
        # This ensures the 'best' formats are at the top of your dropdown
        python_formats.sort(key=lambda x: (x.width * x.height, x.fps), reverse=True)

        return python_formats

    @staticmethod
    def _ensure_bridge():
        """
        ==========================================
        Private helper to ensure the bridge exists.
        ==========================================
        """
        if Camera._camera_bridge is None:
            # We import here to avoid circular imports if necessary
            from .camera_inspector_bridge import CameraInspectorBridge 
            Camera._camera_bridge = CameraInspectorBridge()

    @staticmethod
    def get_connected_cameras(get_formats=False, get_ranges=False):
        Camera._ensure_bridge()
        uvc_devices_raw = Camera._camera_bridge.get_connected_cameras() or []
        uvc_devices_python_style = []

        for device in uvc_devices_raw:
            device_path = str(device.DevicePath)
            
            # 1. Fetch the data using our existing methods
            formats = Camera.get_camera_formats(device_path) if get_formats else None
            ranges = Camera.get_camera_ranges(device_path) if get_ranges else None

            # 2. Wrap it in a NamedTuple instead of a dictionary
            # This is where the magic happens
            cam_info = CameraDeviceInfo(
                name=str(device.Name),
                path=device_path,
                formats=formats,
                ranges=ranges
            )

            uvc_devices_python_style.append(cam_info)
        
        return uvc_devices_python_style
    # !!! ============ {WRAPPER METHODS FOR CAMERA INSPECTOR - END} ============ !!!

    def open(self, device_path: str, camera_format: CameraFormat, request_rgb24_conversion: bool = False):
        """
        ==========================================
        Opens a camera using DirectShow.
        
        Args:
            device_path (str): The device path from CameraDeviceInfo
            camera_format (CameraFormat): The desired format (width, height, fps, pixel_format)
            request_rgb24_conversion (bool): Request .NET SampleGrabber RGB24 conversion.
        
        Returns:
            bool: True if successful, False otherwise.
        ==========================================
        """
        try:
            # Cache device capabilities on first open, then reuse.
            with self._property_cache_lock:
                if device_path not in self._ranges_cache:
                    self._ranges_cache[device_path] = Camera.get_camera_ranges(device_path)
                if device_path not in self._formats_cache:
                    self._formats_cache[device_path] = Camera.get_camera_formats(device_path)

            # Keep current device capabilities easily accessible.
            with self._property_cache_lock:
                self.property_ranges = self._ranges_cache.get(device_path, {})
                self.available_formats = self._formats_cache.get(device_path, [])
                self._rebuild_property_key_index()

            self._request_rgb24_conversion = bool(request_rgb24_conversion)

            # Create the DirectShow camera device bridge
            self.device_bridge = CameraDeviceBridge(
                device_path,
                camera_format,
                debug_logging=self.debug_tiers_enabled.copy(),
                request_rgb24_conversion=self._request_rgb24_conversion
            )

            # Apply any cached .NET logging settings now that the bridge exists.
            self._apply_dotnet_logging_configuration()

            # Set up frame callback if user has registered one
            if self.frame_callback:
                self.device_bridge.set_frame_callback(self._on_frame_ready)

            # Start the camera
            self.device_bridge.start()

            self.is_open = True
            self.device_path = device_path

            negotiated_format = None
            try:
                negotiated_format = self.device_bridge.get_actual_camera_format()
            except Exception as e:
                self.debug_print(f"Failed to query actual camera format: {e}", self.DEBUG_TIER_ERROR)

            if negotiated_format is not None:
                width, height, fps, pixel_format = negotiated_format
                self.current_format = CameraFormat(
                    width=int(width),
                    height=int(height),
                    fps=float(fps),
                    pixel_format=str(pixel_format)
                )
                self.debug_print(
                    "Negotiated camera format: "
                    f"{self.current_format.width}x{self.current_format.height} @ "
                    f"{self.current_format.fps:.2f} FPS ({self.current_format.pixel_format})",
                    self.DEBUG_TIER_VERBOSE
                )
            else:
                self.current_format = camera_format

            try:
                property_names = [
                    str(property_name)
                    for property_name, property_range in self.property_ranges.items()
                    if bool(property_range.property_supported)
                ]

                if len(property_names) > 0:
                    _all_success, property_results = self.device_bridge.get_property_values(property_names)
                    for property_name, success, actual_value in property_results:
                        if bool(success):
                            self._update_cached_property_value(property_name, int(actual_value))

                    self.debug_print(
                        f"Read back {sum(1 for _n, s, _v in property_results if bool(s))}/{len(property_results)} "
                        "property values after open.",
                        self.DEBUG_TIER_VERBOSE
                    )
            except Exception as e:
                self.debug_print(f"Failed to query property values after open: {e}", self.DEBUG_TIER_ERROR)
            return True
        except Exception as e:
            self.debug_print(f"Failed to open camera: {e}", self.DEBUG_TIER_ERROR)

            # Defensive cleanup if bridge creation/start partially succeeded.
            if self.device_bridge is not None:
                try:
                    self.device_bridge.stop()
                except Exception:
                    pass
                try:
                    self.device_bridge.dispose()
                except Exception as dispose_error:
                    self.debug_print(f"Failed to dispose bridge after open failure: {dispose_error}", self.DEBUG_TIER_ERROR)
                finally:
                    self.device_bridge = None

            self.is_open = False
            with self._property_cache_lock:
                self.property_ranges = {}
                self._property_key_index = {}
                self.available_formats = []
            self.device_path = None
            self.current_format = None
            return False
    
    def close(self):
        """
        ==========================================
        Closes the camera connection.
        ==========================================
        """
        if self.device_bridge is not None:
            try:
                self.device_bridge.stop()
                self.device_bridge.dispose()
            except Exception as e:
                self.debug_print(f"Error closing camera: {e}", self.DEBUG_TIER_ERROR)

        self.is_open = False
        self.device_bridge = None
        with self._property_cache_lock:
            self.property_ranges = {}
            self._property_key_index = {}
            self.available_formats = []
        self.device_path = None
        self.current_format = None

    def set_format(self, camera_format: CameraFormat, request_rgb24_conversion: Optional[bool] = None):
        """
        ==========================================
        Changes the camera stream format by reopening the current device.
        It is important to point out that DirectShow does not support dynamic format changes on the fly, 
        so we have to close and reopen the camera with the new format.
        I could have implemented similar method inside of the .net wrapper,
        but it would literally just call the same open and close methods, just one layer closer to
        DirectShow, so I decided to keep it here in the main camera class.

        Args:
            camera_format (CameraFormat): Target format to apply.
            request_rgb24_conversion (Optional[bool]): Override RGB24 conversion request for reopen.

        Returns:
            bool: True if format change succeeded, False otherwise.
        ==========================================
        """
        if camera_format is None:
            return False

        current_rgb24_request = bool(self._request_rgb24_conversion)
        target_rgb24_request = (
            current_rgb24_request
            if request_rgb24_conversion is None
            else bool(request_rgb24_conversion)
        )
        format_unchanged = (self.current_format == camera_format)
        rgb24_unchanged = (target_rgb24_request == current_rgb24_request)

        # Nothing to change in either camera format or RGB24 conversion behavior.
        if format_unchanged and rgb24_unchanged:
            return True

        if self.device_path is None:
            # No active/known camera target yet; cannot apply format.
            return False

        # If camera is currently closed, just remember the desired format.
        if not self.is_open:
            self.current_format = camera_format
            self._request_rgb24_conversion = target_rgb24_request
            return True

        # Reopen the same device with the new format.
        current_device_path = self.device_path
        previous_format = self.current_format
        previous_rgb24_request = bool(self._request_rgb24_conversion)
        self.close()
        format_changed = self.open(
            current_device_path,
            camera_format,
            request_rgb24_conversion=target_rgb24_request
        )
        if format_changed:
            return True

        if previous_format is not None:
            rollback_success = self.open(
                current_device_path,
                previous_format,
                request_rgb24_conversion=previous_rgb24_request
            )
            if not rollback_success:
                self.debug_print(
                    "Failed to change format and failed to restore previous format.",
                    self.DEBUG_TIER_ERROR
                )

        return False
    
    def get_frame(self):
        """
        ==========================================
        Gets the latest frame from the camera.
        
        Returns:
            tuple: (success: bool, frame: np.ndarray or None)
        ==========================================
        """
        if not self.is_open or self.device_bridge is None:
            return False, None

        frame = self.device_bridge.get_latest_frame()
        return frame is not None, frame

    def get_current_fps(self):
        """
        ==========================================
        Get FPS reported by the active .NET camera device.

        Returns:
            float: Current FPS measurement or 0.0 if unavailable.
        ==========================================
        """
        if not self.is_open or self.device_bridge is None:
            return 0.0

        return float(self.device_bridge.get_current_fps())

    def _on_frame_ready(self, frame_count, frame):
        """
        ==========================================
        Internal callback when a new frame is ready from DirectShow.
        Pass frame directly without conversion (DirectShow outputs BGR already).
        ==========================================
        """
        self.debug_print(f"[Camera] _on_frame_ready called, frame_count={frame_count}", self.DEBUG_TIER_VERBOSE)
        if frame is None:
            self.debug_print("[Camera] ERROR: Frame is None!", self.DEBUG_TIER_ERROR)
            return
        
        # DirectShow outputs BGR directly - no conversion needed
        self.debug_print("[Camera] Passing frame to GUI callback...", self.DEBUG_TIER_VERBOSE)
        
        # Call user callback with frame as-is
        if self.frame_callback is not None:
            self.frame_callback(True, frame)
            self.debug_print("[Camera] GUI callback completed", self.DEBUG_TIER_VERBOSE)
    
    def set_property_auto_mode(self, property_name: str, auto_on: bool):
        """
        ==========================================
        Toggle auto/manual mode for a specific camera property.

        Args:
            property_name (str): Property name (e.g. Exposure, Brightness, Focus).
            auto_on (bool): True to enable auto mode, False for manual mode.

        Returns:
            tuple: (success: bool, is_auto_enabled: bool)
        ==========================================
        """
        if not self.is_open or self.device_bridge is None:
            return False, False

        success, is_auto_enabled = self.device_bridge.set_property_auto_mode(property_name, auto_on)

        if success:
            self._update_cached_property_auto_mode(property_name, bool(is_auto_enabled))

        return success, is_auto_enabled

    def set_property_value(self, property_name: str, value: int):
        """
        ==========================================
        Set a numeric property value on the active camera.

        Args:
            property_name (str): Property name (e.g. Exposure, Brightness, Focus).
            value (int): Desired value.

        Returns:
            tuple: (success: bool, actual_value: int)
        ==========================================
        """
        if not self.is_open or self.device_bridge is None:
            return False, int(value)

        success, actual_value = self.device_bridge.set_property_value(property_name, int(value))

        if success:
            self._update_cached_property_value(property_name, actual_value)

        return success, int(actual_value)

    def set_property_values(self, properties: List[tuple]):
        """
        ==========================================
        Set multiple numeric property values in one batch operation.

        Args:
            properties (list): List of (property_name: str, value: int)

        Returns:
            tuple: (all_success: bool, results: list[(property_name, success, actual_value)])
        ==========================================
        """
        if not self.is_open or self.device_bridge is None:
            return False, []

        all_success, results = self.device_bridge.set_property_values(properties)

        for property_name, success, actual_value in results:
            if not bool(success):
                continue
            self._update_cached_property_value(property_name, actual_value)

        return all_success, results

    def reset_all_properties_to_default_values(self):
        """
        ==========================================
        Reset all supported camera property values to numeric defaults.

        Note:
            This does not change auto/manual flags.

        Returns:
            tuple: (all_success: bool, reset_count: int, total_supported_properties: int)
        ==========================================
        """
        if not self.is_open or self.device_bridge is None:
            return False, 0, 0

        all_success, results = self.device_bridge.reset_all_properties_to_default_values()

        reset_count = 0
        for property_name, success, actual_value in results:
            if not bool(success):
                continue
            reset_count += 1
            self._update_cached_property_value(property_name, actual_value)

        total_supported = len(results)

        return all_success, int(reset_count), int(total_supported)

    def reset_all_property_flags(self):
        """
        ==========================================
        Reset all auto/manual property flags to Auto mode.

        Returns:
            tuple: (all_success: bool, updated_count: int, total_auto_supported_properties: int)
        ==========================================
        """
        if not self.is_open or self.device_bridge is None:
            return False, 0, 0

        all_success, results = self.device_bridge.reset_all_property_flags()

        updated_count = 0
        for property_name, success, is_auto_enabled in results:
            if not bool(success):
                continue
            updated_count += 1
            self._update_cached_property_auto_mode(property_name, bool(is_auto_enabled))

        total_auto_supported = len(results)

        return all_success, int(updated_count), int(total_auto_supported)

    def _update_cached_property_value(self, property_name: str, actual_value: int):
        """
        ==========================================
        Update one property's cached numeric value.
        ==========================================
        """
        with self._property_cache_lock:
            existing_name = self._get_cached_property_key(property_name)
            if existing_name is None:
                return

            existing_range = self.property_ranges.get(existing_name)
            if existing_range is None:
                return

            updated_range = CameraRange(
                min=existing_range.min,
                max=existing_range.max,
                step=existing_range.step,
                default=existing_range.default,
                current=float(actual_value),
                property_supported=existing_range.property_supported,
                is_auto=existing_range.is_auto,
                auto_supported=existing_range.auto_supported
            )
            self.property_ranges[existing_name] = updated_range

            if self.device_path in self._ranges_cache:
                cached_ranges = self._ranges_cache[self.device_path]
                if existing_name in cached_ranges:
                    cached_ranges[existing_name] = updated_range

    def _update_cached_property_auto_mode(self, property_name: str, is_auto_enabled: bool):
        """
        ==========================================
        Update one property's cached auto/manual mode.
        ==========================================
        """
        with self._property_cache_lock:
            existing_name = self._get_cached_property_key(property_name)
            if existing_name is None:
                return

            existing_range = self.property_ranges.get(existing_name)
            if existing_range is None:
                return

            updated_range = CameraRange(
                min=existing_range.min,
                max=existing_range.max,
                step=existing_range.step,
                default=existing_range.default,
                current=existing_range.current,
                property_supported=existing_range.property_supported,
                is_auto=bool(is_auto_enabled),
                auto_supported=existing_range.auto_supported
            )
            self.property_ranges[existing_name] = updated_range

            if self.device_path in self._ranges_cache:
                cached_ranges = self._ranges_cache[self.device_path]
                if existing_name in cached_ranges:
                    cached_ranges[existing_name] = updated_range

    def _refresh_property_ranges_cache(self):
        """
        ==========================================
        Refresh property ranges from camera and update local caches.
        ==========================================
        """
        if not self.device_path:
            return

        refreshed_ranges = Camera.get_camera_ranges(self.device_path)
        with self._property_cache_lock:
            self.property_ranges = refreshed_ranges
            if self.device_path in self._ranges_cache:
                self._ranges_cache[self.device_path] = refreshed_ranges
            self._rebuild_property_key_index()

    def _rebuild_property_key_index(self):
        """
        ==========================================
        Rebuild lowercase property-name lookup index for O(1) cache updates.
        ==========================================
        """
        self._property_key_index = {
            str(existing_name).lower(): existing_name
            for existing_name in self.property_ranges.keys()
        }

    def _get_cached_property_key(self, property_name: str):
        """
        ==========================================
        Resolve user-provided property name to canonical cache key.
        ==========================================
        """
        requested_key = str(property_name).lower()
        existing_name = self._property_key_index.get(requested_key)
        if existing_name is not None:
            return existing_name

        # Defensive fallback in case index is stale.
        self._rebuild_property_key_index()
        return self._property_key_index.get(requested_key)
    
    def set_frame_callback(self, callback):
        """
        =====================
        Register a callback function to receive frames.
        
        Args:
            callback (callable): Function that accepts (success: bool, frame: np.ndarray)
        
        Note: The callback is called automatically when frames arrive from DirectShow.
        =====================
        """
        self.frame_callback = callback
        
        # If camera is already open, register callback with device bridge
        if self.is_open and self.device_bridge:
            self.device_bridge.set_frame_callback(self._on_frame_ready)
    