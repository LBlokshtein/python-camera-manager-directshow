import sys
import threading
from PyQt5.QtWidgets import (
    QApplication,
    QMainWindow,
    QAction,
    QDialog,
    QVBoxLayout,
    QHBoxLayout,
    QLabel,
    QComboBox,
    QPushButton,
    QWidget,
    QMessageBox,
    QScrollArea,
    QGroupBox,
    QCheckBox,
    QSlider,
    QSizePolicy,
)
from PyQt5.QtCore import Qt, pyqtSignal


class CameraDialog(QDialog):
    def __init__(self, camera_infos, parent=None):
        """
        ==========================================
        Initialize the camera selection dialog.
        ==========================================
        """
        super().__init__(parent)
        self.setWindowTitle("Select Camera")
        self.setModal(True)
        self.setFixedSize(350, 230)
        self.camera_infos = camera_infos
        self.formats_cache = {}
        layout = QVBoxLayout()
        label = QLabel("Connected Cameras:")
        layout.addWidget(label)
        self.combo = QComboBox()
        self.cameras = [c.name if hasattr(c, 'name') else str(c) for c in camera_infos]
        self.combo.addItems(self.cameras)
        layout.addWidget(self.combo)

        # Cache formats and ranges for each camera (by index)
        self.ranges_cache = {}
        for idx, cam in enumerate(camera_infos):
            # Try to get formats and ranges attributes, fallback to empty list/dict
            formats = getattr(cam, 'formats', [])
            ranges = getattr(cam, 'ranges', {})
            self.formats_cache[idx] = formats
            self.ranges_cache[idx] = ranges

        # Add format dropdown
        self.format_label = QLabel("Select Format:")
        layout.addWidget(self.format_label)
        self.format_combo = QComboBox()
        layout.addWidget(self.format_combo)

        # Populate formats for the initially selected camera
        self.update_formats(0)
        self.combo.currentIndexChanged.connect(self.update_formats)

        # Add RGB24 conversion checkbox
        from PyQt5.QtWidgets import QCheckBox
        self.rgb24_checkbox = QCheckBox("Request RGB24 conversion (force RGB output)")
        self.rgb24_checkbox.setChecked(False)
        layout.addWidget(self.rgb24_checkbox)

        self.ok_button = QPushButton("OK")
        self.ok_button.clicked.connect(self.accept)
        layout.addWidget(self.ok_button)
        self.setLayout(layout)

    def request_rgb24(self):
        """
        ==========================================
        Return True if RGB24 conversion is requested.
        ==========================================
        """
        return self.rgb24_checkbox.isChecked()

    def update_formats(self, camera_index):
        """
        ==========================================
        Update the format combo box for the selected camera.
        ==========================================
        """
        self.format_combo.clear()
        formats = self.formats_cache.get(camera_index, [])
        if not formats:
            self.format_combo.addItem("No formats available")
        else:
            # Show as WxH @ FPS (PixelFormat)
            for fmt in formats:
                if hasattr(fmt, 'width') and hasattr(fmt, 'height') and hasattr(fmt, 'fps') and hasattr(fmt, 'pixel_format'):
                    label = f"{fmt.width}x{fmt.height} @ {fmt.fps} ({fmt.pixel_format})"
                else:
                    label = str(fmt)
                self.format_combo.addItem(label)


