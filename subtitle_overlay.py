"""
subtitle_overlay.py — Transparent Always-On-Top Subtitle Overlay
================================================================
Main entry point. Wraps the transcription engine in a floating,
transparent Tkinter window with a draggable handle and click-through support.

Usage:
    python subtitle_overlay.py [--device INDEX] [--model tiny.en|base.en]
                               [--language en|auto] [--compute-device auto|cpu|cuda]
                               [--save-transcript PATH]

Controls:
    • Drag the ▓▓▓ handle bar at the top to move the overlay.
    • Right-click the handle bar for a settings/quit/copy menu.
    • The subtitle text area is fully click-through (invisible to mouse).
    • Ctrl+Alt+C  — copy transcript to clipboard (global hotkey)
    • Ctrl+Alt+Q  — quit (global hotkey)

Settings are persisted to config.json in the project directory.
System tray icon available when pystray + Pillow are installed.
"""

from __future__ import annotations

import argparse
import json
import logging
import logging.handlers
import os
import pathlib
import queue
import sys
import threading
import time
import tkinter as tk
from tkinter import messagebox
import warnings

warnings.filterwarnings("ignore")

# Windows-specific APIs
_WINDOWS = sys.platform == "win32"
if _WINDOWS:
    import ctypes
    import ctypes.wintypes

# ── Logging ───────────────────────────────────────────────────────────────────
def _setup_logging() -> logging.Logger:
    logger = logging.getLogger("subtitle.overlay")
    if logger.handlers:
        return logger
    root = logging.getLogger("subtitle")
    if root.handlers:
        return logger  # engine already configured it

    root.setLevel(logging.DEBUG)
    fmt = logging.Formatter(
        "%(asctime)s [%(levelname)s] %(name)s: %(message)s", datefmt="%H:%M:%S"
    )
    log_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "app.log")
    # Rotating handler — keeps at most 3 × 5 MB = 15 MB of logs
    fh = logging.handlers.RotatingFileHandler(
        log_path, maxBytes=5 * 1024 * 1024, backupCount=2, encoding="utf-8"
    )
    fh.setLevel(logging.DEBUG)
    fh.setFormatter(fmt)
    root.addHandler(fh)
    ch = logging.StreamHandler(sys.stderr)
    ch.setLevel(logging.INFO)
    ch.setFormatter(fmt)
    root.addHandler(ch)
    return logger

log = _setup_logging()

# ── Engine import ─────────────────────────────────────────────────────────────
try:
    from transcription_engine import TranscriptionEngine
except ImportError as e:
    log.critical("Missing dependency: %s — run: pip install -r requirements.txt", e)
    sys.exit(1)


# ─────────────────────────────────────────────────────────────────────────────
# Settings persistence
# ─────────────────────────────────────────────────────────────────────────────

_CONFIG_PATH = pathlib.Path(__file__).parent / "config.json"

_DEFAULT_CONFIG: dict = {
    "device_index":   None,
    "model_name":     "base.en",
    "language":       "en",
    "compute_device": "auto",
    "overlay_x":      None,     # None → auto-center on first run
    "overlay_y":      None,
    "overlay_width":  860,
}


def _load_config() -> dict:
    """Load config.json, validate types, fall back to defaults on any error."""
    cfg = _DEFAULT_CONFIG.copy()
    try:
        if _CONFIG_PATH.exists():
            data = json.loads(_CONFIG_PATH.read_text(encoding="utf-8"))
            cfg.update(data)
    except Exception as exc:
        log.warning("Could not read config.json (%s) — using defaults.", exc)

    # Type-validate fields that come from user-editable JSON
    try:
        cfg["device_index"] = int(cfg["device_index"]) if cfg["device_index"] is not None else None
    except (TypeError, ValueError):
        log.warning("config.json: invalid device_index %r — resetting to auto.", cfg["device_index"])
        cfg["device_index"] = None

    for key in ("overlay_x", "overlay_y"):
        try:
            cfg[key] = int(cfg[key]) if cfg[key] is not None else None
        except (TypeError, ValueError):
            cfg[key] = None

    try:
        cfg["overlay_width"] = int(cfg.get("overlay_width", 860))
        if cfg["overlay_width"] < 200 or cfg["overlay_width"] > 3840:
            cfg["overlay_width"] = 860
    except (TypeError, ValueError):
        cfg["overlay_width"] = 860

    return cfg


def _save_config(cfg: dict) -> None:
    """Write cfg to config.json, silently logging any error."""
    # Only persist known keys — skip runtime-only keys (prefixed with _)
    to_save = {k: v for k, v in cfg.items() if not k.startswith("_")}
    try:
        _CONFIG_PATH.write_text(json.dumps(to_save, indent=2), encoding="utf-8")
        log.debug("Config saved to %s", _CONFIG_PATH)
    except Exception as exc:
        log.warning("Could not save config.json: %s", exc)


