---
applyTo: "**/{main_window,compositor,theme,backgrounds,frames,project_file,models,icon,title_bar,source_picker,preview_widget,countdown_overlay,processing_overlay,recording_border}.py"
---

# UI & Project Management

## Taskbar Close

- `_WinCloseFilter` (QAbstractNativeEventFilter) is installed on QApplication to intercept `WM_CLOSE`
- `closeEvent` calls `os._exit(0)` for clean shutdown (prevents Qt cleanup hangs with native hooks)

## Project Path & Title Bar

- `_project_path` tracks the current .fcproj file path; Ctrl+S re-saves without dialog
- **Incremental save**: `save_project(metadata_only=True)` rewrites only `project.json` in the ZIP without touching the video. Full saves write the video entry first (offset 0) so that metadata saves can modify the file in-place: the raw video bytes stay untouched and only the JSON + central directory are rewritten at the end of the file. Falls back to streaming copy (8 MB chunks) for old-layout files where the video isn't first.
- **Export filename**: defaults to project name (e.g. `MyProject.mp4`) instead of generic `followcursor-{duration}.mp4`
- `TitleBar.set_title(name, unsaved)` updates the logo label to show project name + unsaved indicator
- Close confirmation dialog (Save / Don't Save / Cancel) when `_unsaved_changes` is True
- Project saving runs on a background `_SaveProjectWorker(QThread)` for UI responsiveness

## Processing Overlay

- `ProcessingOverlay` widget (full-window, pulsing banner) shown during long-running operations
- Reusable: `show_overlay(title, subtitle)` — used for recording finalization and project loading
- Project loading runs on a background `_LoadProjectWorker(QThread)`

## Preview Canvas Sizing

- Compositor's `compose_scene` is called with `(canvas_w, canvas_h)` — painter is translated and clipped to canvas rect
- **Auto (source)**: letterboxed/pillarboxed to match source aspect ratio
- **Non-auto presets** (e.g., 1:1, 4:3): device frame fitted and centered within target aspect ratio

## Error Resilience

- `sys.excepthook` in `main.py` logs unhandled exceptions via `logging`
- Recording/export methods wrapped in `try/except` with `logger.exception()`
- Win32 hook callbacks have `try/except` guards
- On failure, UI is restored to a usable state (buttons re-shown, overlays hidden)

## Frame Presets

- Names must be **generic** (no trademarked device names)
- Current: Wide Bezel, Slim Bezel, Thin Border, Shadow Only, No Frame

## Build Optimization

- PyInstaller excludes 40+ unused PySide6 modules (QtWebEngine, Qt3D, QtMultimedia, QtQml, etc.)
- Only QtCore, QtGui, QtWidgets, QtSvg are needed
- Also excludes tkinter, unittest, email, http, xml, pydoc
