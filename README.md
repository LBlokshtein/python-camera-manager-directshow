# Python Camera Manager (DirectShow Bridge)

Python-first camera discovery, control, and live preview built on top of a .NET DirectShow wrapper.

This project was created to solve a common limitation in OpenCV camera workflows: while OpenCV can open cameras quickly, it does not provide a reliable, discoverable, device-specific model of control capabilities (valid ranges, step sizes, defaults, and auto/manual support) across many webcams.

## Highlights

- Discover connected cameras.
- Query supported formats per device.
- Query control ranges and flags (min/max/step/default/current, supported, auto supported, auto enabled).
- Open camera streams through DirectShow from Python.
- Change auto/manual modes and set property values.
- Preview frames in a PyQt5 GUI.
- Decode MJPG using PyTurboJPEG first (when available), with OpenCV fallback.

## Architecture

- .NET layer in `runtime/dotnet`:
  - `DirectShowLib.dll`
  - `DirectShowLibWrapper.dll`
- Python bridge layer:
  - `camera/camera_inspector_bridge.py`
  - `camera/camera_device_bridge.py`
- Python facade/API:
  - `camera/camera_manager.py`
- GUI:
  - `GUI/main_GUI.py` (PyQt5)

`camera/camera_manager.py` exposes Python-native objects (`NamedTuple`, `dict`, `list`) so consumers do not need to deal with .NET object APIs directly.

## Requirements

- Windows (DirectShow)
- Python 3.10+
- .NET runtime compatible with the shipped wrapper DLLs
- DirectShow-capable camera

Python dependencies:

- `pythonnet`
- `opencv-python`
- `Pillow`
- `PyQt5`

Optional for faster MJPG decode:

- `PyTurboJPEG==2.2.0`
- `libjpeg-turbo` native DLL (`turbojpeg.dll` or `libturbojpeg.dll`)

`PyTurboJPEG` is not required for base functionality and is intentionally not included in default package dependencies.

## Setup

```bash
py -m venv .venv
.venv\Scripts\activate
python -m pip install -U pip
python -m pip install -e .
```

If you want accelerated MJPG decode:

```bash
python -m pip install PyTurboJPEG==2.2.0
```

Without `PyTurboJPEG`, MJPG decoding falls back to OpenCV automatically.

Notes:

- Do not install the unrelated package named `turbojpeg` (different project, different API).
- If needed, point to your native turbojpeg DLL with `TURBOJPEG_LIB_PATH`.

## Run

```bash
python -m app.main
```

Alternative:

```bash
python app/main.py
```

If installed as a package entry point:

```bash
camera-manager
```

## Quick API Example

```python
from camera.camera_manager import Camera

devices = Camera.get_connected_cameras(get_formats=True, get_ranges=True)
if not devices:
    raise RuntimeError("No camera detected")

selected = devices[0]
fmt = selected.formats[0]

cam = Camera(debug_logging=False)
ok = cam.open(selected.path, fmt, request_rgb24_conversion=False)
if not ok:
    raise RuntimeError("Failed to open camera")

try:
    exposure = cam.property_ranges.get("Exposure")
    if exposure and exposure.property_supported:
        cam.set_property_auto_mode("Exposure", False)
        cam.set_property_value("Exposure", int(exposure.default))

    success, frame = cam.get_frame()
finally:
    cam.close()
```

## Decoder Behavior (MJPG)

For MJPG/MJPEG formats, decode priority is:

1. PyTurboJPEG (if import + native DLL initialization succeeds)
2. OpenCV `imdecode`
3. Unavailable (frame skipped with error log)

For uncompressed formats (`RGB24`, `BGR24`, `GRAY8`, etc.), decoding does not require MJPG decoders.

## Troubleshooting

### PyQt5 import error

If you see `ModuleNotFoundError: No module named 'PyQt5'`, verify you are using the project venv and install dependencies:

```bash
.venv\Scripts\python.exe -m pip install -e .
```

### Pylance false positives with Qt/pythonnet

Some warnings are static-analysis false positives due to dynamic runtime APIs in PyQt/pythonnet. Use workspace-level Pylance settings in `.vscode/settings.json` to tune diagnostic severity as needed.

### TurboJPEG confusion

If the wrong package was installed before, clean and reinstall:

```bash
.venv\Scripts\python.exe -m pip uninstall -y turbojpeg
.venv\Scripts\python.exe -m pip install PyTurboJPEG==2.2.0
```

## Maintainer Release Checklist

Before publishing an update:

1. Bump `[project].version` in `pyproject.toml`.
2. Keep `requirements.txt` and `pyproject.toml` dependencies aligned.
3. Verify `project.urls` in `pyproject.toml` point to the correct repository.
4. Confirm runtime DLLs are present under `runtime/dotnet`.
5. Sanity test end-to-end:
   - camera enumeration
   - open/close
   - frame preview
   - property set/get
6. Update README and changelog notes for behavior/API changes.

## Companion .NET Repository

The current Python project repository is:

- https://github.com/LBlokshtein/DirectShowLibWrapper

The .NET wrapper source code is maintained separately; this repository focuses on Python API ergonomics and application usage.

## License

MIT. See `LICENSE`.
