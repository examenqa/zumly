"""Zumly System Tray Application.

A lightweight system-tray wrapper that controls the headless Zumly CLI
engine via subprocess.  Uses `pystray` for the tray icon, `tkinter`
for the settings dialog, and `ctypes.RegisterHotKey` for a global
hotkey (Ctrl+Shift+R) to toggle recording.

Architecture:
    tray_app.py  ──(subprocess.Popen)──▷  main.py  (headless engine)
                  ◁──(stdout / poll)──
"""

import ctypes
import ctypes.wintypes as wintypes
import json
import logging
import os
import subprocess
import sys
import threading
import time
import tkinter as tk
from tkinter import filedialog, ttk
from pathlib import Path
from typing import Optional

from PIL import Image
import pystray

# ── Paths ────────────────────────────────────────────────────────────

_SCRIPT_DIR = Path(__file__).resolve().parent
_MAIN_PY = _SCRIPT_DIR / "zumly" / "main.py"
_ICON_PATH = _SCRIPT_DIR / "zumly" / "followcursor.ico"
_CONFIG_PATH = _SCRIPT_DIR / "config.json"
_PYTHON_EXE = sys.executable

# ── Logging ──────────────────────────────────────────────────────────

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [TRAY] %(levelname)s: %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger("zumly.tray")

# ── Win32 hotkey constants ───────────────────────────────────────────

WM_HOTKEY = 0x0312
WM_QUIT = 0x0012
MOD_CONTROL = 0x0002
MOD_SHIFT = 0x0004
VK_R = 0x52
HOTKEY_ID = 9901  # unique ID (different from main.py's ID=3)

# ── Default configuration ───────────────────────────────────────────

DEFAULT_CONFIG = {
    "output_folder": str(Path.home() / "Videos" / "Zumly"),
    "fps": 60,
    "monitor": 1,
}


# =====================================================================
#  Config helpers
# =====================================================================

def load_config() -> dict:
    """Load config from disk, falling back to defaults."""
    cfg = DEFAULT_CONFIG.copy()
    if _CONFIG_PATH.exists():
        try:
            with open(_CONFIG_PATH, "r", encoding="utf-8") as f:
                saved = json.load(f)
            cfg.update(saved)
        except (json.JSONDecodeError, OSError) as exc:
            logger.warning("Config load failed, using defaults: %s", exc)
    return cfg


def save_config(cfg: dict) -> None:
    """Persist configuration to disk."""
    try:
        with open(_CONFIG_PATH, "w", encoding="utf-8") as f:
            json.dump(cfg, f, indent=2)
        logger.info("Config saved to %s", _CONFIG_PATH)
    except OSError as exc:
        logger.error("Failed to save config: %s", exc)


# =====================================================================
#  Global Hotkey Thread  (Ctrl+Shift+R)
# =====================================================================

class _HotkeyThread(threading.Thread):
    """Win32 RegisterHotKey message-loop thread."""

    def __init__(self, callback):
        super().__init__(daemon=True, name="TrayHotkey")
        self._callback = callback
        self._thread_id = 0
        self._ready = threading.Event()

    def run(self):
        user32 = ctypes.windll.user32
        kernel32 = ctypes.windll.kernel32
        self._thread_id = kernel32.GetCurrentThreadId()

        ok = user32.RegisterHotKey(None, HOTKEY_ID, MOD_CONTROL | MOD_SHIFT, VK_R)
        if not ok:
            logger.warning("Could not register Ctrl+Shift+R — another app may hold it")
        self._ready.set()

        msg = wintypes.MSG()
        while user32.GetMessageW(ctypes.byref(msg), None, 0, 0) > 0:
            if msg.message == WM_HOTKEY and msg.wParam == HOTKEY_ID:
                self._callback()

        user32.UnregisterHotKey(None, HOTKEY_ID)

    def stop(self):
        if self._thread_id:
            ctypes.windll.user32.PostThreadMessageW(
                self._thread_id, WM_QUIT, 0, 0
            )
            self.join(timeout=2.0)


# =====================================================================
#  Settings Dialog  (tkinter)
# =====================================================================

