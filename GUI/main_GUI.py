import sys
import tkinter as tk
from tkinter import ttk
from tkinter import messagebox
import threading
import time
import cv2
from PIL import Image, ImageTk



def _create_and_setup_dialog(parent, title, width, height):
    """
    ==========================================
    Create and setup a dialog window with proper centering and transient relationship.
    Returns the configured dialog window.
    ==========================================
    """
    if parent is not None:
        dialog = tk.Toplevel(parent)
    else:
        dialog = tk.Tk()
    
    dialog.title(title)
    # Compute center coordinates here (inlined from previous helper).
    try:
        if parent is not None:
            # Ensure parent is fully mapped and realized before querying geometry
            parent.deiconify()
            parent.update()
            parent.update_idletasks()
            px = parent.winfo_rootx()
            py = parent.winfo_rooty()
            pw = parent.winfo_width()
            ph = parent.winfo_height()
            # If parent reports tiny size, fall back to screen center
            if pw <= 1 or ph <= 1:
                sw = dialog.winfo_screenwidth()
                sh = dialog.winfo_screenheight()
                x = (sw - width) // 2
                y = (sh - height) // 2
            else:
                x = px + (pw - width) // 2
                y = py + (ph - height) // 2
        else:
            sw = dialog.winfo_screenwidth()
            sh = dialog.winfo_screenheight()
            x = (sw - width) // 2
            y = (sh - height) // 2
    except Exception:
        x, y = 100, 100

    dialog.geometry(f"{width}x{height}+{x}+{y}")
    
    # REMOVE TITLEBAR (no X, no frame, no native drag)
    dialog.overrideredirect(True)

    # Apply transient AFTER geometry is set (important for proper positioning)
    if parent is not None:
        dialog.transient(parent)
    
    return dialog


def _make_dialog_modal_and_wait(dialog, parent):
    """
    ==========================================
    Make a dialog modal and wait for user interaction.
    Handles both parent-relative and standalone dialogs.
    ==========================================
    """
    try:
        dialog.lift()
        dialog.attributes("-topmost", True)
    except Exception:
        pass

    if parent is not None:
        dialog.grab_set_global()
        dialog.focus_set()
        parent.wait_window(dialog)
    else:
        dialog.mainloop()


