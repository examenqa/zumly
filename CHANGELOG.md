# Changelog

All notable changes to FollowCursor are documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/), and this project adheres to [Semantic Versioning](https://semver.org/).

## [0.2.0] — 2026-03-03

### Added

- **GIF export** — export recordings as palette-optimised GIF (15 fps) via a two-pass palettegen + paletteuse ffmpeg filtergraph
- **GPU-accelerated MP4 encoding** — auto-detects NVENC, QuickSync, and AMF hardware encoders at startup with a two-phase fallback chain (immediate + mid-stream retry) down to libx264
- **Video encoder selection** — choose the active encoder from the editor panel's settings menu; persisted via QSettings
- **Pan path points** — add intermediate pan waypoints within a zoom segment to create a smooth panning path while staying zoomed in; numbered yellow markers on the timeline with drag, reorder, and delete support
- **Pan point via preview right-click** — right-click the video surface while zoomed in to add a pan point at the clicked position (replaces the former timeline segment menu item)
- **Activity analyzer** — auto-generate zoom keyframes from typing clusters and click events with spatial-aware clustering, pan-while-zoomed chains, and overlap prevention
- **Keystroke position tracking** — each keystroke records the cursor position (`GetCursorPos`); the activity analyzer uses these coordinates directly for typing zone positions
- **Auto-generate confirmation** — warns before replacing existing zoom sections when auto-generating keyframes
- **Undo/redo for click events** — zoom engine snapshots now capture click events so deletions are fully undoable
- **Debug overlay** — colored zoom markers drawn on the preview (enabled by default)
- **Lazy loading** — heavy imports (dxcam, cv2, mss) are deferred to improve startup time
- **Comprehensive test suite** — 164 pytest tests covering models, zoom engine, activity analyzer, utils, frames, backgrounds, and project files

### Changed

- **Sub-pixel export precision** — both zoom paths in the video exporter now use `cv2.warpAffine` with `WARP_INVERSE_MAP`, eliminating temporal jitter from integer pixel snapping during smooth zoom and pan transitions
- **In-place metadata save** — incremental project saves (`metadata_only=True`) now modify the ZIP in-place, writing only the JSON + central directory without reading or copying video data; O(KB) instead of O(video size)
- **Streaming fallback for old projects** — legacy .fcproj files that can't use in-place rewrite are saved via 8 MB streaming copy instead of loading the entire video into memory
- **Window dragging** — switched to OS-native Aero Snap support for frameless window drag-to-move
- **Zoom clamping** — improved viewport clamping in the compositor to prevent out-of-bounds panning
- **Cursor rendering** — adjusted cursor height in the video exporter for consistency across zoom levels
- **QMessageBox styling** — dialogs now use the dark theme stylesheet

### Fixed

- **Export jitter** — smooth pan/zoom transitions no longer exhibit 1-pixel jumps from integer truncation of crop coordinates
- **Timeline track end** — end timestamp no longer exceeds track duration
- **Zoom-out condition** — corrected threshold check for zoom-out keyframe detection
- **Thumbnail worker cancellation** — workers are safely cancelled and terminated on shutdown
- **Overlap prevention** — enhanced two-phase overlap prevention for zoom segments to handle edge cases in activity analysis
- **Mouse settlement filtering** — mouse settlements are no longer used as zoom triggers, reducing false-positive zoom events

## [0.1.2] — 2025-12-17

- Build badge added to README
- CI fix: ignore markdown files in push/pull triggers
- CI fix: zip release artifact before GitHub release upload

## [0.1.1] — 2025-12-16

- Initial public release with README video