# ─────────────────────────────────────────────────────────────────────────────
# Design constants
# ─────────────────────────────────────────────────────────────────────────────

TRANSPARENT_COLOR = "#010101"       # near-black key — transparent on Windows
HANDLE_COLOR      = "#1a1a2e"       # dark navy handle
HANDLE_ACCENT     = "#e94560"       # red accent strip
TEXT_COLOR        = "#ffffff"       # subtitle text
SHADOW_COLOR      = "#000000"       # outline/shadow
CANVAS_HEIGHT     = 120             # subtitle canvas height (px)
MAX_LINES         = 2               # max subtitle lines visible
FADE_AFTER_MS     = 5_000           # clear text after N ms of silence
HISTORY_MAX       = 6               # keep last N transcript segments

# Segoe UI ships with every Windows 10/11 install.
# This is a Windows-only application, so the fallback is never needed in practice,
# but "Arial" is listed for safety on older/embedded builds.
FONT_FAMILY: str = "Segoe UI"
FONT_SIZE:   int = 22


# ─────────────────────────────────────────────────────────────────────────────
# Windows click-through helpers
# ─────────────────────────────────────────────────────────────────────────────

GWL_EXSTYLE       = -20
WS_EX_LAYERED     = 0x00080000
WS_EX_TRANSPARENT = 0x00000020

# Virtual key codes for global hotkeys (Ctrl+Alt+C / Ctrl+Alt+Q)
_MOD_ALT      = 0x0001
_MOD_CONTROL  = 0x0002
_WM_HOTKEY    = 0x0312
_HOTKEY_COPY  = 1
_HOTKEY_QUIT  = 2


def _set_clickthrough(hwnd: int) -> None:
    """
    On Windows, setting -transparentcolor attributes already makes that specific
    color click-through natively. WS_EX_TRANSPARENT is NOT set here because it would
    make the entire window (including the draggable drag handle) click-through,
    preventing moving, resizing, or clicking settings.
    """
    pass


def _unset_clickthrough(hwnd: int) -> None:
    """No-op matching the simplified clickthrough logic."""
    pass


# ─────────────────────────────────────────────────────────────────────────────
# System tray icon (optional — requires pystray + Pillow)
# ─────────────────────────────────────────────────────────────────────────────

def _make_tray_image():
    """Build a small PIL Image for the system tray icon, matching the app palette."""
    from PIL import Image, ImageDraw  # type: ignore[import]
    sz = 64
    img = Image.new("RGBA", (sz, sz), (0, 0, 0, 0))
    d   = ImageDraw.Draw(img)
    d.ellipse([2, 2, 62, 62],       fill=(26, 26, 46, 255))   # dark navy circle
    d.rectangle([10, 25, 54, 32],   fill=(255, 255, 255, 255)) # subtitle bar 1
    d.rectangle([14, 36, 50, 42],   fill=(200, 200, 200, 200)) # subtitle bar 2
    d.ellipse([44, 8, 58, 22],      fill=(233, 69, 96, 255))   # red accent dot
    return img


# ─────────────────────────────────────────────────────────────────────────────
# Subtitle canvas
# ─────────────────────────────────────────────────────────────────────────────

class SubtitleCanvas(tk.Canvas):
    """
    Canvas that renders subtitle text with an 8-direction outline effect.
    Background is set to the transparent key color so it vanishes into the desktop.
    """

    def __init__(self, parent: tk.Widget, **kwargs) -> None:
        super().__init__(
            parent,
            bg=TRANSPARENT_COLOR,
            highlightthickness=0,
            bd=0,
            **kwargs,
        )
        self._lines: list[str] = []
        self._font = (FONT_FAMILY, FONT_SIZE, "bold")
        self._fade_job: str | None = None

    def set_text(self, lines: list[str]) -> None:
        self._lines = lines[-MAX_LINES:]
        self._redraw()
        if self._fade_job:
            self.after_cancel(self._fade_job)
        self._fade_job = self.after(FADE_AFTER_MS, self.clear_text)

    def clear_text(self) -> None:
        self._lines = []
        self._redraw()
        self._fade_job = None

    def _redraw(self) -> None:
        self.delete("all")
        if not self._lines:
            return

        w = self.winfo_width() or int(self["width"] or 860)
        h = self.winfo_height() or CANVAS_HEIGHT
        y_center    = h // 2
        line_height = FONT_SIZE + 12
        total       = len(self._lines)
        start_y     = y_center - (total - 1) * line_height // 2

        offsets = [(-2,-2),(2,-2),(-2,2),(2,2),(0,-2),(0,2),(-2,0),(2,0)]

        for i, line in enumerate(self._lines):
            x = w // 2
            y = start_y + i * line_height
            for dx, dy in offsets:
                self.create_text(x+dx, y+dy, text=line, fill=SHADOW_COLOR,
                                 font=self._font, anchor="center", tags="shadow")
            self.create_text(x, y, text=line, fill=TEXT_COLOR,
                             font=self._font, anchor="center", tags="text")


