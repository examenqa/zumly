---
applyTo: "**/{screen_recorder,window_utils,mouse_tracker,keyboard_tracker,click_tracker,global_hotkeys}.py"
---

# Capture & Input Tracking

## DPI Awareness

- **Do NOT** call `SetProcessDpiAwareness` manually — PySide6 already sets `PER_MONITOR_DPI_AWARE_V2`
- Window capture via `PrintWindow` returns physical pixels; do not apply DPI scale factors

## Win32 Hooks (ctypes)

- Mouse and keyboard hooks use `WINFUNCTYPE` (not `CFUNCTYPE`) for 64-bit Windows compatibility
- Hook callbacks must have explicit `argtypes` and `restype` to prevent integer overflow on 64-bit pointers
- `CallNextHookEx` needs proper argument types defined

## Recording Performance

- During recording, the preview widget shows a **static blurred snapshot** (not live compositor output)
- `screen_recorder.py` skips emitting preview frames during recording to reduce CPU load
- The compositor pipeline only runs during playback/editing

## Keyboard Tracking

- Each keystroke records timestamp + cursor position (`GetCursorPos`)
- Modifier keys (Ctrl, Shift, Alt, Win, CapsLock, NumLock, ScrollLock) and app-hotkey keys (R, =, -) are excluded so they don't inflate typing activity signals
- `KeyEvent` has optional `x`/`y` fields (backward-compatible with old projects that lack them)