def select_camera_gui(UVC_devices, parent=None):
    """
    ==========================================
    Opens a small modal dialog (Toplevel) with a dropdown list of camera names.
    If `parent` is provided (a Tk or Toplevel), the dialog will be centered
    over it and be modal/always-on-top so it doesn't appear behind the main GUI.

    Returns (device_path, camera_format, request_rgb24) where camera_format is a CameraFormat NamedTuple.
    ==========================================
    """

    # We now track both the camera and the specific format chosen
    selected_data = {
        "camera": None,
        "format": None,
        "device_path": None,
        "camera_format": None,
        "request_rgb24": False,
    }

    def on_select():
        """
        ==========================================
        Store current combobox selections and close the dialog.
        ==========================================
        """
        camera_idx = combo_UVC_name.current()
        format_idx = combo_UVC_format.current()
        
        if camera_idx != -1 and format_idx != -1:
            selected_camera = UVC_devices[camera_idx]
            selected_data["device_path"] = selected_camera.path
            selected_format = selected_camera.formats[format_idx]
            selected_data["camera_format"] = selected_format
            selected_data["request_rgb24"] = bool(request_rgb24_var.get())
        
        dialog.destroy()

    def on_cancel_exit():
        """
        ==========================================
        Close parent/dialog and terminate application flow.
        ==========================================
        """
        try:
            if parent is not None:
                parent.destroy()
        finally:
            dialog.destroy()
            sys.exit(0)

    def update_formats(event):
        """
        ==========================================
        Refresh format combobox when a different camera is selected.
        ==========================================
        """
        selected_idx = combo_UVC_name.current()
        if selected_idx != -1:
            # New dot-notation access for CameraDeviceInfo
            selected_camera_data = UVC_devices[selected_idx]
            raw_formats = selected_camera_data.formats or []
            
            # New dot-notation access for CameraFormat
            display_formats = [
                f"{f.width} x {f.height} @ {f.fps:.2f} FPS ({f.pixel_format})"
                for f in raw_formats
            ]
            
            combo_UVC_format['values'] = display_formats
            
            if display_formats:
                combo_UVC_format.current(0)
            else:
                combo_UVC_format.set("No formats available")

    # The height is increased slightly (180) to accommodate the second box comfortably
    dialog = _create_and_setup_dialog(parent, "Select Camera", 400, 180)

    label = ttk.Label(dialog, text="Choose a camera:")
    label.pack(pady=(10, 0))

    # New dot-notation: device.name
    names_only = [device.name for device in UVC_devices]
    combo_UVC_name = ttk.Combobox(dialog, values=names_only, state="readonly", width=45)
    combo_UVC_name.pack(pady=5)

    label_fmt = ttk.Label(dialog, text="Choose a resolution/format:")
    label_fmt.pack()

    combo_UVC_format = ttk.Combobox(dialog, state="readonly", width=45)
    combo_UVC_format.pack(pady=5)
    
    request_rgb24_var = tk.BooleanVar(value=False)
    request_rgb24_checkbox = ttk.Checkbutton(
        dialog,
        text="Request RGB24",
        variable=request_rgb24_var
    )
    request_rgb24_checkbox.pack(pady=(0, 6))

    combo_UVC_name.bind("<<ComboboxSelected>>", update_formats)

    if UVC_devices:
        combo_UVC_name.current(0)
        update_formats(None) 

    btn_frame = tk.Frame(dialog)
    btn_frame.pack(pady=10)

    button = ttk.Button(btn_frame, text="Select", command=on_select)
    button.pack(side="left", padx=8)

    exit_btn = ttk.Button(btn_frame, text="Cancel and Exit", command=on_cancel_exit)
    exit_btn.pack(side="left", padx=8)

    _make_dialog_modal_and_wait(dialog, parent)

    return selected_data["device_path"], selected_data["camera_format"], selected_data["request_rgb24"]


def show_no_camera_dialog(parent=None):
    """
    ==========================================
    Show a modal retry/cancel dialog informing the user no cameras were found.
    Returns True if the user chose Retry, False if Cancel.
    ==========================================
    """

    result = {"retry": False}

    def on_retry():
        """
        ==========================================
        Mark retry choice and close dialog.
        ==========================================
        """
        result["retry"] = True
        dialog.destroy()

    def on_cancel():
        """
        ==========================================
        Mark cancel choice and close dialog.
        ==========================================
        """
        result["retry"] = False
        dialog.destroy()

    dialog = _create_and_setup_dialog(parent, "No Camera Found", 360, 120)

    label = ttk.Label(dialog, text="No cameras detected. Please plug in a camera and Retry, or Cancel to exit.", wraplength=320, justify="center")
    label.pack(pady=(20, 10), padx=10)

    btn_frame = tk.Frame(dialog)
    btn_frame.pack(pady=8)

    retry_btn = ttk.Button(btn_frame, text="Retry", command=on_retry)
    retry_btn.pack(side="left", padx=8)

    cancel_btn = ttk.Button(btn_frame, text="Cancel", command=on_cancel)
    cancel_btn.pack(side="left", padx=8)

    _make_dialog_modal_and_wait(dialog, parent)

    return result["retry"]