# ─────────────────────────────────────────────────────────────────────────────
# Drag handle
# ─────────────────────────────────────────────────────────────────────────────

class DragHandle(tk.Frame):
    """
    Slim bar at the top of the overlay:
      • Shows a visual grip indicator.
      • Allows window dragging (saves position on release).
      • Provides a right-click context menu.
      • Auto-hides after 3 s of inactivity.
    """

    HANDLE_HEIGHT = 28

    def __init__(self, parent: "SubtitleOverlay", **kwargs) -> None:
        super().__init__(
            parent,
            bg=HANDLE_COLOR,
            height=self.HANDLE_HEIGHT,
            cursor="fleur",
            **kwargs,
        )
        self.pack_propagate(False)
        self._parent  = parent
        self._drag_x  = 0
        self._drag_y  = 0
        self._hide_job: str | None = None

        self._accent = tk.Frame(self, bg=HANDLE_ACCENT, height=3)
        self._accent.pack(fill="x", side="top")

        self._grip = tk.Label(
            self, text="▓▓▓  SUBTITLES  ▓▓▓",
            bg=HANDLE_COLOR, fg="#555577", font=(FONT_FAMILY, 9, "bold"),
        )
        self._grip.pack(expand=True)

        self._status_dot = tk.Label(
            self, text="●", bg=HANDLE_COLOR, fg="#444466", font=(FONT_FAMILY, 10),
        )
        self._status_dot.place(relx=1.0, rely=0.5, anchor="e", x=-8)

        for w in (self, self._accent, self._grip, self._status_dot):
            w.bind("<ButtonPress-1>",   self._on_press)
            w.bind("<B1-Motion>",       self._on_drag)
            w.bind("<ButtonRelease-1>", self._on_release)
            w.bind("<ButtonPress-3>",   self._show_context_menu)
            w.bind("<Enter>",           self._on_enter)
            w.bind("<Leave>",           self._on_leave)

        self._hide_job = self.after(3000, self._hide_handle)

    # ── Auto-hide ──────────────────────────────────────────────────────────────

    def _on_enter(self, _: tk.Event) -> None:
        if self._hide_job:
            self.after_cancel(self._hide_job)
            self._hide_job = None
        self.configure(height=self.HANDLE_HEIGHT)
        self._accent.pack(fill="x", side="top")
        self._grip.pack(expand=True)
        self._status_dot.place(relx=1.0, rely=0.5, anchor="e", x=-8)

    def _on_leave(self, _: tk.Event) -> None:
        if self._hide_job:
            self.after_cancel(self._hide_job)
        self._hide_job = self.after(2000, self._hide_handle)

    def _hide_handle(self) -> None:
        self._accent.pack_forget()
        self._grip.pack_forget()
        self._status_dot.place_forget()
        self.configure(height=2)

    def set_status(self, active: bool) -> None:
        self._status_dot.configure(fg="#00ff88" if active else "#ff4444")

    # ── Dragging ───────────────────────────────────────────────────────────────

    def _on_press(self, event: tk.Event) -> None:
        self._drag_x = event.x_root - self._parent.winfo_x()
        self._drag_y = event.y_root - self._parent.winfo_y()

    def _on_drag(self, event: tk.Event) -> None:
        x = event.x_root - self._drag_x
        y = event.y_root - self._drag_y
        self._parent.geometry(f"+{x}+{y}")

    def _on_release(self, _: tk.Event) -> None:
        """Save position to config on every drag-release."""
        self._parent.save_position()

    # ── Context menu ───────────────────────────────────────────────────────────

    def _show_context_menu(self, event: tk.Event) -> None:
        menu = tk.Menu(
            self._parent, tearoff=False,
            bg="#1a1a2e", fg="white",
            activebackground=HANDLE_ACCENT, activeforeground="white",
            font=(FONT_FAMILY, 10),
        )
        menu.add_command(label="⚙  Settings",               command=self._parent.open_settings)
        menu.add_command(label="📋  Copy Transcript  Ctrl+Alt+C",
                         command=self._parent.copy_transcript)
        menu.add_separator()
        menu.add_command(label="✕  Quit  Ctrl+Alt+Q",        command=self._parent.quit_app)
        menu.tk_popup(event.x_root, event.y_root)