class SettingsDialog:
    """A minimal tkinter window for FPS, monitor, and output folder."""

    def __init__(self, cfg: dict, on_save):
        self._cfg = cfg.copy()
        self._on_save = on_save
        self._win: Optional[tk.Tk] = None

    def show(self):
        """Open the settings window (runs on a new thread)."""
        threading.Thread(target=self._build, daemon=True, name="Settings").start()

    def _build(self):
        win = tk.Tk()
        self._win = win
        win.title("Zumly Settings")
        win.resizable(False, False)
        win.configure(bg="#1e1e2e")

        # Centre on screen
        w, h = 420, 280
        sx = (win.winfo_screenwidth() - w) // 2
        sy = (win.winfo_screenheight() - h) // 2
        win.geometry(f"{w}x{h}+{sx}+{sy}")

        # Try to set taskbar icon
        try:
            win.iconbitmap(str(_ICON_PATH))
        except tk.TclError:
            pass

        style = ttk.Style(win)
        style.theme_use("clam")

        # Dark-mode colors
        bg = "#1e1e2e"
        fg = "#cdd6f4"
        entry_bg = "#313244"
        accent = "#89b4fa"

        style.configure("TLabel", background=bg, foreground=fg, font=("Segoe UI", 10))
        style.configure("TEntry", fieldbackground=entry_bg, foreground=fg,
                         font=("Segoe UI", 10), borderwidth=0)
        style.configure("TButton", background=accent, foreground="#1e1e2e",
                         font=("Segoe UI", 10, "bold"), padding=6)
        style.configure("TSpinbox", fieldbackground=entry_bg, foreground=fg,
                         font=("Segoe UI", 10), arrowcolor=fg)

        frame = ttk.Frame(win, padding=20)
        frame.configure(style="TFrame")
        style.configure("TFrame", background=bg)
        frame.pack(fill="both", expand=True)

        # ── Title ──
        title_lbl = tk.Label(frame, text="⚙  Zumly Settings", bg=bg, fg=accent,
                             font=("Segoe UI", 14, "bold"))
        title_lbl.grid(row=0, column=0, columnspan=3, pady=(0, 16), sticky="w")

        # ── Output folder ──
        ttk.Label(frame, text="Output Folder").grid(row=1, column=0, sticky="w", pady=4)
        self._folder_var = tk.StringVar(value=self._cfg.get("output_folder", ""))
        folder_entry = ttk.Entry(frame, textvariable=self._folder_var, width=30)
        folder_entry.grid(row=1, column=1, padx=(8, 4), pady=4, sticky="ew")
        ttk.Button(frame, text="…", width=3, command=self._browse_folder).grid(
            row=1, column=2, pady=4
        )

        # ── FPS ──
        ttk.Label(frame, text="FPS").grid(row=2, column=0, sticky="w", pady=4)
        self._fps_var = tk.IntVar(value=self._cfg.get("fps", 60))
        fps_spin = tk.Spinbox(frame, from_=15, to=120, increment=5,
                              textvariable=self._fps_var, width=8,
                              bg=entry_bg, fg=fg, font=("Segoe UI", 10),
                              buttonbackground=entry_bg, relief="flat",
                              highlightthickness=0)
        fps_spin.grid(row=2, column=1, padx=(8, 0), pady=4, sticky="w")

        # ── Monitor ──
        ttk.Label(frame, text="Monitor").grid(row=3, column=0, sticky="w", pady=4)
        self._monitor_var = tk.IntVar(value=self._cfg.get("monitor", 1))
        mon_spin = tk.Spinbox(frame, from_=1, to=8, increment=1,
                              textvariable=self._monitor_var, width=8,
                              bg=entry_bg, fg=fg, font=("Segoe UI", 10),
                              buttonbackground=entry_bg, relief="flat",
                              highlightthickness=0)
        mon_spin.grid(row=3, column=1, padx=(8, 0), pady=4, sticky="w")

        # ── Hotkey label ──
        hk_lbl = tk.Label(frame, text="Hotkey:  Ctrl + Shift + R", bg=bg, fg="#a6adc8",
                          font=("Segoe UI", 9, "italic"))
        hk_lbl.grid(row=4, column=0, columnspan=3, pady=(12, 4), sticky="w")

        # ── Save button ──
        ttk.Button(frame, text="Save", command=self._save).grid(
            row=5, column=0, columnspan=3, pady=(12, 0), sticky="ew"
        )

        frame.columnconfigure(1, weight=1)

        win.protocol("WM_DELETE_WINDOW", win.destroy)
        win.mainloop()

    def _browse_folder(self):
        folder = filedialog.askdirectory(
            initialdir=self._folder_var.get(), title="Select output folder"
        )
        if folder:
            self._folder_var.set(folder)

    def _save(self):
        self._cfg["output_folder"] = self._folder_var.get()
        self._cfg["fps"] = self._fps_var.get()
        self._cfg["monitor"] = self._monitor_var.get()
        self._on_save(self._cfg)
        if self._win:
            self._win.destroy()


