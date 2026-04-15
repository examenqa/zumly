# Changelog

All notable changes to FollowCursor are documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/), and this project adheres to [Semantic Versioning](https://semver.org/).

## [0.13.0] — 2026-04-15

### Added
- **Startup splash screen** — branded splash shown during app initialization, respects light/dark mode preference
- **Narration guidance prompt** — optional free-text field in the voiceover panel to steer AI script generation toward specific focus areas or key messages
- **Voiceover generation indicator** — animated amber spinner on voiceover timeline segments while TTS synthesis is in progress

### Changed
- **AI narration prompt** — reframed toward product benefit and ease-of-use storytelling; narrator now captures the meta-level value (what's easy, what's impressive) rather than literal on-screen mechanics
- **Light mode theming** — improved coverage for custom-painted timeline and voiceover widgets

### Removed
- **Annotations feature** — text, arrow, and highlight overlays removed from recording and export
- **Key strokes overlay** — keystroke recording and rendering removed from recording and export

## [0.12.0] — 2026-04-08

### Added
- **Draggable annotations** — text, arrow, and highlight annotations can now be repositioned by dragging them in the preview widget, with hover cursor feedback and zoom-corrected coordinates

### Changed
- **Larger font sizes** — bumped legacy font aliases (caption 11→12, body 13→14, subtitle 15→16, title 16→18, header 20→22) and TimeDisplay from 12px to 14px for improved readability
- **Higher-res window thumbnails** — further increased window capture resolution to 800×440 for sharper source picker previews
- **Horizontal keystroke overlay** — keystrokes now render side-by-side instead of stacked vertically, with automatic row wrapping when badges exceed available width
- **Larger background swatches** — increased swatch sizes (patterns 32→44px, gradients 28→36px, solids 24→28px) and adjusted column counts to eliminate orphan swatches

### Fixed
- **Record button icon** — changed from red-on-red (invisible) to white icon on red background
- **Dropdown popup styling** — softer border, larger items (36px), brand-tinted selection highlight, removed harsh outline on selected item
- **Arrow annotation drag** — uses midpoint delta so both endpoints translate together, keeping shape intact
- **Thumbnail fallback consistency** — async thumbnail callbacks now use matching 800×400 fallback size

## [0.11.0] — 2026-04-07

### Added
- **Mica/Acrylic backdrop** — Windows 11 Mica on the main window; Acrylic on the source picker dialog (requires Windows 11 22621+)
- **Fluent 2 light theme** — full light theme with LIGHT_* design token set; press Ctrl+T to toggle dark/light at runtime
- **FluentButton and FluentTabBar** — custom animated controls with Fluent 2 hover/press transitions and `fluentAnimated` property
- **Editor panel layout polish** — 32px header, 4px-grid spacing, 320px width, SVG icons replacing emoji glyphs
- **Fluent 2 spacing tokens** — filled gaps in `tokens.py` with the complete `SPACE_*` token set

### Fixed
- **QSlider handle** — 20px white circle matching Fluent 2 spec, using `T.FG_1` token
- **Widget style gaps** — QCheckBox indeterminate gradient, QRadioButton radial dot
- **Dark theme calibration** — aligned all neutral backgrounds to official Fluent 2 `colorNeutralBackground` values
- **Section labels** — replaced all-caps text with sentence-case per Fluent 2 spec
- **Source picker dialog** — aligned styling with Fluent 2
- **Window thumbnail resolution** — increased capture resolution for sharper previews

## [0.10.0] — 2026-04-07

### Added
- **Fluent 2 color system** — adopted Fluent 2 neutral palette with layered backgrounds, foreground tints, and elevation-based shadow tokens
- **Fluent UI System Icons** — replaced emoji icons throughout the UI with resolution-independent Fluent UI SVG system icons
- **Fluent 2 typography and spacing** — applied 4px spacing grid, type ramp tokens, and standardized corner radii across all controls
- **Fluent 2 widget styling** — aligned buttons, inputs, toggles, and panels with Fluent 2 component patterns

### Changed
- **Documentation rewrite** — comprehensive rewrite of User Guide, Quick Start, Architecture, and Contributing docs

### Removed
- **Auto-rebase workflow** — removed the auto-rebase GitHub Actions workflow

## [0.9.0] — 2026-04-07

### Added

- **Fluent 2 design system** — centralized design tokens (`tokens.py`) with 4px spacing grid, unified corner radii (4px controls / 8px containers), and status colors; `theme.py` refactored to reference tokens instead of hardcoded values
- **Fluent 2 visual polish** — depth shadows via `QGraphicsDropShadowEffect`, smooth expand/collapse animations, hover glow transitions, and keyboard focus rings across all interactive controls
- **SVG-based cursor** — replaced polygon cursor with compound-path SVG pointer icon with shadow depth; both QPainter (preview) and OpenCV (export) pipelines updated

### Changed

- **CI/CD workflows** — GitHub Actions workflows for automated build, test, and release; squad workflow placeholders filled with real CI/CD content

### Fixed

- **PySide6 warning** — use separate `findChildren` calls to avoid `qt_isinstance` deprecation warning
- **PR review fixes (PRs 42–65)** — input validation for keystroke filter modes, ZeroDivisionError guard in activity analyzer, chapter end-time fixes, annotation z-order in export, process leak in screen recorder, subprocess timeout after kill

## [0.8.0] — 2026-04-06

### Added

- **Keystroke visualization** — floating key badges show keyboard shortcuts during playback and export; configurable position, style, and filter mode; defaults to shortcuts-only for privacy safety
- **Click effect customization** — 8 built-in click effect presets (colors, styles) with configurable ripple/burst/highlight appearance; persists in project files and settings
- **Interactive annotations** — add text labels, arrows, and highlight boxes to recordings; timeline-aware rendering with normalized coordinates; dual QPainter/OpenCV pipeline
- **AI-powered scene chapters** — heuristic auto-detection of scene boundaries from activity gaps and position jumps; chapter markers on timeline; MP4 metadata export for YouTube chapters
- **Credentials test suite** — 22 tests for DPAPI encryption module covering roundtrips, edge cases, legacy compatibility, and platform fallbacks

### Changed

- **Video exporter refactored** — decomposed 300+ line export thread into composable phases with `GeometryComputer` class and typed result dataclasses; 12 new geometry unit tests

### Fixed

- **Resource leaks** — cv2.VideoCapture now releases in try/finally blocks; ffmpeg subprocess cleanup uses deterministic stdin→wait→kill sequence; voiceover playback uses defensive copies to prevent thread races
- **Keystroke security** — implemented filter_mode (all/modifiers-only/shortcuts-only) that was previously defined but ignored in the renderer; changed default from "all" to "shortcuts-only" to prevent password exposure; added security warning for "All Keys" mode
- **v0.7.1/v0.7.2 docs updated** — USER_GUIDE, ARCHITECTURE, CONTRIBUTING, and README updated with recording compression, DPAPI encryption, AI response caps, and dev signing changes

## [0.7.2] — 2026-03-28

### Changed

- **Recording compression** — switched intermediate recording codec from huffyuv to H.264 (CRF 18, ultrafast), reducing temp file sizes from ~50 GB/min to under 1 GB/min for 4K recordings

### Fixed

- **Zip Slip vulnerability** — project file loading now validates all ZIP member paths before extraction, preventing path traversal attacks (CWE-22)
- **API key storage** — AI API keys are now encrypted with Windows DPAPI before storing in the registry, instead of plaintext
- **AI response cap** — AI zoom analysis responses are capped at 50 sections to prevent excessive memory usage from malformed LLM responses
- **Temp file leak** — previous recording temp files are cleaned up when starting a new recording
- **Temp file collision** — voiceover merge now uses unique temp filenames instead of a fixed path
- **Temp directory leak** — project extraction directories are tracked and cleaned up on exit
- **Region header typo** — fixed `x-ms-region` header name in Azure Speech region extraction
- **Test quality** — strengthened test assertions to verify actual logic behavior instead of trivially passing

## [0.7.1] — 2026-03-27

### Changed

- **MSIX build script** — fixed XML declaration corruption by anchoring version regex to the `<Identity>` element; write manifest without BOM for MakeAppx compatibility; strip stray quotes from PFX path
- **CI workflow** — release job can now be triggered manually via workflow_dispatch for testing Azure signing without creating a GitHub Release
- **Dev signing** — added VS Code task to create a self-signed certificate for local MSIX sideloading; certificate and PFX output to `.certs/` folder

### Fixed

- **Timeline widget** — fixed indentation error in `video_segments` assignment that prevented app launch

## [0.7.0] — 2026-03-25

### Added

- **Video segments** — new VideoSegment model with timeline selection, deletion (ripple delete), undo/redo, and export support
- **Split at playhead** — split any timeline segment at the playhead position via context menu "Split here" or keyboard shortcut
- **Per-segment playback speed** — set independent playback speed for each video segment from the editor panel
- **Scroll-wheel zoom on timeline** — zoom in/out on the editor timeline with the mouse scroll wheel; coordinate mapping keeps the pointer position stable
- **Untrim by dragging handles** — drag trim handles back outward to restore previously trimmed regions
- **Hide trimmed regions** — trimmed areas are hidden from the timeline and playback is blocked outside the trim range
- **Auto-rebase workflow** — GitHub Actions workflow automatically rebases all open PRs when main is updated; PRs labeled `no-rebase` are excluded
- **Merge conflict resolution prompt** — `/resolve-conflicts` prompt to rebase conflicted PR branches onto main, resolve conflicts, run tests, and force-push
- **PR artifact comments** — CI posts a comment with a link to the build artifact on each PR; previous comments are cleaned up automatically

### Changed

- **MSIX signing** — migrated from deprecated NuGet-based signing to `azure/artifact-signing-action` for Azure Trusted Signing
- **Azure Trusted Signing labels** — renamed parameter labels from "(CI)" to "(local Azure signing)" for clarity
- **PR artifact naming** — shortened commit SHA in artifact names and set retention days dynamically

### Fixed

- **Split position** — context menu "Split here" now splits at the playhead position instead of the mouse click position
- **Voiceover deletion** — overlap check now uses `duration_ms` instead of timestamp-only filter to avoid deleting the wrong voiceover
- **Duplicate zoom reset** — removed duplicate `video_segments` reset in `_reset_session()` that could cause data loss
- **Segment retime logic** — simplified by removing dead overlap handling code for non-overlapping segments

## [0.6.0] — 2026-03-25

### Added

- **MSIX packaging** — Windows app package with .fcproj file association, automated build script, and CI integration for tagged releases
- **Azure Trusted Signing** — CI signs MSIX packages via Azure Trusted Signing with OIDC; includes idempotent setup script for provisioning signing resources
- **Error log file** — RotatingFileHandler writes ERROR+ entries to %LOCALAPPDATA%/FollowCursor/error.log (2 MB, 3 backups) with timestamps, module, file/line, and full tracebacks
- **Discard recording** — discard a recording without saving, with improved error handling in AI worker

### Changed

- **Encoder fallback chain** — removed h264_mf (Media Foundation) from automatic fallback; chain is now NVENC → QSV → AMF → libx264 for consistently higher quality
- **Build scripts** — converted batch scripts to PowerShell with Verb-Noun naming convention, moved into scripts/ folder

### Fixed

- **Remux timeout on long recordings** — dynamically scale ffmpeg remux timeout based on recording duration instead of fixed 60s limit
- **Discard permission error** — release video handle before deleting temp file to avoid WinError 32 on Windows

## [0.5.0] — 2026-03-05

### Added

- **Mermaid architecture diagrams** — replaced ASCII diagrams in ARCHITECTURE.md with 12 interactive Mermaid diagrams covering high-level overview, recording flow, zoom interpolation, activity analyzer pipeline, export pipeline, encoder fallback, AI data flow, widget communication, and project file structure
- **Phased export status messages** — export progress now reports what's actually happening: preparing video, building background & frame, rendering/encoding, and finalizing

### Changed

- **Export performance** — pre-compute device-frame mask once instead of per-frame, eliminating ~12M NumPy operations per frame in the zoomed-device-frame path
- **CFR floor lowered to 24 fps** — reduced constant-frame-rate minimum from 30 fps to 24 fps (cinematic standard), cutting output frame count by ~20 % for low-fps recordings

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
