# Zumly

Zumly is a Windows-first screen recording utility for QA evidence, bug reports,
software demos, and engineering walkthroughs. It records from the system tray,
captures mouse/click metadata, opens a lightweight editor after recording, and
exports polished MP4s with smart zooms, click markers, backgrounds, and device
frames.

This repository is forked from `sabbour/followcursor`, but the active Zumly
architecture is no longer the original monolithic editor/export loop. The app is
now split into small processes so the tray can stay idle and cheap until the
user starts a recording.

## Current Status

Zumly is in an architecture-stabilization phase.

Working in the current branch:

- System tray daemon with global `Ctrl+Shift+R` recording toggle.
- Headless monitor recording through Windows Graphics Capture.
- JSON project handoff from recorder to editor/exporter.
- PySide6 editor for preview, backgrounds, device frames, click effect presets,
  and output sizing.
- FFmpeg-based MP4 export with zoom, background/frame composition, and visible
  click markers.
- PyInstaller multi-executable bundle: `tray_app.exe`, `editor_app.exe`, and
  `export_app.exe`.

Known limitations:

- The editor/export pipeline is still being simplified after the fork.
- Some older project-file, narration, and docs code remains for compatibility
  and is not the core recording path.
- The live editor preview still uses OpenCV for video frame decoding; the final
  export renderer uses FFmpeg filter graphs.

## Architecture

Zumly uses a multi-process daemon model:

| Process | Entry point | Responsibility |
| --- | --- | --- |
| Tray daemon | `tray_app.py` | System tray UI, global hotkey, settings, subprocess orchestration |
| Capture worker | `zumly/main.py` | Headless WGC capture, input tracking, auto-zoom generation, JSON serialization |
| Editor | `editor_app.py` | PySide6 project editor and preview UI |
| Export worker | `export_app.py` | Headless FFmpeg export from project JSON |

The process contract is intentionally simple:

1. The tray starts `zumly/main.py` from source, or relaunches `tray_app.exe` with
   `--headless-engine` when frozen.
2. The capture worker records the raw video, tracks mouse/click activity, writes
   `<timestamp>_project.json`, then exits so Windows capture resources are
   released.
3. The tray watches stdout for the serialized project path.
4. The tray opens `editor_app` with `--project <json>`, or starts `export_app`
   directly when editor review is disabled.
5. The exporter reads the JSON and builds an FFmpeg `filter_complex_script` for
   zooms, click markers, backgrounds, and device frames.

## Data Contract

The JSON project file is the IPC boundary between processes. Important fields:

| Field | Meaning |
| --- | --- |
| `videoPath` | Raw captured video path |
| `outPath` | Intended MP4 export path |
| `duration` | Recording duration in milliseconds |
| `actualFps` | Measured capture FPS |
| `monitorRect` | Source monitor bounds used to map cursor/click coordinates |
| `mouseTrack` | Recorded cursor samples |
| `clickEvents` | Recorded click timestamps and coordinates |
| `keyframes` | Generated or edited zoom keyframes |
| `backgroundId` | Selected background preset |
| `frameId` | Selected device frame preset |
| `clickEffectId` | Selected click effect preset |
| `outputDimensions` | Export canvas size or `auto` |

## Source Setup

Requirements:

- Windows 10/11
- Python 3.10+
- FFmpeg is provided through `imageio-ffmpeg`

Install dependencies:

```powershell
pip install -r requirements.txt
```

The top-level `requirements.txt` delegates to `zumly/requirements.txt`.

## Run From Source

Start the tray app:

```powershell
python tray_app.py
```

Start a recording:

- Press `Ctrl+Shift+R`, or
- Use the tray menu and choose `Start Recording`.

Stop a recording:

- Press `Ctrl+Shift+R` again, or
- Use the tray menu and choose `Stop Recording`.

Recordings and project JSON files are written by default to:

```text
%USERPROFILE%\Videos\Zumly
```

Open an existing project in the editor:

```powershell
python editor_app.py --project "C:\Users\<you>\Videos\Zumly\<timestamp>_project.json"
```

Export an existing project without opening the editor:

```powershell
python export_app.py --project "C:\Users\<you>\Videos\Zumly\<timestamp>_project.json"
```

## Build

Build the frozen app:

```powershell
python -m PyInstaller --noconfirm zumly.spec
```

Output folder:

```text
dist\zumly
```

Expected executables:

```text
dist\zumly\tray_app.exe
dist\zumly\editor_app.exe
dist\zumly\export_app.exe
```

Run the frozen tray:

```powershell
Start-Process -FilePath .\dist\zumly\tray_app.exe -WorkingDirectory .\dist\zumly
```

If Windows keeps old build files locked, stop the running tray/editor/export
processes before rebuilding.

## Verification

Fast syntax check:

```powershell
python -B -c "from pathlib import Path; files=['tray_app.py','editor_app.py','export_app.py','zumly/main.py','zumly/app/video_exporter.py']; [compile(Path(f).read_text(encoding='utf-8'), f, 'exec') for f in files]; print('syntax ok')"
```

Synthetic export smoke test, when `e2e_artifacts\synthetic_project.json` exists:

```powershell
python export_app.py --project .\e2e_artifacts\synthetic_project.json
```

Frozen export smoke test:

```powershell
.\dist\zumly\export_app.exe --project .\e2e_artifacts\synthetic_project.json
```

## Project Layout

```text
.
|-- tray_app.py                  # System tray daemon and hotkey owner
|-- editor_app.py                # Standalone PySide6 editor entry point
|-- export_app.py                # Headless export entry point
|-- zumly.spec                   # PyInstaller multi-executable build
|-- requirements.txt             # Delegates to zumly/requirements.txt
`-- zumly/
    |-- main.py                  # Headless capture worker
    |-- requirements.txt         # Python dependencies
    `-- app/
        |-- activity_analyzer.py # Mouse/click activity to zoom keyframes
        |-- backgrounds.py       # Background presets
        |-- click_tracker.py     # Win32 click hook
        |-- frames.py            # Device frame presets
        |-- global_hotkeys.py    # Win32 RegisterHotKey helper
        |-- keyboard_tracker.py  # Legacy-compatible no-op key tracker
        |-- models.py            # JSON/session dataclasses
        |-- mouse_tracker.py     # Cursor sampling
        |-- recording_overlay.py # Lightweight Win32 recording badge
        |-- screen_recorder.py   # Windows capture and raw recording
        |-- utils.py             # FFmpeg and subprocess helpers
        |-- video_exporter.py    # FFmpeg graph export engine
        `-- widgets/
            |-- editor_window.py # Editor shell and export routing
            |-- editor_panel.py  # Settings panel
            |-- preview_widget.py
            `-- timeline_widget.py
```

## Notes For Contributors

- Keep capture, editor, and export isolated. Do not start PySide6 UI from the
  capture worker.
- Treat the project JSON as the IPC contract and keep field names stable.
- Prefer FFmpeg for final rendering work; avoid reintroducing frame-by-frame
  Python export rendering.
- Preserve the tray's low idle cost. Heavy imports belong in the worker process
  that actually needs them.
- When changing export behavior, test both source `export_app.py` and frozen
  `dist\zumly\export_app.exe`.
