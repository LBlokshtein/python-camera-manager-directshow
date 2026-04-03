from ..camera import _camera
from ..GUI.main_GUI import run_gui
import cv2
cv2.utils.logging.setLogLevel(cv2.utils.logging.LOG_LEVEL_ERROR)
debug = False

def debug_print(*args, **kwargs):
    """Print only if DEBUG mode is enabled."""
    if debug:
        print(*args, **kwargs)

def main():
    camera = _camera.Camera(debug_logging=[False,False,False])
    run_gui(camera)

if __name__ == "__main__":
    main()
