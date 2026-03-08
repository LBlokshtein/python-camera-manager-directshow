# Python Camera Manager (DirectShow Bridge)

A Python-first camera control and preview application built on top of a .NET DirectShow wrapper.

This project was created to solve a common limitation in OpenCV camera workflows: while OpenCV can open cameras quickly, it does not provide a reliable, discoverable, device-specific model of control capabilities (valid ranges, step sizes, defaults, and auto/manual support) across many webcams.

This codebase provides that missing layer:

- discover camera capabilities and supported formats using DirectShow
- expose those capabilities as plain Python data structures
- open and stream camera frames through the same API
- optionally use this metadata with another capture backend (for example OpenCV)

## Why This Project Exists

In many practical camera applications, you need to know more than whether a property exists. You need to know:

- minimum and maximum values
- step increments
- default values
- current values
- whether auto mode is supported
- whether auto mode is currently enabled

OpenCV alone does not consistently provide this level of introspection across devices and drivers. This project addresses that gap by using DirectShow (via a .NET wrapper) for capability discovery and control, then exposing a clean Python API for application code.

## Core Design

### .NET for camera graph and capability access

The .NET layer (`DirectShowLibWrapper.dll`) handles DirectShow-specific operations such as:

- camera enumeration
- supported format discovery
- capability/range discovery
- frame acquisition
- camera property set/get with auto/manual modes

### Python bridge for usability

The Python bridge files:

- `camera/camera_inspector_bridge.py`
- `camera/camera_device_bridge.py`

load the .NET assemblies and call into the wrapper.

### Python manager for developer experience

`camera/camera_manager.py` is the high-level facade.

It converts .NET objects into Python-native types (`NamedTuple`, `dict`, `list`) so downstream code does not need to work with .NET object syntax. The output is intentionally Pythonic and user-friendly.

In other words, consumers can use the API without needing to understand .NET interop details.

## What You Can Do With It

1. Full managed workflow
- discover devices, formats, ranges
- open camera and stream frames
- change auto/manual modes
- set precise property values based on discovered constraints
- format note: without OpenCV installed, streaming requires uncompressed formats (for example RGB24/BGR24/GRAY8). MJPG and YUY2 decoding paths require OpenCV.

2. Hybrid workflow
- use this project only for camera capability discovery (ranges, steps, auto support)
- then open/stream with another backend (such as OpenCV) if preferred

This gives you flexibility to mix tooling while still relying on robust capability metadata.

## Repository Structure

```text

app/
  main.py                      # Main runnable application entrypoint
camera/
  __init__.py
  camera_manager.py            # High-level Python API and cache
  camera_device_bridge.py      # Frame/camera control bridge into .NET
  camera_inspector_bridge.py   # Camera discovery/capability bridge into .NET
GUI/
  main_GUI.py                  # Tkinter UI
runtime/
  dotnet/
    DirectShowLib.dll
    DirectShowLibWrapper.dll
```

## Requirements

- Windows (DirectShow)
- Python 3.10+ (recommended)
- .NET runtime compatible with your `DirectShowLibWrapper.dll`
- A DirectShow-capable camera device

Python packages:

- `pythonnet`
- `opencv-python` (required for MJPG and YUY2 decode paths)
- `Pillow`

Streaming without OpenCV is supported only when the camera output is already an uncompressed format handled by the bridge (for example `RGB24`, `BGR24`, or `GRAY8`).

## Setup

```bash
python -m venv .venv
.venv\Scripts\activate
pip install pythonnet opencv-python Pillow
```

Ensure these assemblies exist:

- `runtime/dotnet/DirectShowLib.dll`
- `runtime/dotnet/DirectShowLibWrapper.dll`

## Run

```bash
python -m app.main
```

Alternative:

```bash
python app/main.py
```

## API Example (Python-native usage)

```python
from camera.camera_manager import Camera

# Discover all cameras with formats and control ranges
devices = Camera.get_connected_cameras(get_formats=True, get_ranges=True)

if not devices:
    raise RuntimeError("No camera detected")

selected = devices[0]
fmt = selected.formats[0]

cam = Camera(debug_logging=False)
cam.open(selected.path, fmt, request_rgb24_conversion=False)

# Ranges are plain Python structures
ranges = cam.property_ranges
exposure = ranges.get("Exposure")
if exposure and exposure.property_supported:
    # Respect discovered min/max/step
    cam.set_property_auto_mode("Exposure", False)
    cam.set_property_value("Exposure", int(exposure.default))

ok, frame = cam.get_frame()
cam.close()
```

## Hybrid Example (Use metadata here, capture elsewhere)

```python
import cv2
from camera.camera_manager import Camera

devices = Camera.get_connected_cameras(get_formats=False, get_ranges=True)
if not devices:
    raise RuntimeError("No camera")

# Use discovered ranges for UI/validation logic
ranges = devices[0].ranges
print(ranges.get("Exposure"))

# Open camera with another backend if desired
cap = cv2.VideoCapture(0, cv2.CAP_DSHOW)
ret, frame = cap.read()
cap.release()
```

## Notes for Recruiters / Reviewers

This project demonstrates:

- cross-language integration (.NET DirectShow + Python)
- practical camera systems engineering
- robust control-surface modeling (ranges, steps, auto/manual capabilities)
- Python API design that hides interop complexity
- real-time GUI integration and format negotiation

## Limitations

- Device behavior depends on camera driver and hardware implementation.
- Some controls may be unsupported or partially supported on specific devices.
- Only one process/backend should actively own a camera stream at a time.
- MJPG and YUY2 decoding require OpenCV in the current implementation; without OpenCV, use uncompressed output formats.

## Companion .NET Repository

The .NET wrapper is intended to be maintained as a separate repository. This Python repository consumes the compiled artifacts and focuses on Python API ergonomics and application usage.

## License

MIT. See `LICENSE`.
