---
applyTo: "**/{video_exporter,cursor_renderer,utils}.py"
---

# Video Export Pipeline

## General Architecture

- Frames are piped to ffmpeg via stdin (not written to temp files)
- A dedicated writer thread drains composed frames from a bounded queue (depth 16) into ffmpeg's stdin while the main compositor thread prepares the next frame — overlaps CPU compositing with GPU encoding
- Export renders each frame with zoom, cursor overlay, device bezel, and background

## MP4 Encoding

- GPU-accelerated encoding: `detect_available_encoders()` in `utils.py` probes NVENC / QuickSync / AMF at startup; `best_hw_encoder()` picks the fastest available, falling back to `libx264`
- `ENCODER_PROFILES` dict maps each encoder ID to codec + quality args (tuned to approximate CRF 18)
- `build_encoder_args(encoder_id)` returns the ffmpeg arg list for the selected encoder
- Encoder choice is persisted via QSettings and exposed in the editor panel's settings menu
- `VideoExporter.export()` and `_run()` accept an `encoder_id` parameter; the ffmpeg command is built dynamically via `build_encoder_args()`

## Encoder Fallback Chain (Two-Phase)

1. **Immediate check**: if ffmpeg exits within 100ms, try the next available HW encoder in priority order (NVENC → QuickSync → AMF), falling back to `libx264` only after all are exhausted
2. **Mid-stream retry**: if the HW encoder fails partway through, restart the full encode walking the same fallback chain
- `VideoExporter` emits `status = Signal(str)` and `MainWindow._on_export_status()` updates the status bar on each fallback attempt

## GIF Export

- When output path ends in `.gif`, skips the H.264 encoder chain
- Uses `build_gif_args()` from `utils.py` for palette-based ffmpeg filtergraph (`fps=15, palettegen+paletteuse`)
- `GIF_FPS = 15` constant controls default frame rate
- No encoder fallback; palette generation timeout is 300s (palettegen buffers all frames before writing)

## Zoom Behavior in Export

- **No Frame**: zoom/pan applies only to video content — background stays static. Cursor and click overlays use virtual screen-rect mapping with clip rect when zoomed.
- **Device frame (any bezel)**: zoom/pan moves the device (frame + video) while background stays static — like physically bringing a device closer

## Sub-Pixel Precision

- Both zoom paths in `_compose_cv` use `cv2.warpAffine` with `WARP_INVERSE_MAP` for sub-pixel crop/pan accuracy
- Eliminates temporal jitter from integer pixel snapping during smooth zoom and pan transitions
- Device frame path uses a single warpAffine pass (no intermediate upscale) for both content and device mask

## Error Handling

- Both `BrokenPipeError` and `OSError` are caught on pipe writes (Windows raises `OSError(22)` instead of `BrokenPipeError`)
- Status bar shows active encoder/format during export and updates on each fallback attempt
