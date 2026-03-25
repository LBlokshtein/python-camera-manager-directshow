# Changelog

All notable changes to this project are documented in this file.

## 0.2.0 - 2026-03-25

### Added

- Added PyQt5 as a project dependency in both `pyproject.toml` and `requirements.txt`.
- Clarified that `PyTurboJPEG` is optional (acceleration only) and not part of default project dependencies.
- Added MJPG decoder selection and reporting APIs:
  - `CameraDeviceBridge.get_active_mjpg_decoder_name()`
  - `Camera.get_active_mjpg_decoder_name()`
- Added TurboJPEG initialization flow in the device bridge:
  - optional native DLL probing via common Windows paths
  - support for `TURBOJPEG_LIB_PATH`
  - one-time decoder status logging
- Added safer static typing support for bridge internals:
  - type aliases for debug logging config
  - `TYPE_CHECKING` import for `CameraFormat`
- Added bridge-availability guards in camera manager discovery helpers to avoid `None` access in edge cases.
- Added practical maintainer/release guidance and troubleshooting sections in README.

### Changed

- Bumped package version from `0.1.0` to `0.2.0`.
- Replaced the previous Tkinter-based GUI flow with a PyQt5 GUI flow (`run_gui`) in `GUI/main_GUI.py` and `app/main.py`.
- Updated app entrypoint flow in `app/main.py` to launch the consolidated GUI via `run_gui(camera)`.
- Updated MJPG decode pipeline in `camera/camera_device_bridge.py`:
  - prefer TurboJPEG decode when available
  - fall back to OpenCV decode
  - report explicit error only when both are unavailable
- Updated pythonnet interop calls for better type-checker compatibility:
  - dynamic lookup for `clr.AddReference`
  - dynamic import/getattr for `DirectShowLibWrapper` symbols
- Updated README to reflect current architecture, dependencies, setup, and runtime behavior.

### Fixed

- Fixed common false-positive static analysis diagnostics around pythonnet dynamic APIs by making runtime-bound imports explicit.
- Fixed potential discovery-path failures by handling uninitialized bridge state in camera manager methods.
- Fixed MJPG fallback behavior and decoder visibility in UI-facing format status workflows.

### Performance

- Reduced MJPG decode bottlenecks in the frame pipeline by preferring TurboJPEG when available.
- Observed result in local testing: stream throughput improved from about 17/30 FPS to sustaining the full camera FPS target (for example ~30/30), depending on device/driver/format.

### Repository Maintenance

- Updated `.gitignore` with capture output and legacy-file ignore entries to keep repository state cleaner during development.