# =====================================================================
#  Tray Application
# =====================================================================

class ZumlyTray:
    """System-tray controller that wraps the headless CLI engine."""

    def __init__(self):
        self._recording = False
        self._process: Optional[subprocess.Popen] = None
        self._hotkey_thread: Optional[_HotkeyThread] = None
        self._icon: Optional[pystray.Icon] = None
        self._cfg = load_config()
        self._rec_count = 0  # counter for unique filenames

    # ── lifecycle ────────────────────────────────────────────────────

    def run(self):
        """Entry point — blocks until quit."""
        logger.info("Zumly tray starting…")

        # Ensure output folder exists
        os.makedirs(self._cfg["output_folder"], exist_ok=True)

        # Register global hotkey
        self._hotkey_thread = _HotkeyThread(self._on_hotkey)
        self._hotkey_thread.start()
        self._hotkey_thread._ready.wait(timeout=2.0)

        # Build tray icon
        icon_image = self._load_icon()
        self._icon = pystray.Icon(
            "Zumly",
            icon=icon_image,
            title="Zumly — Ready  (Ctrl+Shift+R)",
            menu=pystray.Menu(
                pystray.MenuItem(
                    lambda _: "⏹ Stop Recording" if self._recording else "⏺ Start Recording",
                    self._on_toggle,
                    default=True,
                ),
                pystray.Menu.SEPARATOR,
                pystray.MenuItem("⚙ Settings", self._on_settings),
                pystray.Menu.SEPARATOR,
                pystray.MenuItem("✕ Quit", self._on_quit),
            ),
        )
        self._icon.run()

    def _load_icon(self) -> Image.Image:
        """Load the ICO file, falling back to a generated icon."""
        try:
            return Image.open(str(_ICON_PATH))
        except Exception:
            # Fallback: create a simple coloured circle
            img = Image.new("RGBA", (64, 64), (0, 0, 0, 0))
            from PIL import ImageDraw
            draw = ImageDraw.Draw(img)
            draw.ellipse([8, 8, 56, 56], fill="#89b4fa")
            return img

    # ── tray callbacks ──────────────────────────────────────────────

    def _on_toggle(self, icon=None, item=None):
        if self._recording:
            self._stop_recording()
        else:
            self._start_recording()

    def _on_settings(self, icon=None, item=None):
        self._cfg = load_config()
        dialog = SettingsDialog(self._cfg, self._on_settings_saved)
        dialog.show()

    def _on_settings_saved(self, cfg: dict):
        self._cfg = cfg
        save_config(cfg)

    def _on_quit(self, icon=None, item=None):
        logger.info("Quit requested")
        if self._recording:
            self._stop_recording()
        if self._hotkey_thread:
            self._hotkey_thread.stop()
        if self._icon:
            self._icon.stop()

    def _on_hotkey(self):
        """Called by the hotkey thread when Ctrl+Shift+R is pressed."""
        self._on_toggle()

    # ── recording control ───────────────────────────────────────────

    def _start_recording(self):
        if self._recording:
            return

        self._cfg = load_config()
        os.makedirs(self._cfg["output_folder"], exist_ok=True)

        # Generate unique output filename
        self._rec_count += 1
        ts = time.strftime("%Y%m%d_%H%M%S")
        out_path = os.path.join(
            self._cfg["output_folder"],
            f"zumly_{ts}.mp4",
        )

        # Release our hotkey so main.py can register it
        if self._hotkey_thread:
            self._hotkey_thread.stop()
            self._hotkey_thread = None
            time.sleep(0.3)  # brief pause for Win32 to release

        # Spawn the headless engine by calling ourselves with the routing flag
        if getattr(sys, 'frozen', False):
            cmd = [sys.executable, "--headless-engine"]
        else:
            cmd = [sys.executable, sys.argv[0], "--headless-engine"]
            
        cmd += [
            "--out", out_path,
            "--monitor", str(self._cfg.get("monitor", 1)),
            "--fps", str(self._cfg.get("fps", 60)),
        ]
        logger.info("Starting recording: %s", " ".join(cmd))

        try:
            self._process = subprocess.Popen(
                cmd,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                cwd=str(_SCRIPT_DIR),
                creationflags=subprocess.CREATE_NO_WINDOW,
            )
        except OSError as exc:
            logger.error("Failed to start engine: %s", exc)
            self._reregister_hotkey()
            return

        self._recording = True
        self._update_tray("Recording…  (Ctrl+Shift+R to stop)", recording=True)

        # Monitor subprocess in background
        threading.Thread(
            target=self._monitor_process,
            args=(out_path,),
            daemon=True,
            name="ProcMon",
        ).start()

    def _stop_recording(self):
        """Request stop — the subprocess handles CTRL+SHIFT+R itself.

        If the subprocess is still running (e.g. user clicked Stop in menu
        instead of pressing the hotkey), we terminate it gracefully.
        """
        if not self._recording or self._process is None:
            return

        if self._process.poll() is None:
            logger.info("Terminating recording subprocess (pid=%s)", self._process.pid)
            self._process.terminate()
            try:
                self._process.wait(timeout=15)
            except subprocess.TimeoutExpired:
                self._process.kill()

    def _monitor_process(self, out_path: str):
        """Wait for the engine subprocess to exit, then restore tray state."""
        proc = self._process
        if proc is None:
            return

        # Stream stdout for logging
        try:
            for line in proc.stdout:
                line = line.rstrip()
                if line:
                    logger.info("[engine] %s", line)
        except (ValueError, OSError):
            pass

        proc.wait()
        rc = proc.returncode
        logger.info("Engine exited with code %s", rc)

        self._recording = False
        self._process = None

        if rc == 0 and os.path.isfile(out_path):
            self._update_tray(f"Saved: {os.path.basename(out_path)}")
            self._notify(f"Recording saved!\n{out_path}")
        else:
            self._update_tray("Ready  (Ctrl+Shift+R)")

        # Re-register our hotkey
        self._reregister_hotkey()

    # ── helpers ──────────────────────────────────────────────────────

    def _reregister_hotkey(self):
        """Re-register the tray's global hotkey after subprocess exits."""
        if self._hotkey_thread is not None:
            return
        self._hotkey_thread = _HotkeyThread(self._on_hotkey)
        self._hotkey_thread.start()
        self._hotkey_thread._ready.wait(timeout=2.0)
        logger.info("Hotkey re-registered")

    def _update_tray(self, title: str, recording: bool = False):
        """Update the tray icon tooltip."""
        if self._icon:
            self._icon.title = f"Zumly — {title}"
            # Swap icon colour to indicate recording state
            if recording:
                img = Image.new("RGBA", (64, 64), (0, 0, 0, 0))
                from PIL import ImageDraw
                draw = ImageDraw.Draw(img)
                draw.ellipse([8, 8, 56, 56], fill="#f38ba8")  # red = recording
                draw.rectangle([24, 24, 40, 40], fill="white")  # stop square
                self._icon.icon = img
            else:
                self._icon.icon = self._load_icon()

    def _notify(self, message: str):
        """Show a Windows toast notification via the tray icon."""
        try:
            if self._icon:
                self._icon.notify(message, "Zumly")
        except Exception:
            pass  # Not critical


# =====================================================================
#  Entry point
# =====================================================================

def main():
    app = ZumlyTray()
    app.run()


if __name__ == "__main__":
    main()