class MainGUI:
    def __init__(self):
        """
        ==========================================
        Initialize the main window, layout, and control widgets.
        ==========================================
        """
        self.root = tk.Tk()
        self.root.title("Camera Control Panel")
        self.root.geometry("1000x700")
        self.camera = None
        self.device_path = None

        # layout frames
        self.video_frame = tk.Frame(self.root)
        self.video_frame.pack(side="left", fill="both", expand=True)

        self.controls_frame = tk.Frame(self.root)
        self.controls_frame.pack(side="right", fill="y")

        self.controls_canvas = tk.Canvas(self.controls_frame, highlightthickness=0)
        self.controls_scrollbar = ttk.Scrollbar(self.controls_frame, orient="vertical", command=self.controls_canvas.yview)
        self.controls_canvas.configure(yscrollcommand=self.controls_scrollbar.set)

        self.controls_scrollbar.pack(side="right", fill="y")
        self.controls_canvas.pack(side="left", fill="both", expand=True)

        self.controls_content_frame = tk.Frame(self.controls_canvas)
        self.controls_canvas_window = self.controls_canvas.create_window((0, 0), window=self.controls_content_frame, anchor="nw")

        self.controls_content_frame.bind(
            "<Configure>",
            lambda event: self.controls_canvas.configure(scrollregion=self.controls_canvas.bbox("all"))
        )
        self.controls_canvas.bind(
            "<Configure>",
            lambda event: self.controls_canvas.itemconfigure(self.controls_canvas_window, width=event.width)
        )

        # A LabelFrame is the Tkinter equivalent of a .NET GroupBox
        self.settings_group = ttk.LabelFrame(self.controls_content_frame, text="Camera Controls")
        self.settings_group.pack(padx=10, pady=10, fill="x", anchor="n")

        self.current_format_var = tk.StringVar(value="Current format: N/A")
        self.current_format_label = tk.Label(
            self.settings_group,
            textvariable=self.current_format_var,
            wraplength=260,
            justify="left",
            fg="black"
        )
        self.current_format_label.pack(padx=10, pady=(10, 4), fill="x")

        self.current_fps_var = tk.StringVar(value="FPS Ingest: --    FPS Render: --    FPS (.NET): --")
        self.current_fps_label = tk.Label(
            self.settings_group,
            textvariable=self.current_fps_var,
            justify="left",
            fg="black"
        )
        self.current_fps_label.pack(padx=10, pady=(0, 6), fill="x")

        self._ingest_fps_window_start = time.perf_counter()
        self._ingest_frame_count = 0
        self._ingest_fps_value = None
        self._render_fps_window_start = time.perf_counter()
        self._render_frame_count = 0
        self._render_fps_value = None
        self._dotnet_fps_value = None
        self._last_ingest_time = None
        self._last_render_time_for_fps = None
        self._fps_has_measurement = False
        self._fps_stale_timeout_sec = 1.5
        self.root.after(500, self._refresh_fps_display_state)

        self.format_button = ttk.Button(
            self.settings_group,
            text="Camera Format Options",
            command=self.show_camera_format_options,
            state="disabled"
        )
        self.format_button.pack(padx=10, pady=(4, 10), fill="x")

        self.reset_settings_button = ttk.Button(
            self.settings_group,
            text="Reset Settings",
            command=self.show_reset_settings_options,
            state="disabled"
        )
        self.reset_settings_button.pack(padx=10, pady=(0, 10), fill="x")

        self.auto_mode_title = ttk.Label(self.settings_group, text="Auto/Manual Controls")
        self.auto_mode_title.pack(padx=10, pady=(0, 4), anchor="w")

        self.auto_mode_controls_frame = tk.Frame(self.settings_group)
        self.auto_mode_controls_frame.pack(padx=10, pady=(0, 10), fill="x")

        self.auto_mode_vars = {}
        self.auto_mode_checkbuttons = {}

        self.property_value_controls_frame = tk.Frame(self.settings_group)
        self.property_value_controls_frame.pack(padx=10, pady=(0, 10), fill="x")
        self.property_slider_vars = {}
        self.property_sliders = {}
        self.property_value_labels = {}
        self._updating_property_sliders = set()
        
        # You can now add widgets to self.settings_group instead of self.controls_frame

        # placeholder for video canvas
        self.canvas = tk.Canvas(self.video_frame, width=640, height=480, bg="black")
        self.canvas.pack(fill="both", expand=True)
        self.canvas.bind("<Configure>", self._on_video_canvas_configure)
        
        # To prevent garbage collection of the image
        self.current_image = None
        self.canvas_image_id = None

        # Frame rendering backpressure:
        # keep only the latest frame and ensure max one pending Tk render callback.
        self._latest_bgr_frame = None
        self._render_pending = False
        self._target_ui_fps = 30.0
        self._last_render_time = 0.0
        self._last_canvas_resize_time = 0.0
        self._resize_settle_seconds = 0.30
        self._preview_cap_enabled = True
        self._preview_max_width = 1280
        self._preview_max_height = 720

    def bind_camera(self, camera, device_path):
        """
        ==========================================
        Bind active camera instance and device path to GUI controls.
        ==========================================
        """
        self.camera = camera
        self.device_path = device_path
        self.format_button.configure(state="normal")
        self.reset_settings_button.configure(state="normal")
        self._ingest_fps_value = None
        self._render_fps_value = None
        self._dotnet_fps_value = None
        self._update_fps_label()
        self._ingest_fps_window_start = time.perf_counter()
        self._ingest_frame_count = 0
        self._render_fps_window_start = time.perf_counter()
        self._render_frame_count = 0
        self._last_ingest_time = None
        self._last_render_time_for_fps = None
        self._fps_has_measurement = False
        self._refresh_current_format_label()
        self._refresh_auto_mode_controls()
        self._refresh_property_value_controls()

    def _update_fps_label(self):
        """
        =====================
        Refresh combined FPS label for Python-side and .NET-side values.
        =====================
        """
        ingest_text = "--" if self._ingest_fps_value is None else f"{float(self._ingest_fps_value):.1f}"
        render_text = "--" if self._render_fps_value is None else f"{float(self._render_fps_value):.1f}"
        dotnet_text = "--" if self._dotnet_fps_value is None else f"{float(self._dotnet_fps_value):.1f}"
        self.current_fps_var.set(
            f"FPS Ingest (Py): {ingest_text}    FPS Render (Py): {render_text}    FPS (.NET): {dotnet_text}"
        )

    @staticmethod
    def _show_reset_failure_message(action_title, success_count, total_count):
        """
        ==========================================
        Show one aggregated failure message for a reset action.
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

        messagebox.showerror("Reset Failed", message)

    def show_reset_settings_options(self):
        """
        ==========================================
        Show reset actions dialog for properties and property flags.
        ==========================================
        """
        if self.camera is None:
            return

        dialog = _create_and_setup_dialog(self.root, "Reset Settings", 400, 180)

        title_label = ttk.Label(dialog, text="Choose reset action", justify="center")
        title_label.pack(pady=(12, 8), padx=12)

        btn_frame = tk.Frame(dialog)
        btn_frame.pack(padx=12, pady=(4, 12), fill="x")

        reset_properties_btn = ttk.Button(btn_frame, text="Reset Properties")
        reset_properties_btn.pack(fill="x", pady=(0, 8))

        reset_flags_btn = ttk.Button(btn_frame, text="Reset Property Flags")
        reset_flags_btn.pack(fill="x")

        def on_reset_properties():
            all_success, reset_count, total_supported = self.camera.reset_all_properties_to_default_values()

            if all_success:
                reset_properties_btn.configure(text="Reset Properties - Success", state="disabled")
                self.current_format_label.configure(fg="green")
                self._refresh_property_value_controls()
                return

            self.current_format_label.configure(fg="red")
            self._refresh_property_value_controls()
            self._show_reset_failure_message("Reset Properties", reset_count, total_supported)

        def on_reset_flags():
            all_success, updated_count, total_auto_supported = self.camera.reset_all_property_flags()

            if all_success:
                reset_flags_btn.configure(text="Reset Property Flags - Success", state="disabled")
                self.current_format_label.configure(fg="green")
                self._refresh_auto_mode_controls()
                self._refresh_property_value_controls()
                return

            self.current_format_label.configure(fg="red")
            self._refresh_auto_mode_controls()
            self._refresh_property_value_controls()
            self._show_reset_failure_message("Reset Property Flags", updated_count, total_auto_supported)

        reset_properties_btn.configure(command=on_reset_properties)
        reset_flags_btn.configure(command=on_reset_flags)

        close_btn = ttk.Button(dialog, text="Close", command=dialog.destroy)
        close_btn.pack(pady=(0, 12))

        _make_dialog_modal_and_wait(dialog, self.root)

    def _clear_auto_mode_controls(self):
        """
        ==========================================
        Remove existing auto/manual control checkboxes from the UI.
        ==========================================
        """
        for widget in self.auto_mode_controls_frame.winfo_children():
            widget.destroy()
        self.auto_mode_vars = {}
        self.auto_mode_checkbuttons = {}

    def _on_auto_mode_toggle(self, property_name, var):
        """
        ==========================================
        Handle user toggling of one auto/manual property checkbox.
        ==========================================
        """
        if self.camera is None:
            return

        requested_auto_on = bool(var.get())
        success, is_auto_enabled = self.camera.set_property_auto_mode(property_name, requested_auto_on)
        var.set(bool(is_auto_enabled))

        if success:
            self.current_format_label.configure(fg="green")
        else:
            self.current_format_label.configure(fg="red")

        self._refresh_property_value_controls()

    def _refresh_property_value_controls(self):
        """
        ==========================================
        Rebuild property sliders from the camera property ranges.
        ==========================================
        """
        for widget in self.property_value_controls_frame.winfo_children():
            widget.destroy()

        self.property_slider_vars = {}
        self.property_sliders = {}
        self.property_value_labels = {}
        self._updating_property_sliders = set()

        section_title = ttk.Label(self.property_value_controls_frame, text="Property Controls")
        section_title.pack(anchor="w")

        if self.camera is None:
            unavailable_label = ttk.Label(self.property_value_controls_frame, text="Property controls not available")
            unavailable_label.pack(anchor="w", pady=(2, 0))
            return

        ranges = self.camera.property_ranges or {}
        supported_properties = [
            (name, camera_range)
            for name, camera_range in ranges.items()
            if camera_range.property_supported
        ]

        if not supported_properties:
            unavailable_label = ttk.Label(self.property_value_controls_frame, text="Property controls not available")
            unavailable_label.pack(anchor="w", pady=(2, 0))
            return

        for property_name, property_range in sorted(supported_properties, key=lambda x: str(x[0]).lower()):
            label = ttk.Label(self.property_value_controls_frame, text=str(property_name))
            label.pack(anchor="w", pady=(6, 0))

            min_value = float(property_range.min)
            max_value = float(property_range.max)
            step_value = float(property_range.step) if float(property_range.step) > 0 else 1.0
            current_value = float(property_range.current)

            slider_var = tk.DoubleVar(value=current_value)
            slider = tk.Scale(
                self.property_value_controls_frame,
                from_=min_value,
                to=max_value,
                orient="horizontal",
                showvalue=False,
                resolution=step_value,
                variable=slider_var,
                command=lambda raw_value, n=property_name: self._on_property_slider_change(n, raw_value)
            )
            slider.pack(fill="x", pady=(2, 0))

            min_max_label = ttk.Label(
                self.property_value_controls_frame,
                text=f"Min: {int(min_value)}    Max: {int(max_value)}    Value: {int(current_value)}"
            )
            min_max_label.pack(anchor="w", pady=(2, 0))

            slider_state = "disabled" if (property_range.auto_supported and bool(property_range.is_auto)) else "normal"
            slider.configure(state=slider_state)

            property_key = str(property_name)
            self.property_slider_vars[property_key] = slider_var
            self.property_sliders[property_key] = slider
            self.property_value_labels[property_key] = min_max_label

    def _on_property_slider_change(self, property_name, raw_value):
        """
        ==========================================
        Handle property slider movement and apply snapped value based on API step.
        ==========================================
        """
        if self.camera is None:
            return

        property_key = str(property_name)
        if property_key in self._updating_property_sliders:
            return

        ranges = self.camera.property_ranges or {}
        selected_range = None
        for name, camera_range in ranges.items():
            if str(name).lower() == str(property_name).lower():
                selected_range = camera_range
                break

        if selected_range is None:
            return

        min_value = float(selected_range.min)
        max_value = float(selected_range.max)
        step_value = float(selected_range.step) if float(selected_range.step) > 0 else 1.0
        raw_numeric = float(raw_value)

        snapped_value = min_value + round((raw_numeric - min_value) / step_value) * step_value
        snapped_value = max(min_value, min(max_value, snapped_value))
        target_value = int(round(snapped_value))

        success, actual_value = self.camera.set_property_value(str(property_name), target_value)

        slider_var = self.property_slider_vars.get(property_key)
        if slider_var is not None:
            self._updating_property_sliders.add(property_key)
            slider_var.set(actual_value)
            self._updating_property_sliders.discard(property_key)

        value_label = self.property_value_labels.get(property_key)
        if value_label is not None:
            value_label.configure(
                text=f"Min: {int(min_value)}    Max: {int(max_value)}    Value: {int(actual_value)}"
            )

        if success:
            self.current_format_label.configure(fg="green")
        else:
            self.current_format_label.configure(fg="red")

    def _refresh_auto_mode_controls(self):
        """
        ==========================================
        Rebuild auto/manual controls from the camera property cache.
        ==========================================
        """
        self._clear_auto_mode_controls()

        if self.camera is None:
            return

        ranges = self.camera.property_ranges or {}
        supported_auto_properties = [
            (name, camera_range)
            for name, camera_range in ranges.items()
            if camera_range.property_supported and camera_range.auto_supported
        ]

        if not supported_auto_properties:
            no_controls_label = ttk.Label(self.auto_mode_controls_frame, text="No auto/manual controls available")
            no_controls_label.pack(anchor="w")
            return

        for property_name, camera_range in sorted(supported_auto_properties, key=lambda x: x[0].lower()):
            var = tk.BooleanVar(value=bool(camera_range.is_auto))
            checkbox = ttk.Checkbutton(
                self.auto_mode_controls_frame,
                text=f"{property_name} Auto",
                variable=var,
                command=lambda n=property_name, v=var: self._on_auto_mode_toggle(n, v)
            )
            checkbox.pack(anchor="w", pady=1)
            self.auto_mode_vars[property_name] = var
            self.auto_mode_checkbuttons[property_name] = checkbox

    @staticmethod
    def _format_to_display_text(camera_format):
        """
        ==========================================
        Format camera mode details into a user-friendly label string.
        ==========================================
        """
        return f"{camera_format.width} x {camera_format.height} @ {camera_format.fps:.2f} FPS ({camera_format.pixel_format})"

    def _refresh_current_format_label(self, format_change_succeeded=None):
        """
        ==========================================
        Refresh current format text and status color.
        ==========================================
        """
        if self.camera is None or self.camera.current_format is None:
            self.current_format_var.set("Current format: N/A")
            self.current_format_label.configure(fg="black")
            return

        current_text = self._format_to_display_text(self.camera.current_format)
        self.current_format_var.set(f"Current format: {current_text}")
        if format_change_succeeded is True:
            self.current_format_label.configure(fg="green")
        elif format_change_succeeded is False:
            self.current_format_label.configure(fg="red")
        else:
            self.current_format_label.configure(fg="black")

    def show_camera_format_options(self):
        """
        ==========================================
        Show available formats and allow switching to a different one.
        ==========================================
        """
        if self.camera is None or self.device_path is None:
            return

        available_formats = []
        try:
            available_formats = self.camera.get_camera_formats(self.device_path) or []
        except Exception:
            available_formats = []

        if not available_formats:
            available_formats = self.camera.available_formats or []
        else:
            self.camera.available_formats = available_formats

        if not available_formats:
            return

        current_format = self.camera.current_format

        dialog = _create_and_setup_dialog(self.root, "Camera Format Options", 400, 160)

        current_text = "Current format: "
        if current_format is not None:
            current_text += self._format_to_display_text(current_format)
        else:
            current_text += "Unknown"

        current_label = ttk.Label(dialog, text=current_text, wraplength=300, justify="center")
        current_label.pack(pady=(12, 8), padx=12)

        display_formats = [self._format_to_display_text(fmt) for fmt in available_formats]
        combo_formats = ttk.Combobox(dialog, values=display_formats, state="readonly", width=65)
        combo_formats.pack(pady=6, padx=12)

        current_rgb24_request = bool(getattr(self.camera, "_request_rgb24_conversion", False))
        request_rgb24_var = tk.BooleanVar(value=current_rgb24_request)
        request_rgb24_checkbox = ttk.Checkbutton(
            dialog,
            text="Request RGB24",
            variable=request_rgb24_var
        )
        request_rgb24_checkbox.pack(pady=(0, 6), padx=12)

        default_index = 0
        if current_format is not None:
            for idx, fmt in enumerate(available_formats):
                if fmt == current_format:
                    default_index = idx
                    break
        combo_formats.current(default_index)

        def on_apply():
            """
            =====================
            Apply the selected format and refresh dependent controls.
            =====================
            """
            selected_idx = combo_formats.current()
            if selected_idx == -1:
                dialog.destroy()
                return

            selected_format = available_formats[selected_idx]
            target_format = selected_format
            request_rgb24 = bool(request_rgb24_var.get())
            dialog.destroy()

            self.current_format_label.configure(fg="black")

            def apply_format_in_background():
                format_changed = False
                try:
                    format_changed = bool(
                        self.camera.set_format(
                            target_format,
                            request_rgb24_conversion=request_rgb24
                        )
                    )
                except Exception:
                    format_changed = False

                def update_after_apply():
                    self._refresh_current_format_label(format_changed)
                    self._refresh_auto_mode_controls()
                    self._refresh_property_value_controls()

                self.root.after(0, update_after_apply)

            threading.Thread(target=apply_format_in_background, daemon=True).start()

        def on_close():
            """
            =====================
            Close the format options dialog without changes.
            =====================
            """
            dialog.destroy()

        btn_frame = tk.Frame(dialog)
        btn_frame.pack(pady=10)

        apply_btn = ttk.Button(btn_frame, text="Apply", command=on_apply)
        apply_btn.pack(side="left", padx=8)

        close_btn = ttk.Button(btn_frame, text="Close", command=on_close)
        close_btn.pack(side="left", padx=8)

        _make_dialog_modal_and_wait(dialog, self.root)

    def run(self):
        """
        ==========================================
        Start the Tkinter main event loop.
        ==========================================
        """
        self.root.mainloop()

    def update_video_frame(self, success, frame):
        """
        =====================
        Callback to be passed to the Camera class.
        Handles thread-safety by scheduling the UI update on the main loop.
        =====================
        """
        # print(f"GUI received frame: success={success}, shape={frame.shape if frame is not None else 'None'}")
        
        if not success or frame is None:
            return

        self._ingest_frame_count += 1
        now = time.perf_counter()
        self._last_ingest_time = now
        ingest_elapsed = now - self._ingest_fps_window_start
        if ingest_elapsed >= 0.5:
            self._ingest_fps_value = self._ingest_frame_count / ingest_elapsed
            self._ingest_fps_window_start = now
            self._ingest_frame_count = 0
            self._fps_has_measurement = True

        # Keep only the newest frame in BGR. Conversion happens only for frames that are rendered.
        self._latest_bgr_frame = frame
        if not self._render_pending:
            self._render_pending = True
            self.root.after(0, self._drain_latest_frame)

    def _on_video_canvas_configure(self, event):
        """
        =====================
        Track canvas resize activity so render path can use cheaper scaling while resizing.
        =====================
        """
        self._last_canvas_resize_time = time.perf_counter()

    def _drain_latest_frame(self):
        """
        =====================
        Render at most one frame callback at a time and coalesce burst updates.
        =====================
        """
        now = time.perf_counter()
        min_render_interval = 1.0 / self._target_ui_fps if self._target_ui_fps > 0 else 0.0
        elapsed_since_last_render = now - self._last_render_time
        if elapsed_since_last_render < min_render_interval:
            remaining_ms = max(1, int((min_render_interval - elapsed_since_last_render) * 1000))
            self.root.after(remaining_ms, self._drain_latest_frame)
            return

        frame_to_render = self._latest_bgr_frame
        if frame_to_render is None:
            self._render_pending = False
            return

        self._latest_bgr_frame = None
        self._update_canvas_safe(frame_to_render)
        self._last_render_time = time.perf_counter()

        if self._latest_bgr_frame is not None:
            self.root.after(0, self._drain_latest_frame)
        else:
            self._render_pending = False

    def _update_canvas_safe(self, bgr_frame):
        """
        =====================
        Actual UI update running on the main thread.
        =====================
        """
        # Get canvas dimensions
        canvas_width = self.canvas.winfo_width()
        canvas_height = self.canvas.winfo_height()
        
        # Use configured size if canvas not yet rendered
        if canvas_width <= 1:
            canvas_width = 640
        if canvas_height <= 1:
            canvas_height = 480

        target_width = canvas_width
        target_height = canvas_height
        if self._preview_cap_enabled:
            target_width = min(target_width, int(self._preview_max_width))
            target_height = min(target_height, int(self._preview_max_height))
        
        # Convert to RGB only for frames that are actually rendered.
        rgb_frame = cv2.cvtColor(bgr_frame, cv2.COLOR_BGR2RGB)
        pil_image = Image.fromarray(rgb_frame)
        
        # Downscale-only fit while maintaining aspect ratio.
        # If target area is larger than source, keep source size (no upscaling).
        img_width, img_height = pil_image.size
        width_scale = float(target_width) / float(img_width)
        height_scale = float(target_height) / float(img_height)
        downscale_factor = min(width_scale, height_scale, 1.0)
        new_width = max(1, int(img_width * downscale_factor))
        new_height = max(1, int(img_height * downscale_factor))
        
        if new_width != img_width or new_height != img_height:
            now = time.perf_counter()
            is_resizing = (now - self._last_canvas_resize_time) < self._resize_settle_seconds
            resample_filter = Image.NEAREST if is_resizing else Image.BILINEAR
            pil_image = pil_image.resize((new_width, new_height), resample_filter)
        imgtk = ImageTk.PhotoImage(image=pil_image)
        
        # Keep a reference to prevent GC
        self.current_image = imgtk
        
        # Center the image on canvas
        x_offset = (canvas_width - new_width) // 2
        y_offset = (canvas_height - new_height) // 2
        if self.canvas_image_id is None:
            self.canvas_image_id = self.canvas.create_image(x_offset, y_offset, anchor="nw", image=imgtk)
        else:
            self.canvas.coords(self.canvas_image_id, x_offset, y_offset)
            self.canvas.itemconfig(self.canvas_image_id, image=imgtk)

        self._render_frame_count += 1
        now = time.perf_counter()
        self._last_render_time_for_fps = now
        render_elapsed = now - self._render_fps_window_start
        if render_elapsed >= 0.5:
            self._render_fps_value = float(self._render_frame_count / render_elapsed)
            self._update_fps_label()
            self._fps_has_measurement = True
            self._render_fps_window_start = now
            self._render_frame_count = 0

    def _refresh_fps_display_state(self):
        """
        =====================
        Reset FPS label when stream is stale and keep periodic watchdog running.
        =====================
        """
        now = time.perf_counter()
        latest_activity_time = None
        if self._last_ingest_time is not None:
            latest_activity_time = self._last_ingest_time
        if self._last_render_time_for_fps is not None:
            if latest_activity_time is None or self._last_render_time_for_fps > latest_activity_time:
                latest_activity_time = self._last_render_time_for_fps

        if latest_activity_time is not None:
            if (now - latest_activity_time) >= self._fps_stale_timeout_sec and self._fps_has_measurement:
                self._ingest_fps_value = None
                self._render_fps_value = None
                self._fps_has_measurement = False
                self._ingest_frame_count = 0
                self._render_frame_count = 0
                self._ingest_fps_window_start = now
                self._render_fps_window_start = now

        if self.camera is not None:
            try:
                dotnet_fps = float(self.camera.get_current_fps())
                self._dotnet_fps_value = dotnet_fps if dotnet_fps > 0 else None
            except Exception:
                self._dotnet_fps_value = None
        else:
            self._dotnet_fps_value = None

        self._update_fps_label()

        self.root.after(500, self._refresh_fps_display_state)
        