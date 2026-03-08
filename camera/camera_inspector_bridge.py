import os
import sys
import clr

class CameraInspectorBridge:
    def __init__(self):
        self._inspector = None
        self._initialize_bridge()

    def _initialize_bridge(self):
        """Loads the DLLs once during instantiation."""
        current_dir = os.path.dirname(os.path.abspath(__file__))
        project_root = os.path.dirname(current_dir)
        dotnet_runtime_dir = os.path.join(project_root, "runtime", "dotnet")

        # Prefer runtime/dotnet layout, but keep camera/ fallback for compatibility.
        candidate_paths = [dotnet_runtime_dir, current_dir]
        for candidate in candidate_paths:
            if os.path.isdir(candidate) and candidate not in sys.path:
                sys.path.append(candidate)

        try:
            # Add references once
            clr.AddReference("DirectShowLib") # Good practice to include the dependency
            clr.AddReference("DirectShowLibWrapper")

            # Import the static class from the DLL
            from DirectShowLibWrapper import CameraInspector
            self._inspector = CameraInspector
            # print("Successfully initialized .NET Camera Bridge.")
            
        except Exception as e:
            print(f"CRITICAL: Failed to load .NET DLLs: {e}")
            self._inspector = None

    def get_connected_cameras(self):
        if not self._inspector: return None
        return self._inspector.GetConnectedCameras()

    def get_camera_ranges(self, device_path):
        if not self._inspector: return None
        return self._inspector.GetCameraRanges(device_path)

    def get_camera_formats(self, device_path):
        if not self._inspector: return None
        return self._inspector.GetSupportedFormats(device_path)