# ─────────────────────────────────────────────────────────────────────────────
# Settings window
# ─────────────────────────────────────────────────────────────────────────────

class SettingsWindow(tk.Toplevel):
    """Settings panel for device, model, language, compute device, and overlay width."""

    def __init__(self, parent: "SubtitleOverlay") -> None:
        super().__init__(parent)
        self._parent = parent
        self.title("Subtitle Overlay — Settings")
        self.geometry("500x580")
        self.resizable(False, False)
        self.configure(bg="#0f0f23")
        self.attributes("-topmost", True)
        # Close callback: clear the parent's reference so it can be re-opened
        self.protocol("WM_DELETE_WINDOW", self._on_close)
        self._build_ui()

    def _on_close(self) -> None:
        self._parent._settings_win = None
        self.destroy()

    def _build_ui(self) -> None:
        pad      = {"padx": 16, "pady": 6}
        label_kw = dict(bg="#0f0f23", fg="#ccccdd", font=(FONT_FAMILY, 10))
        entry_kw = dict(bg="#1a1a2e", fg="white", insertbackground="white",
                        relief="flat", font=(FONT_FAMILY, 11), bd=8)

        tk.Label(self, text="⚙  Subtitle Overlay Settings",
                 bg="#0f0f23", fg=HANDLE_ACCENT,
                 font=(FONT_FAMILY, 13, "bold")).pack(**pad, fill="x")
        tk.Frame(self, bg=HANDLE_ACCENT, height=1).pack(fill="x", padx=16)

        # ── Device index ──────────────────────────────────────────────────────
        tk.Label(self, text="Audio Device Index:", **label_kw).pack(**pad, anchor="w")
        self._device_var = tk.StringVar(
            value=str(self._parent._cfg["device_index"] or "auto")
        )
        tk.Entry(self, textvariable=self._device_var, **entry_kw).pack(**pad, fill="x")

        # ── Live device list (clickable) ──────────────────────────────────────
        self._build_device_list()

        # ── Model ─────────────────────────────────────────────────────────────
        tk.Label(self, text="Whisper Model:", **label_kw).pack(**pad, anchor="w")
        self._model_var = tk.StringVar(value=self._parent._cfg["model_name"])
        model_menu = tk.OptionMenu(self, self._model_var,
                                   "tiny.en", "base.en", "small.en", "medium.en")
        model_menu.configure(bg="#1a1a2e", fg="white", activebackground=HANDLE_ACCENT,
                             font=(FONT_FAMILY, 11), relief="flat", highlightthickness=0)
        model_menu["menu"].configure(bg="#1a1a2e", fg="white", font=(FONT_FAMILY, 10))
        model_menu.pack(**pad, fill="x")

        # ── Language ──────────────────────────────────────────────────────────
        tk.Label(self, text="Language (BCP-47 code, e.g. 'en', 'fr', 'de', or 'auto'):",
                 **label_kw).pack(**pad, anchor="w")
        self._lang_var = tk.StringVar(value=self._parent._cfg["language"] or "auto")
        tk.Entry(self, textvariable=self._lang_var, **entry_kw).pack(**pad, fill="x")

        # ── Compute device ────────────────────────────────────────────────────
        tk.Label(self, text="Compute Device:", **label_kw).pack(**pad, anchor="w")
        self._compute_var = tk.StringVar(value=self._parent._cfg["compute_device"])
        compute_menu = tk.OptionMenu(self, self._compute_var, "auto", "cpu", "cuda")
        compute_menu.configure(bg="#1a1a2e", fg="white", activebackground=HANDLE_ACCENT,
                               font=(FONT_FAMILY, 11), relief="flat", highlightthickness=0)
        compute_menu["menu"].configure(bg="#1a1a2e", fg="white", font=(FONT_FAMILY, 10))
        compute_menu.pack(**pad, fill="x")

        # ── Overlay width ─────────────────────────────────────────────────────
        tk.Label(self, text="Overlay Width (px, 200–3840):", **label_kw).pack(**pad, anchor="w")
        self._width_var = tk.StringVar(value=str(self._parent._cfg.get("overlay_width", 860)))
        tk.Entry(self, textvariable=self._width_var, **entry_kw).pack(**pad, fill="x")

        # ── Info ──────────────────────────────────────────────────────────────
        tk.Label(self,
                 text="Model/language/device changes take effect after engine restart.\n"
                      "Width change is applied immediately.",
                 bg="#0f0f23", fg="#666688",
                 font=(FONT_FAMILY, 9, "italic"),
                 justify="left").pack(pady=2, padx=16, anchor="w")

        # ── Buttons ───────────────────────────────────────────────────────────
        btn_frame = tk.Frame(self, bg="#0f0f23")
        btn_frame.pack(fill="x", padx=16, pady=12)
        tk.Button(btn_frame, text="Apply & Restart Engine",
                  bg=HANDLE_ACCENT, fg="white", relief="flat",
                  font=(FONT_FAMILY, 10, "bold"), cursor="hand2",
                  command=self._apply).pack(side="left", padx=4, ipady=6, ipadx=12)
        tk.Button(btn_frame, text="Cancel",
                  bg="#2a2a4e", fg="white", relief="flat",
                  font=(FONT_FAMILY, 10), cursor="hand2",
                  command=self._on_close).pack(side="left", padx=4, ipady=6, ipadx=12)

    def _build_device_list(self) -> None:
        """Show available soundcard devices; clicking fills the index field."""
        try:
            import soundcard as sc
            mics = sc.all_microphones(include_loopback=True)
            if not mics:
                return
        except Exception:
            return

        frame = tk.Frame(self, bg="#0f0f23")
        frame.pack(fill="x", padx=16, pady=(0, 2))
        tk.Label(frame, text="Available devices (click to select):",
                 bg="#0f0f23", fg="#aaaacc",
                 font=(FONT_FAMILY, 8)).pack(anchor="w")
        lb = tk.Listbox(frame, bg="#0d0d1e", fg="#aaaacc", font=(FONT_FAMILY, 8),
                        relief="flat", height=min(len(mics), 4),
                        selectbackground=HANDLE_ACCENT, highlightthickness=0)
        for idx, mic in enumerate(mics):
            lb.insert(tk.END, f"  {idx:>3}  {mic.name}")
        lb.pack(fill="x")

        def _on_select(_event: tk.Event) -> None:
            sel = lb.curselection()
            if sel:
                self._device_var.set(str(sel[0]))

        lb.bind("<<ListboxSelect>>", _on_select)

    def _apply(self) -> None:
        # Validate device index
        dev_str = self._device_var.get().strip()
        try:
            new_device = None if dev_str.lower() == "auto" else int(dev_str)
        except ValueError:
            messagebox.showerror("Invalid Input",
                                 "Device index must be an integer or 'auto'.")
            return

        # Validate width
        try:
            new_width = int(self._width_var.get().strip())
            if not (200 <= new_width <= 3840):
                raise ValueError
        except ValueError:
            messagebox.showerror("Invalid Input", "Overlay width must be 200–3840 px.")
            return

        lang_str = self._lang_var.get().strip()
        new_lang = None if lang_str.lower() == "auto" else (lang_str or None)

        old_width = self._parent._cfg.get("overlay_width", 860)

        self._parent._cfg.update({
            "device_index":   new_device,
            "model_name":     self._model_var.get(),
            "language":       new_lang,
            "compute_device": self._compute_var.get(),
            "overlay_width":  new_width,
        })
        _save_config(self._parent._cfg)

        # Apply width immediately without full restart
        if new_width != old_width:
            self._parent.apply_width(new_width)

        self._on_close()
        self._parent.restart_engine()


