import os
import sys

# Allow running as `python app/main.py` while importing top-level packages.
PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

from camera import _camera
from GUI.main_GUI import MainGUI, select_camera_gui, show_no_camera_dialog
import cv2
import threading
cv2.utils.logging.setLogLevel(cv2.utils.logging.LOG_LEVEL_ERROR)

debug = False

def debug_print(*args, **kwargs):
    """Print only if DEBUG mode is enabled."""
    if debug:
        print(*args, **kwargs)

def main():
    gui = MainGUI()
    # Scan for devices and prompt user to Retry/Cancel if none found
    while True:
        uvc_cameras = _camera.Camera.get_connected_cameras(get_formats=True, get_ranges=True)

        if uvc_cameras:
            break

        retry = show_no_camera_dialog(gui.root)
        if not retry:
            print("No cameras found. Exiting.")
            return

    # Get device path and format from GUI
    device_path, camera_format, request_rgb24 = select_camera_gui(uvc_cameras, gui.root)
    if device_path is None or camera_format is None:
        print("No camera selected. Exiting.")
        return

    debug_print(f"Selected camera: {device_path}")
    debug_print(f"Format: {camera_format.width}x{camera_format.height} @ {camera_format.fps} FPS")

    # Open the selected camera using the Camera class
    camera = _camera.Camera(debug_logging=[False,False,False])
    
    # Connect the GUI callback to the camera BEFORE opening
    camera.set_frame_callback(gui.update_video_frame)
    
    # Open with device path and format
    camera_opened_successfully = camera.open(
        device_path,
        camera_format,
        request_rgb24_conversion=bool(request_rgb24)
    )
    if not camera_opened_successfully:
        debug_print("Failed to open camera.")
        return

    # Bind active camera to GUI controls (e.g., format switching)
    gui.bind_camera(camera, device_path)
    
    debug_print("Camera opened successfully!")
    
    # Handle window close event to ensure clean shutdown
    closing_state = {"started": False}

    def on_close():
        if closing_state["started"]:
            return

        closing_state["started"] = True

        try:
            # Stop forwarding new frames to Tk while shutdown is in progress.
            camera.set_frame_callback(None)
        except Exception:
            pass

        def close_camera_background():
            try:
                camera.close()
            except Exception as e:
                print(f"Error closing camera: {e}")

        close_thread = threading.Thread(target=close_camera_background, daemon=True)
        close_thread.start()

        def finish_close():
            try:
                gui.root.destroy()
            except Exception:
                pass

        # Try fast/clean close first; if driver blocks, force-close UI shortly after.
        gui.root.after(50, finish_close)
        gui.root.after(1500, finish_close)
    
    gui.root.protocol("WM_DELETE_WINDOW", on_close)
    
    # Start the GUI event loop
    gui.run()

if __name__ == "__main__":
    main()