class MainWindow(QMainWindow):
    frame_update_signal = pyqtSignal(bool, object)
    _format_changed_signal = pyqtSignal(bool)

    def __init__(self, camera):
        """
        ==========================================
        Initialize the main application window and layout.
        ==========================================
        """
        super().__init__()
        self.camera = camera
        self.device_path = None
        self.setWindowTitle("Rolling Shutter Correction App")
        self.setGeometry(100, 100, 900, 700)
        self._create_menu()

        # Main split layout: video area on the left, camera controls on the right.
        self.central_widget = QWidget()
        self.setCentralWidget(self.central_widget)
        self.main_layout = QHBoxLayout(self.central_widget)

        self.video_container = QWidget()
        self.video_layout = QVBoxLayout(self.video_container)
        self.video_layout.setContentsMargins(0, 0, 0, 0)

        self.video_stage = QWidget()
        self.video_stage_layout = QVBoxLayout(self.video_stage)
        self.video_stage_layout.setContentsMargins(0, 0, 0, 0)

        self.video_label = QLabel("Camera stream will appear here.")
        self.video_label.setAlignment(Qt.AlignCenter)
        self.video_label.setMinimumSize(0, 0)
        self.video_label.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Ignored)
        self.video_stage_layout.addWidget(self.video_label)

        self.fps_label = QLabel(".NET FPS: -- | Received: -- | Displayed: --", self.video_stage)
        self.fps_label.setAlignment(Qt.AlignLeft | Qt.AlignTop)
        self.fps_label.setStyleSheet("font-size: 10pt; color: #333; background: rgba(255,255,255,0.7); padding: 2px 6px;")
        self.fps_label.setAttribute(Qt.WA_TransparentForMouseEvents, True)
        self.fps_label.move(8, 8)
        self.fps_label.adjustSize()
        self.fps_label.raise_()

        self.video_layout.addWidget(self.video_stage)
        self.main_layout.addWidget(self.video_container, stretch=3)

        self.controls_scroll_area = QScrollArea()
        self.controls_scroll_area.setWidgetResizable(True)
        self.controls_scroll_area.setFixedWidth(360)
        self.controls_scroll_area.setSizePolicy(QSizePolicy.Fixed, QSizePolicy.Expanding)
        self.controls_widget = QWidget()
        self.controls_layout = QVBoxLayout(self.controls_widget)
        self.controls_scroll_area.setWidget(self.controls_widget)
        self.main_layout.addWidget(self.controls_scroll_area)

        self.controls_group = QGroupBox("Camera Controls")
        self.controls_group_layout = QVBoxLayout(self.controls_group)
        self.controls_layout.addWidget(self.controls_group)

        self.current_format_label = QLabel("Current format: N/A")
        self.current_format_label.setWordWrap(True)
        self.controls_group_layout.addWidget(self.current_format_label)

        self.format_button = QPushButton("Camera Format Options")
        self.format_button.setEnabled(False)
        self.format_button.clicked.connect(self.show_camera_format_options)
        self.controls_group_layout.addWidget(self.format_button)

        self.reset_settings_button = QPushButton("Reset Settings")
        self.reset_settings_button.setEnabled(False)
        self.reset_settings_button.clicked.connect(self.show_reset_settings_options)
        self.controls_group_layout.addWidget(self.reset_settings_button)

        self.auto_title = QLabel("Auto/Manual Controls")
        self.controls_group_layout.addWidget(self.auto_title)
        self.auto_controls_widget = QWidget()
        self.auto_controls_layout = QVBoxLayout(self.auto_controls_widget)
        self.auto_controls_layout.setContentsMargins(0, 0, 0, 0)
        self.controls_group_layout.addWidget(self.auto_controls_widget)

        self.property_title = QLabel("Property Controls")
        self.controls_group_layout.addWidget(self.property_title)
        self.property_controls_widget = QWidget()
        self.property_controls_layout = QVBoxLayout(self.property_controls_widget)
        self.property_controls_layout.setContentsMargins(0, 0, 0, 0)
        self.controls_group_layout.addWidget(self.property_controls_widget)

        self.controls_layout.addStretch(1)

        self.auto_mode_checkboxes = {}
        self.property_sliders = {}
        self.property_slider_labels = {}
        self._updating_property_sliders = set()

        self.current_camera = None
        self._last_frame_time = None
        self._frame_count = 0
        self._displayed_count = 0
        self._last_fps_update = None
        self._received_fps = 0.0
        self._displayed_fps = 0.0
        self._dotnet_fps_value = None

        # Connect the signal to the GUI update slot
        self.frame_update_signal.connect(self._update_video_frame_gui)
        self._format_changed_signal.connect(self._on_format_changed)

    def _create_menu(self):
        """
        ==========================================
        Create the application menu bar and top-level menus.
        ==========================================
        """
        menubar = self.menuBar()
        camera_menu = menubar.addMenu("Camera")

        select_action = QAction("Select Camera", self)
        select_action.triggered.connect(self.show_camera_dialog)
        camera_menu.addAction(select_action)

        format_action = QAction("Camera Format Options", self)
        format_action.triggered.connect(self.show_camera_format_options)
        camera_menu.addAction(format_action)

        reset_action = QAction("Reset Settings", self)
        reset_action.triggered.connect(self.show_reset_settings_options)
        camera_menu.addAction(reset_action)

    def show_camera_dialog(self):
        """
        ==========================================
        Show the camera selection dialog and open the selected camera.
        ==========================================
        """
        # Use the passed-in camera manager or class to get connected cameras, formats, and ranges
        try:
            camera_infos = self.camera.get_connected_cameras(get_formats=True, get_ranges=True)
        except Exception:
            camera_infos = []
        if not camera_infos:
            QMessageBox.warning(self, "No Cameras", "No cameras are connected.")
            return
        dialog = CameraDialog(camera_infos, self)
        dialog.setWindowModality(Qt.ApplicationModal)
        # Center the dialog in the main window
        parent_geom = self.geometry()
        dialog.move(
            parent_geom.center().x() - dialog.width() // 2,
            parent_geom.center().y() - dialog.height() // 2
        )
        if dialog.exec_() == QDialog.Accepted:
            cam_idx = dialog.combo.currentIndex()
            fmt_idx = dialog.format_combo.currentIndex()
            rgb24 = dialog.request_rgb24()
            cam_info = camera_infos[cam_idx]
            # Defensive: check formats
            if not cam_info.formats or fmt_idx < 0 or fmt_idx >= len(cam_info.formats):
                QMessageBox.warning(self, "Format Error", "No valid format selected.")
                return
            camera_format = cam_info.formats[fmt_idx]
            device_path = cam_info.path
            # Open the camera
            try:
                # Close any existing camera before opening a new one.
                if self.current_camera is not None:
                    try:
                        self.camera.close()
                    except Exception:
                        pass
                # Set the frame callback to our handler (update_video_frame)
                self.camera.set_frame_callback(self.update_video_frame)
                self.camera.open(device_path, camera_format, request_rgb24_conversion=rgb24)
                self.current_camera = self.camera
                self.device_path = device_path
            except Exception as e:
                QMessageBox.critical(self, "Camera Open Error", f"Failed to open camera: {e}")
                return

            self.format_button.setEnabled(True)
            self.reset_settings_button.setEnabled(True)
            self._refresh_current_format_label()
            self._refresh_auto_mode_controls()
            self._refresh_property_value_controls()

            # Reset FPS counters
            from time import time
            self._last_frame_time = time()
            self._last_fps_update = time()
            self._frame_count = 0
            self._displayed_count = 0
            self._received_fps = 0.0
            self._displayed_fps = 0.0

    @staticmethod
    def _format_to_display_text(camera_format):
        """
        ==========================================
        Format camera mode details into a user-friendly string.
        ==========================================
        """
        return (
            f"{camera_format.width} x {camera_format.height} @ "
            f"{float(camera_format.fps):.2f} FPS ({camera_format.pixel_format})"
        )

    def _set_format_status_color(self, color_name):
        """
        ==========================================
        Apply status color to current format label.
        ==========================================
        """
        self.current_format_label.setStyleSheet(f"color: {color_name};")

    def _refresh_current_format_label(self, format_change_succeeded=None):
        """
        ==========================================
        Refresh current format text and status color.
        ==========================================
        """
        current_format = getattr(self.camera, "current_format", None) if self.camera is not None else None
        if self.camera is None or current_format is None:
            self.current_format_label.setText("Current format: N/A")
            self._set_format_status_color("black")
            return

        label_text = f"Current format: {self._format_to_display_text(current_format)}"
        pixel_format = str(getattr(current_format, "pixel_format", "") or "").strip().upper()
        if pixel_format in ("MJPG", "MJPEG") and hasattr(self.camera, "get_active_mjpg_decoder_name"):
            decoder_name = self.camera.get_active_mjpg_decoder_name()
            if decoder_name is not None:
                label_text = f"{label_text}\nDecoder: {decoder_name}"

        self.current_format_label.setText(label_text)
        if format_change_succeeded is True:
            self._set_format_status_color("green")
        elif format_change_succeeded is False:
            self._set_format_status_color("red")
        else:
            self._set_format_status_color("black")

    def _clear_layout(self, layout):
        """
        ==========================================
        Remove all widgets from a Qt layout.
        ==========================================
        """
        while layout.count():
            item = layout.takeAt(0)
            widget = item.widget()
            if widget is not None:
                widget.deleteLater()

    @staticmethod
    def _as_float(value, default_value=0.0):
        """
        ==========================================
        Convert values from camera range objects to float safely.
        ==========================================
        """
        try:
            return float(value)
        except Exception:
            return float(default_value)

    def _get_property_range_for_name(self, property_name):
        """
        ==========================================
        Find property range using case-insensitive name matching.
        ==========================================
        """
        if self.camera is None:
            return None, None

        ranges = getattr(self.camera, "property_ranges", {}) or {}
        for name, camera_range in ranges.items():
            if str(name).lower() == str(property_name).lower():
                return name, camera_range
        return None, None

    def _refresh_auto_mode_controls(self):
        """
        ==========================================
        Rebuild auto/manual controls from camera property ranges.
        ==========================================
        """
        self._clear_layout(self.auto_controls_layout)
        self.auto_mode_checkboxes = {}

        if self.camera is None:
            return

        ranges = getattr(self.camera, "property_ranges", {}) or {}
        supported = []
        for name, camera_range in ranges.items():
            if bool(getattr(camera_range, "property_supported", False)) and bool(getattr(camera_range, "auto_supported", False)):
                supported.append((name, camera_range))

        if not supported:
            self.auto_controls_layout.addWidget(QLabel("No auto/manual controls available"))
            return

        for property_name, camera_range in sorted(supported, key=lambda x: str(x[0]).lower()):
            checkbox = QCheckBox(f"{property_name} Auto")
            checkbox.setChecked(bool(getattr(camera_range, "is_auto", False)))
            checkbox.toggled.connect(
                lambda checked, n=property_name, cb=checkbox: self._on_auto_mode_toggle(n, checked, cb)
            )
            self.auto_controls_layout.addWidget(checkbox)
            self.auto_mode_checkboxes[str(property_name)] = checkbox

    def _on_auto_mode_toggle(self, property_name, requested_auto_on, checkbox):
        """
        ==========================================
        Handle user toggling of one auto/manual property checkbox.
        ==========================================
        """
        if self.camera is None:
            return

        try:
            success, is_auto_enabled = self.camera.set_property_auto_mode(str(property_name), bool(requested_auto_on))
        except Exception:
            success, is_auto_enabled = False, bool(not requested_auto_on)

        checkbox.blockSignals(True)
        checkbox.setChecked(bool(is_auto_enabled))
        checkbox.blockSignals(False)

        self._set_format_status_color("green" if success else "red")
        self._refresh_property_value_controls()

    def _refresh_property_value_controls(self):
        """
        ==========================================
        Rebuild property sliders from camera property ranges.
        ==========================================
        """
        self._clear_layout(self.property_controls_layout)
        self.property_sliders = {}
        self.property_slider_labels = {}
        self._updating_property_sliders = set()

        if self.camera is None:
            self.property_controls_layout.addWidget(QLabel("Property controls not available"))
            return

        ranges = getattr(self.camera, "property_ranges", {}) or {}
        supported = []
        for name, camera_range in ranges.items():
            if bool(getattr(camera_range, "property_supported", False)):
                supported.append((name, camera_range))

        if not supported:
            self.property_controls_layout.addWidget(QLabel("Property controls not available"))
            return

        for property_name, camera_range in sorted(supported, key=lambda x: str(x[0]).lower()):
            display_name = str(property_name)
            label = QLabel(display_name)
            self.property_controls_layout.addWidget(label)

            min_value = self._as_float(getattr(camera_range, "min", 0), 0)
            max_value = self._as_float(getattr(camera_range, "max", 0), 0)
            step_value = self._as_float(getattr(camera_range, "step", 1), 1)
            if step_value <= 0:
                step_value = 1.0
            current_value = self._as_float(getattr(camera_range, "current", min_value), min_value)

            slider = QSlider(Qt.Horizontal)
            slider.setMinimum(int(round(min_value)))
            slider.setMaximum(int(round(max_value)))
            slider.setSingleStep(int(max(1, round(step_value))))
            slider.setPageStep(int(max(1, round(step_value))))
            slider.setValue(int(round(current_value)))
            slider.valueChanged.connect(lambda value, n=display_name: self._on_property_slider_change(n, value))

            is_auto = bool(getattr(camera_range, "auto_supported", False) and getattr(camera_range, "is_auto", False))
            slider.setEnabled(not is_auto)
            self.property_controls_layout.addWidget(slider)

            value_label = QLabel(
                f"Min: {int(round(min_value))}    Max: {int(round(max_value))}    Value: {int(round(current_value))}"
            )
            self.property_controls_layout.addWidget(value_label)

            self.property_sliders[display_name] = slider
            self.property_slider_labels[display_name] = value_label

    def _on_property_slider_change(self, property_name, raw_value):
        """
        ==========================================
        Handle property slider movement and apply snapped values.
        ==========================================
        """
        if self.camera is None:
            return

        property_key = str(property_name)
        if property_key in self._updating_property_sliders:
            return

        _, selected_range = self._get_property_range_for_name(property_name)
        if selected_range is None:
            return

        min_value = self._as_float(getattr(selected_range, "min", 0), 0)
        max_value = self._as_float(getattr(selected_range, "max", 0), 0)
        step_value = self._as_float(getattr(selected_range, "step", 1), 1)
        if step_value <= 0:
            step_value = 1.0

        raw_numeric = float(raw_value)
        snapped_value = min_value + round((raw_numeric - min_value) / step_value) * step_value
        snapped_value = max(min_value, min(max_value, snapped_value))
        target_value = int(round(snapped_value))

        try:
            success, actual_value = self.camera.set_property_value(str(property_name), target_value)
        except Exception:
            success, actual_value = False, target_value

        actual_value = int(round(self._as_float(actual_value, target_value)))

        slider = self.property_sliders.get(property_key)
        if slider is not None:
            self._updating_property_sliders.add(property_key)
            slider.setValue(actual_value)
            self._updating_property_sliders.discard(property_key)

        value_label = self.property_slider_labels.get(property_key)
        if value_label is not None:
            value_label.setText(
                f"Min: {int(round(min_value))}    Max: {int(round(max_value))}    Value: {int(round(actual_value))}"
            )

        self._set_format_status_color("green" if success else "red")

    @staticmethod
    def _show_reset_failure_message(parent, action_title, success_count, total_count):
        """
        ==========================================
        Show one aggregated failure message for reset operations.
        ==========================================
        """
        failed_count = max(0, int(total_count) - int(success_count))

        if int(total_count) <= 0:
            message = f"{action_title} failed: no supported properties were available to reset."
        elif int(success_count) <= 0:
            message = f"{action_title} failed: 0/{int(total_count)} properties were reset."
        else:
            message = (
                f"{action_title} partially failed: "
                f"{int(success_count)}/{int(total_count)} succeeded, {failed_count} failed."
            )

        QMessageBox.critical(parent, "Reset Failed", message)

    def show_reset_settings_options(self):
        """
        ==========================================
        Show reset actions dialog for properties and property flags.
        ==========================================
        """
        if self.camera is None:
            return

        dialog = QDialog(self)
        dialog.setWindowFlag(Qt.WindowContextHelpButtonHint, False)
        dialog.setWindowTitle("Reset Settings")
        dialog.setModal(True)
        dialog.setFixedSize(420, 170)

        layout = QVBoxLayout(dialog)
        title = QLabel("Choose reset action")
        title.setAlignment(Qt.AlignCenter)
        layout.addWidget(title)

        reset_properties_btn = QPushButton("Reset Properties")
        reset_flags_btn = QPushButton("Reset Property Flags")
        close_btn = QPushButton("Close")

        layout.addWidget(reset_properties_btn)
        layout.addWidget(reset_flags_btn)
        layout.addWidget(close_btn)

        def on_reset_properties():
            try:
                all_success, reset_count, total_supported = self.camera.reset_all_properties_to_default_values()
            except Exception:
                all_success, reset_count, total_supported = False, 0, 0

            if all_success:
                reset_properties_btn.setText("Reset Properties - Success")
                reset_properties_btn.setEnabled(False)
                self._set_format_status_color("green")
                self._refresh_property_value_controls()
                return

            self._set_format_status_color("red")
            self._refresh_property_value_controls()
            self._show_reset_failure_message(self, "Reset Properties", reset_count, total_supported)

        def on_reset_flags():
            try:
                all_success, updated_count, total_auto_supported = self.camera.reset_all_property_flags()
            except Exception:
                all_success, updated_count, total_auto_supported = False, 0, 0

            if all_success:
                reset_flags_btn.setText("Reset Property Flags - Success")
                reset_flags_btn.setEnabled(False)
                self._set_format_status_color("green")
                self._refresh_auto_mode_controls()
                self._refresh_property_value_controls()
                return

            self._set_format_status_color("red")
            self._refresh_auto_mode_controls()
            self._refresh_property_value_controls()
            self._show_reset_failure_message(self, "Reset Property Flags", updated_count, total_auto_supported)

        reset_properties_btn.clicked.connect(on_reset_properties)
        reset_flags_btn.clicked.connect(on_reset_flags)
        close_btn.clicked.connect(dialog.accept)

        dialog.exec_()

    def show_camera_format_options(self):
        """
        ==========================================
        Show available formats and allow switching to a different one.
        ==========================================
        """
        if self.camera is None or self.device_path is None:
            return

        try:
            available_formats = self.camera.get_camera_formats(self.device_path) or []
        except Exception:
            available_formats = []

        if not available_formats:
            available_formats = getattr(self.camera, "available_formats", []) or []
        else:
            self.camera.available_formats = available_formats

        if not available_formats:
            QMessageBox.warning(self, "Camera Format Options", "No formats are available for the current camera.")
            return

        current_format = getattr(self.camera, "current_format", None)

        dialog = QDialog(self)
        dialog.setWindowFlag(Qt.WindowContextHelpButtonHint, False)
        dialog.setWindowTitle("Camera Format Options")
        dialog.setModal(True)
        dialog.setFixedSize(460, 200)
        layout = QVBoxLayout(dialog)

        current_text = "Current format: "
        if current_format is not None:
            current_text += self._format_to_display_text(current_format)
        else:
            current_text += "Unknown"

        current_label = QLabel(current_text)
        current_label.setWordWrap(True)
        layout.addWidget(current_label)

        combo_formats = QComboBox()
        display_formats = [self._format_to_display_text(fmt) for fmt in available_formats]
        combo_formats.addItems(display_formats)
        layout.addWidget(combo_formats)

        request_rgb24_checkbox = QCheckBox("Request RGB24")
        request_rgb24_checkbox.setChecked(bool(getattr(self.camera, "_request_rgb24_conversion", False)))
        layout.addWidget(request_rgb24_checkbox)

        if current_format is not None:
            for idx, fmt in enumerate(available_formats):
                if fmt == current_format:
                    combo_formats.setCurrentIndex(idx)
                    break

        button_row = QHBoxLayout()
        apply_btn = QPushButton("Apply")
        close_btn = QPushButton("Close")
        button_row.addWidget(apply_btn)
        button_row.addWidget(close_btn)
        layout.addLayout(button_row)

        def on_apply():
            selected_idx = combo_formats.currentIndex()
            if selected_idx < 0:
                dialog.accept()
                return

            target_format = available_formats[selected_idx]
            request_rgb24 = request_rgb24_checkbox.isChecked()
            dialog.accept()
            self._set_format_status_color("black")

            def apply_format_in_background():
                try:
                    format_changed = bool(
                        self.camera.set_format(target_format, request_rgb24_conversion=bool(request_rgb24))
                    )
                except Exception:
                    format_changed = False

                self._format_changed_signal.emit(format_changed)

            threading.Thread(target=apply_format_in_background, daemon=True).start()

        apply_btn.clicked.connect(on_apply)
        close_btn.clicked.connect(dialog.accept)

        dialog.exec_()


    def update_video_frame(self, success, frame):
        """
        ==========================================
        Frame callback: schedule GUI update for new frame (thread-safe).
        ==========================================
        """
        # Emit the signal to update the GUI in the main thread
        self.frame_update_signal.emit(success, frame)

    def _on_format_changed(self, format_changed: bool):
        self._refresh_current_format_label(format_changed)
        self._refresh_auto_mode_controls()
        self._refresh_property_value_controls()

    def _update_video_frame_gui(self, success, frame):
        """
        ==========================================
        Update the GUI with the new video frame.
        ==========================================
        """
        import time
        if not success or frame is None:
            return
        now = time.time()
        try:
            self._frame_count += 1
            # Assume frame is a numpy array (H, W, 3) in RGB
            import numpy as np
            from PyQt5.QtGui import QImage, QPixmap
            if frame.dtype != np.uint8:
                frame = frame.astype(np.uint8)

            frame_rgb = np.ascontiguousarray(frame[:, :, ::-1])  # BGR→RGB, contiguous
            h, w, ch = frame_rgb.shape
            bytes_per_line = ch * w
            qimg = QImage(frame_rgb.data, w, h, bytes_per_line, QImage.Format_RGB888)
            pixmap = QPixmap.fromImage(qimg)
            label_size = self.video_label.size()
            if pixmap.width() > label_size.width() or pixmap.height() > label_size.height():
                pixmap = pixmap.scaled(label_size, Qt.KeepAspectRatio, Qt.FastTransformation)
            self.video_label.setPixmap(pixmap)
            self._displayed_count += 1
        except Exception as e:
            # Optionally log or show error
            pass
        self._update_fps_label()


    def _update_fps_label(self):
        """
        ==========================================
        Update the FPS label with current stats.
        ==========================================
        """
        import time
        now = time.time()
        # Update every 1s
        if self._last_fps_update is None:
            self._last_fps_update = now
        elapsed = now - self._last_fps_update
        if elapsed >= 1:
            self._received_fps = self._frame_count / elapsed
            self._displayed_fps = self._displayed_count / elapsed
            # Periodically fetch .NET FPS and store in self._dotnet_fps_value
            if self.camera is not None:
                try:
                    dotnet_fps = float(self.camera.get_current_fps())
                    self._dotnet_fps_value = dotnet_fps if dotnet_fps > 0 else None
                except Exception:
                    self._dotnet_fps_value = None
            else:
                self._dotnet_fps_value = None
            dotnet_text = '--' if self._dotnet_fps_value is None else f"{self._dotnet_fps_value:.2f}"
            self.fps_label.setText(f".NET FPS: {dotnet_text} | Received: {self._received_fps:.2f} | Displayed: {self._displayed_fps:.2f}")
            self.fps_label.adjustSize()
            self._last_fps_update = now
            self._frame_count = 0
            self._displayed_count = 0

def run_gui(camera_manager):
    """
    ==========================================
    Launch the main GUI application.
    ==========================================
    """
    app = QApplication(sys.argv)
    window = MainWindow(camera_manager)
    window.show()
    sys.exit(app.exec_())