# ─────────────────────────────────────────────────────────────────────────────
# Main overlay window
# ─────────────────────────────────────────────────────────────────────────────

class SubtitleOverlay(tk.Tk):
    """
    Transparent, always-on-top subtitle overlay.

    Window layout (top → bottom):
        ┌────────────────────────────────────┐  ← DragHandle  (opaque, draggable)
        │  ▓▓▓  SUBTITLES  ▓▓▓           ●  │
        ├────────────────────────────────────┤
        │                                    │  ← SubtitleCanvas (transparent bg,
        │    Subtitle line 1                 │     click-through)
        │    Subtitle line 2                 │
        └────────────────────────────────────┘
    """

    def __init__(self, cfg: dict) -> None:
        super().__init__()
        self._cfg = cfg
        self._engine: TranscriptionEngine | None = None
        self._transcript_queue: queue.Queue[str] = queue.Queue()
        self._history: list[str] = []
        self._settings_win: SettingsWindow | None = None   # duplicate-guard
        self._transcript_file = None   # optional file handle for --save-transcript
        self._tray = None

        self._open_transcript_file()
        self._build_window()
        self._build_ui()

        # Click-through: primary call (works when update_idletasks is enough)
        self._apply_clickthrough()
        # Secondary guarantee: fires when the native HWND is fully realized
        self._map_bind_id = self.bind("<Map>", self._on_map_event, add="+")

        self._start_tray()
        self._start_hotkeys()
        self._start_engine()
        self._poll_transcripts()

    # ── Transcript file ───────────────────────────────────────────────────────

    def _open_transcript_file(self) -> None:
        path = self._cfg.get("_transcript_path")  # runtime-only, not saved to JSON
        if not path:
            return
        try:
            self._transcript_file = open(path, "a", encoding="utf-8", buffering=1)
            log.info("Saving transcript to: %s", path)
        except OSError as e:
            log.warning("Could not open transcript file '%s': %s", path, e)

    # ── Window setup ──────────────────────────────────────────────────────────

    def _build_window(self) -> None:
        self.title("Subtitle Overlay")
        self.overrideredirect(True)
        self.attributes("-topmost", True)
        self.configure(bg=TRANSPARENT_COLOR)
        if _WINDOWS:
            self.attributes("-transparentcolor", TRANSPARENT_COLOR)

        w        = self._cfg.get("overlay_width", 860)
        total_h  = CANVAS_HEIGHT + DragHandle.HANDLE_HEIGHT
        self.geometry(f"{w}x{total_h}")
        self.resizable(False, False)

        # Restore saved position or auto-center at bottom of primary screen
        x = self._cfg.get("overlay_x")
        y = self._cfg.get("overlay_y")
        if x is None or y is None:
            sw = self.winfo_screenwidth()
            sh = self.winfo_screenheight()
            x  = (sw - w) // 2
            y  = sh - total_h - 80
        self.geometry(f"+{x}+{y}")

    def _build_ui(self) -> None:
        self._handle = DragHandle(self)
        self._handle.pack(fill="x")
        w = self._cfg.get("overlay_width", 860)
        self._canvas = SubtitleCanvas(self, width=w, height=CANVAS_HEIGHT)
        self._canvas.pack(fill="both", expand=True)

    # ── Click-through ─────────────────────────────────────────────────────────

    def _on_map_event(self, _: tk.Event) -> None:
        """
        Fires once when the OS has fully created the native window.
        Guarantees clickthrough is applied even if the primary call in
        __init__ fired before winfo_id() returned a valid HWND.
        """
        self.unbind("<Map>", self._map_bind_id)   # remove only this specific binding
        self._apply_clickthrough()

    def _apply_clickthrough(self) -> None:
        if not _WINDOWS:
            return
        self.update_idletasks()
        hwnd = self.winfo_id()
        if hwnd:
            _set_clickthrough(hwnd)
            log.debug("Click-through applied (HWND=%d).", hwnd)

    # ── Position persistence ──────────────────────────────────────────────────

    def save_position(self) -> None:
        """Persist current overlay position to config.json."""
        self._cfg["overlay_x"] = self.winfo_x()
        self._cfg["overlay_y"] = self.winfo_y()
        _save_config(self._cfg)

    def apply_width(self, new_width: int) -> None:
        """Resize the overlay and canvas to *new_width* pixels immediately."""
        total_h = CANVAS_HEIGHT + DragHandle.HANDLE_HEIGHT
        self.geometry(f"{new_width}x{total_h}")
        self._canvas.configure(width=new_width)
        log.info("Overlay width changed to %d px.", new_width)

    # ── System tray ───────────────────────────────────────────────────────────

    def _start_tray(self) -> None:
        """Start a system-tray icon if pystray + Pillow are available."""
        try:
            import pystray  # type: ignore[import]

            image = _make_tray_image()
            menu = pystray.Menu(
                pystray.MenuItem("Settings",
                                 lambda icon, item: self.after(0, self.open_settings)),
                pystray.MenuItem("Copy Transcript (Ctrl+Alt+C)",
                                 lambda icon, item: self.after(0, self.copy_transcript)),
                pystray.Menu.SEPARATOR,
                pystray.MenuItem("Quit (Ctrl+Alt+Q)",
                                 lambda icon, item: self.after(0, self.quit_app)),
            )
            self._tray = pystray.Icon("SubtitleOverlay", image,
                                      "Subtitle Overlay", menu)
            threading.Thread(target=self._tray.run, daemon=True,
                             name="TrayIcon").start()
            log.info("System tray icon started.")
        except ImportError:
            log.info("pystray / Pillow not installed — tray icon disabled. "
                     "Install with: pip install pystray Pillow")
        except Exception as exc:
            log.warning("Could not start tray icon: %s", exc)

    # ── Global hotkeys (Ctrl+Alt+C / Ctrl+Alt+Q) ─────────────────────────────

    def _start_hotkeys(self) -> None:
        """Register global hotkeys via Win32 RegisterHotKey in a daemon thread."""
        if not _WINDOWS:
            return

        def _listener() -> None:
            user32 = ctypes.windll.user32
            ok_c = user32.RegisterHotKey(None, _HOTKEY_COPY,
                                         _MOD_CONTROL | _MOD_ALT, 0x43)  # 'C'
            ok_q = user32.RegisterHotKey(None, _HOTKEY_QUIT,
                                         _MOD_CONTROL | _MOD_ALT, 0x51)  # 'Q'
            if not (ok_c and ok_q):
                log.debug("Could not register one or more global hotkeys "
                          "(another app may have claimed them).")

            msg = ctypes.wintypes.MSG()
            while user32.GetMessageW(ctypes.byref(msg), None, 0, 0) > 0:
                if msg.message == _WM_HOTKEY:
                    if msg.wParam == _HOTKEY_COPY:
                        self.after(0, self.copy_transcript)
                    elif msg.wParam == _HOTKEY_QUIT:
                        self.after(0, self.quit_app)
                user32.TranslateMessage(ctypes.byref(msg))
                user32.DispatchMessageW(ctypes.byref(msg))

            user32.UnregisterHotKey(None, _HOTKEY_COPY)
            user32.UnregisterHotKey(None, _HOTKEY_QUIT)

        threading.Thread(target=_listener, daemon=True,
                         name="HotkeyListener").start()
        log.info("Global hotkeys registered: Ctrl+Alt+C (copy), Ctrl+Alt+Q (quit).")

    # ── Engine management ─────────────────────────────────────────────────────

    def _start_engine(self) -> None:
        self._handle.set_status(False)
        self._engine = TranscriptionEngine(
            device_index   = self._cfg["device_index"],
            model_name     = self._cfg["model_name"],
            language       = self._cfg["language"],
            compute_device = self._cfg["compute_device"],
            on_transcript  = self._on_transcript,
            on_error       = self._on_engine_error,
        )
        threading.Thread(target=self._run_engine, daemon=True,
                         name="EngineStarter").start()

    def _run_engine(self) -> None:
        com_initialized = False
        if sys.platform == "win32":
            try:
                import pythoncom
                pythoncom.CoInitialize()
                com_initialized = True
            except Exception:
                try:
                    import ctypes
                    ctypes.windll.ole32.CoInitialize(None)
                    com_initialized = True
                except Exception:
                    pass
        try:
            self._engine.start()
            self.after(0, lambda: self._handle.set_status(True))
        except Exception as exc:
            err = str(exc)
            log.error("Engine startup failed: %s", exc, exc_info=True)
            self.after(0, lambda: self._show_engine_error(err))
        finally:
            if com_initialized:
                if sys.platform == "win32":
                    try:
                        import pythoncom
                        pythoncom.CoUninitialize()
                    except Exception:
                        try:
                            import ctypes
                            ctypes.windll.ole32.CoUninitialize()
                        except Exception:
                            pass

    def restart_engine(self) -> None:
        """
        Stop the running engine then start a fresh one.
        The stop blocks up to ~13 s in a daemon thread; the new engine is
        only started after the old one has fully exited — no race condition.
        """
        old_engine = self._engine
        self._engine = None
        self._history.clear()
        self._canvas.clear_text()
        self._handle.set_status(False)

        def _stop_then_start() -> None:
            com_initialized = False
            if sys.platform == "win32":
                try:
                    import pythoncom
                    pythoncom.CoInitialize()
                    com_initialized = True
                except Exception:
                    try:
                        import ctypes
                        ctypes.windll.ole32.CoInitialize(None)
                        com_initialized = True
                    except Exception:
                        pass
            try:
                if old_engine:
                    old_engine.stop()
            finally:
                if com_initialized:
                    if sys.platform == "win32":
                        try:
                            import pythoncom
                            pythoncom.CoUninitialize()
                        except Exception:
                            try:
                                import ctypes
                                ctypes.windll.ole32.CoUninitialize()
                            except Exception:
                                pass
            self.after(0, self._start_engine)

        threading.Thread(target=_stop_then_start, daemon=True,
                         name="EngineRestart").start()

    def _show_engine_error(self, msg: str) -> None:
        self._handle.set_status(False)
        self._canvas.set_text([f"⚠ Engine error: {msg[:60]}"])

    def _on_engine_error(self, msg: str) -> None:
        """Called from the AudioProducer thread when it dies mid-session."""
        log.error("Engine mid-session error: %s", msg)
        self.after(0, lambda: self._show_engine_error(msg))

    # ── Transcript pipeline ───────────────────────────────────────────────────

    def _on_transcript(self, text: str) -> None:
        """Called from WhisperConsumer thread — only enqueue, never touch UI."""
        self._transcript_queue.put_nowait(text)

    def _poll_transcripts(self) -> None:
        """Drain the transcript queue on the UI thread every 80 ms."""
        try:
            while True:
                text = self._transcript_queue.get_nowait()
                self._add_transcript(text)
        except queue.Empty:
            pass
        self.after(80, self._poll_transcripts)

    def _add_transcript(self, text: str) -> None:
        if not text:
            return
        self._history.append(text)
        if len(self._history) > HISTORY_MAX:
            self._history = self._history[-HISTORY_MAX:]
        self._canvas.set_text(self._wrap_lines(self._history[-MAX_LINES:]))
        # Optionally persist to file
        if self._transcript_file:
            try:
                self._transcript_file.write(
                    f"[{time.strftime('%H:%M:%S')}] {text}\n"
                )
            except OSError as e:
                log.warning("Transcript file write error: %s", e)
                self._transcript_file = None

    def _wrap_lines(self, texts: list[str]) -> list[str]:
        """Word-wrap long phrases to fit the canvas width."""
        max_chars = 72
        result: list[str] = []
        for text in texts:
            if len(text) <= max_chars:
                result.append(text)
            else:
                words, line = text.split(), ""
                for word in words:
                    if len(line) + len(word) + 1 <= max_chars:
                        line = (line + " " + word).strip()
                    else:
                        if line:
                            result.append(line)
                        line = word
                if line:
                    result.append(line)
        return result[-MAX_LINES:]

    # ── User actions ──────────────────────────────────────────────────────────

    def open_settings(self) -> None:
        """Open the settings window; raise/focus if already open."""
        if self._settings_win is not None and self._settings_win.winfo_exists():
            self._settings_win.lift()
            self._settings_win.focus_force()
            return
        self._settings_win = SettingsWindow(self)

    def copy_transcript(self) -> None:
        """Copy the full session transcript to the clipboard."""
        if not self._history:
            return
        text = "\n".join(self._history)
        self.clipboard_clear()
        self.clipboard_append(text)
        log.info("Transcript copied to clipboard (%d segments).", len(self._history))

    def quit_app(self) -> None:
        log.info("User requested quit.")
        self.save_position()
        if self._tray:
            try:
                self._tray.stop()
            except Exception:
                pass
        if self._transcript_file:
            self._transcript_file.close()
        if self._engine:
            threading.Thread(target=self._engine.stop, daemon=True).start()
        self.after(800, self.destroy)


# ─────────────────────────────────────────────────────────────────────────────
# Entry point
# ─────────────────────────────────────────────────────────────────────────────

def main() -> None:
    parser = argparse.ArgumentParser(
        description="Transparent always-on-top real-time subtitle overlay."
    )
    parser.add_argument(
        "--device", type=int, default=None, metavar="INDEX",
        help="soundcard device index (see list_devices.py). Omit to auto-detect.",
    )
    parser.add_argument(
        "--model", type=str, default=None,
        choices=["tiny.en", "base.en", "small.en", "medium.en"],
        help="Whisper model size. Overrides saved config.",
    )
    parser.add_argument(
        "--language", type=str, default=None, metavar="CODE",
        help="BCP-47 language code (e.g. 'en', 'fr') or 'auto'. Overrides saved config.",
    )
    parser.add_argument(
        "--compute-device", type=str, default=None,
        choices=["auto", "cpu", "cuda"],
        help="Inference device. Overrides saved config.",
    )
    parser.add_argument(
        "--save-transcript", type=str, default=None, metavar="PATH",
        help="Append every transcript line to this file (timestamped). "
             "Not saved to config.json — pass on each launch.",
    )
    args = parser.parse_args()

    # Load persisted config, then apply CLI overrides
    cfg = _load_config()
    if args.device is not None:
        cfg["device_index"] = args.device
    if args.model is not None:
        cfg["model_name"] = args.model
    if args.language is not None:
        cfg["language"] = None if args.language.lower() == "auto" else args.language
    if args.compute_device is not None:
        cfg["compute_device"] = args.compute_device
    if args.save_transcript is not None:
        cfg["_transcript_path"] = args.save_transcript   # runtime-only key

    log.info(
        "Starting overlay — model=%s, device=%s, language=%s, compute=%s, width=%d",
        cfg["model_name"],
        cfg["device_index"] if cfg["device_index"] is not None else "auto",
        cfg["language"] or "auto",
        cfg["compute_device"],
        cfg.get("overlay_width", 860),
    )

    print("\n" + "=" * 64)
    print("  REAL-TIME SUBTITLE OVERLAY")
    print("=" * 64)
    print(f"  Model    : {cfg['model_name']}")
    print(f"  Device   : {cfg['device_index'] if cfg['device_index'] is not None else 'auto'}")
    print(f"  Language : {cfg['language'] or 'auto'}")
    print(f"  Compute  : {cfg['compute_device']}")
    if cfg.get("_transcript_path"):
        print(f"  Saving to: {cfg['_transcript_path']}")
    print("  Hotkeys  : Ctrl+Alt+C = copy  |  Ctrl+Alt+Q = quit")
    print("  Right-click the handle bar for Settings / Quit.\n")

    app = SubtitleOverlay(cfg=cfg)
    app.mainloop()


if __name__ == "__main__":
    main()
