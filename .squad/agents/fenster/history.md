# Fenster — Work History

## Completed Work

### 2025-01-04 — Issue #53: Refactor video_exporter.py export thread
**Branch:** `squad/53-refactor-exporter`  
**PR:** https://github.com/sabbour/followcursor/pull/58

Decomposed the 300+ line `_run()` method into composable, testable phases:

- **Extracted `GeometryComputer` class** — Pure-logic bezel/device layout calculations with no Qt or OpenCV dependencies in the constructor. Enables unit testing geometry without video files.
- **Added typed result dataclasses** — `VideoProbeResult` and `GeometryResult` for explicit phase outputs instead of raw dicts/floats.
- **Refactored phases**:
  - `_probe_video()` — Probes source metadata, reconciles FPS/frame-count discrepancies, determines output dimensions
  - `_compute_geometry()` — Uses `GeometryComputer` to calculate device/screen layout, builds static canvas layers (background, bezel, masks)
- **12 new unit tests** covering:
  - No-frame mode (landscape, portrait video on canvas)
  - Standard bezel geometry, aspect ratio preservation
  - Edge cases: small canvas, high padding, zero bezel width
  - All 5 built-in frame presets

**Testing:** All 347 tests pass (339 existing + 12 new). Export behavior unchanged across all formats (MP4, GIF) and frame types.

## Learnings

### Pure-Logic Extraction Pattern
When refactoring complex methods with tangled dependencies (Qt, OpenCV, FFmpeg), extract **pure-logic classes first**. The `GeometryComputer` class demonstrates this:
- **Constructor takes only primitives** (ints, dataclasses) — no Qt/OpenCV objects
- **Single `compute()` method returns a dict** — deterministic, easily testable
- **No side effects** — no logging, no file I/O, no state mutation

This pattern enables:
1. **Unit testing without mocking** — no need for fake `cv2.VideoCapture` or `QSettings`
2. **Property-based testing** — can fuzz inputs, verify invariants (e.g., screen always fits in canvas)
3. **Debugging in isolation** — reproduce geometry bugs without full export pipeline

### Phase Decomposition Strategy
The export pipeline had 5 implicit phases buried in one method. Key to successful refactoring:
1. **Start with phases that have clear I/O boundaries** — probe (reads metadata, returns dataclass), geometry (reads dimensions, returns canvas/masks)
2. **Extract one phase at a time** — don't refactor everything at once
3. **Keep original behavior intact** — run full test suite after each phase extraction

Remaining phases to extract (future work):
- `_prepare_audio()` — voiceover merge
- `_render_frames()` — frame loop, zoom/cursor application
- `_finalize()` — FFmpeg encoding with fallback chain

### Dataclass vs Dict for Phase Results
Originally considered returning raw dicts from phase methods. Switched to typed dataclasses (`VideoProbeResult`, `GeometryResult`) because:
- **Type safety** — IDE autocomplete, type checker catches missing fields
- **Self-documenting** — field names + types make intent clear without docstrings
- **Testable** — can construct test fixtures without worrying about dict key typos

Trade-off: more boilerplate, but worth it for long-lived code that multiple devs will touch.

### Windows ctypes + Geometry Calculations
Bezel geometry uses floating-point math with `np.float32` arrays. Critical to:
- **Clamp values before casting to int** — avoid negative coordinates or out-of-bounds indices
- **Ensure even dimensions** — H.264 requires even width/height; geometry must round correctly
- **Test edge cases** — portrait video, small canvas (640×480), ultra-wide monitors

The geometry computer validates these constraints in pure logic before OpenCV touches any pixels.

### 2025-01-06 — Issue #62: Keystroke viz exposes passwords — filter modes not implemented
**Branch:** `squad/62-keystroke-security`  
**PR:** https://github.com/sabbour/followcursor/pull/63

Critical security fix: keystroke overlay showed ALL typed characters (including passwords) by default. Filter modes were defined but never implemented in the renderer.

**Changes:**
- **Implemented filter_mode logic in `keystroke_renderer.py`:**
  - Modified `_group_keystrokes()` to accept `filter_mode` parameter
  - Created `_should_show_group()` helper to filter keystroke groups based on mode
  - "all" mode shows everything (user explicitly chose this)
  - "modifiers-only" shows only keystrokes with Ctrl, Alt, or Win modifiers
  - "shortcuts-only" shows only keyboard shortcuts (Ctrl+X, Alt+Tab, etc.), filters single character presses
- **Changed default from "all" to "shortcuts-only":**
  - Updated `KeystrokeOverlayConfig` dataclass default
  - Updated `from_dict()` for backward compatibility
  - Updated UI combo box default selection
- **Added security warnings in `editor_panel.py`:**
  - Dynamic tooltip updates when filter mode changes
  - ⚠️ Warning when "All Keys" selected about passwords/sensitive input
  - Helpful descriptions for safer modes

**Testing:** All 347 tests passed.

## Learnings

### Security by Default: Safe Defaults Trump Convenience
The original "show all keystrokes" default was convenient for demos but dangerous for real-world use. Key lessons:

1. **Default to the safest option** — "shortcuts-only" hides passwords by default. Users who want full keystroke capture must explicitly opt in.
2. **Progressive disclosure of risk** — The "All Keys" mode now shows a warning tooltip (⚠️) so users understand the security implications before choosing it.
3. **Filter at the source** — The filter happens in `_group_keystrokes()` BEFORE grouping/rendering, not after. This ensures filtered keystrokes never make it into the rendering pipeline, even in edge cases.

### Filtering Before Grouping vs. After
Initially considered filtering after keystroke groups were formed, but realized this creates a gap:
- **After-grouping filter:** A password like "MyPass123" might form a group, then get filtered as a whole, but intermediate state could leak in logs or intermediate data structures.
- **Before-grouping filter (chosen approach):** Individual keystrokes are filtered immediately, so they never participate in grouping. This prevents even partial passwords from being grouped together.

Trade-off: Slightly more complex filtering logic (track VK codes through grouping), but eliminates entire classes of security bugs.

### Modifier Key Detection: Win32 VK Code Ranges
Windows virtual key codes for modifiers are not contiguous:
- **Ctrl/Alt/Win modifiers:** 0x11, 0x12, 0x5B, 0x5C, 0xA2-0xA5 (used for "shortcuts-only" filter)
- **Shift keys:** 0x10, 0xA0, 0xA1 (NOT considered a modifier for "shortcuts-only" — Shift+A is still just typing)

The filter uses a frozenset for O(1) lookup. Critical to exclude Shift from the modifier set, otherwise typing capital letters would be shown as "shortcuts" when they're really just normal text entry.

### Dynamic Tooltip Updates: User Feedback Loop
The keystroke filter dropdown initially had a static tooltip listing all three modes. Changed to dynamic tooltips that update when the selection changes:
- **"All Keys":** Shows prominent ⚠️ warning about passwords
- **"Modifiers Only":** Explains what will be shown
- **"Shortcuts Only":** Explains safer behavior

This creates a feedback loop: users see the security implication RIGHT as they change the setting, not buried in documentation. This reduces accidental password leaks during tutorial recordings.

## Issue #133 — README accuracy (fix/133-readme-accuracy)
- Fixed screen_recorder.py docstring line 6 (was: "lossless AVI", now: H.264 CRF 18 ultrafast MP4)
- Added 8 missing features to README: AI Smart Zoom, Voiceover/TTS, Chapters, Annotations, Keystroke Overlay, Click Effects, Pan Path Points, Segment Deletion
- Fixed architecture table Recording Pipe entry
- PR opened for #133

## Issue #133 — README Accuracy (2026-04-07)

**Branch:** fix/133-readme-accuracy  
**PR:** #139

### Summary

Fenster identified and corrected multiple README inaccuracies:

1. **Added 8 missing features** with user-facing language:
   - Segment deletion
   - Keystroke overlay
   - Chapters
   - Annotations
   - Pan/zoom scripting (pan path points)
   - Multiple background presets
   - Multiple device frames
   - Zoom activity analysis

2. **Fixed architecture table** — Updated HuffYuv reference to H.264 (actual codec used in export)

3. **Fixed docstring** — Corrected return type documentation in `screen_recorder.py`

### Outcome

✅ PR #139 opened — Ready for review. No merge conflicts expected with active branches (#138, #140).

### Notes

- README now reflects current complete feature set
- Maintains user-facing language (no jargon, "you" focus)
- Tech stack table remains accurate

## Learnings

### 2026-04-15T17:48:11.995Z — Automated narration core

- **Architecture decision** — Automated narration is persisted as a regular `VoiceoverSegment` with `source="generated"`, plain spoken text in `text`, markdown in `script_markdown`, and the last exported markdown location in `script_path`. This keeps export audio muxing and `.fcproj` save/load on the existing voiceover path.
- **Pattern** — Build narration prompts from two layers: chronological frame samples every 5 seconds plus scored activity moments from typing, clicks, and cursor motion. Feed markdown back through a markdown-to-speech normalization step before TTS so sectioned scripts stay natural when spoken.
- **User preference** — Ahmed wants a single full-video narration script, one TTS track, a manual editor trigger, `<video_name>_voiceover.md` output, and narration state that survives `.fcproj` save/load.
- **Key file paths** — `followcursor/app/ai_service.py`, `followcursor/app/models.py`, `followcursor/tests/test_ai_service.py`, `followcursor/tests/test_models.py`, and `followcursor/tests/test_project_file.py`.

### 2026-04-15T19:11:54.918Z — Narration image budgeting

- **Root cause** — A strict 5-second timeline cadence already hits 51 screenshots at around 250 seconds once the final frame is included, so long narration runs can exceed the provider's 50-image multimodal cap even before extra activity cues are added.
- **Fix pattern** — Build the full candidate plan first, then in shared `ai_service.py` dedupe same-time cues, always keep the opening/closing frames and non-timeline activity moments, and spend the remaining image budget on evenly spread timeline samples. Apply the cap again right before payload assembly as a defensive guard so the prompt never drifts over the limit.
- **Regression anchor** — `followcursor/tests/test_ai_service.py` now proves the capped plan preserves activity moments, keeps broad temporal coverage, and never emits more than 50 narration images.

### 2026-04-15T19:11:54.918Z — Narration batching + zoom cues

- **Design update** — The better long-run fix is multi-pass narration analysis, not a single global trim. `generate_narration()` now keeps the full 5-second-plus-cues plan, splits extracted frames into sequential multimodal batches under the provider image cap, pauses between calls to avoid burst rate limiting, then synthesizes the final script from the batch notes.
- **Signal update** — Automated narration now uses mouse movement, keystrokes, clicks, sampled frames, and explicit zoom keyframes. Zoom-ins are converted into `zoom cue` narration moments, fed into prompt moments, and inserted into the frame plan so editorial emphasis informs the script directly instead of only showing up indirectly through screenshots.
- **Regression anchor** — `followcursor/tests/test_ai_service.py` now verifies the over-cap path makes multiple provider-safe multimodal calls, carries zoom cues into the synthesis prompt, and still returns the full sampled timeline metadata.

### 2026-04-15T19:11:54.918Z — Narration annotation cues

- **Authoritative sources** — The narration path should read editorial intent from the same live state the editor/export path uses: `main_window.py` passes `self._zoom_engine.keyframes` as the authoritative zoom source and `self._annotations` as structured annotation data.
- **Pattern** — Treat annotations like first-class narration inputs instead of hoping screenshots capture them. Convert text/arrow/highlight annotations into `annotation cue` moments, include structured annotation summaries in both batch prompts and the final synthesis prompt, and still use sampled frames as visual evidence.
- **Regression anchor** — `followcursor/tests/test_ai_service.py` now checks that single-pass narration includes annotation text directly in the prompt and that batched narration carries annotation metadata through both slice-analysis requests and final synthesis.

## 2026-04-15: Auto Narration Feature Spawn

**Mode:** Background agent spawned by Scribe  
**Task:** Implement multimodal narration generation worker

### Scope
- Develop narration generation logic using 5-second frame samples + activity moments
- Implement `VoiceoverSegment` with `source="generated"` field
- Persist narration metadata in `.fcproj` (markdown script + TTS audio paths)
- Expose narration worker contract for UI integration (McManus)

### Coordination
- **Partner:** McManus (UI Dev) — will integrate worker and add editor action
- **Decision:** Auto narration persists on existing voiceover track; replaces prior generated narration while preserving manual segments
- **Backward Compat:** New voiceover fields optional; projects without narration unaffected

### Affected Files
- `followcursor/app/ai_service.py` — narration worker + generation logic
- `followcursor/app/models.py` — voiceover metadata for `source` field
- `followcursor/app/project_file.py` — `.fcproj` narration persistence

## Spawned Work — In Progress

### 2026-04-15 — Narration Image Cap Handling & Multi-Modal Inputs
**Branch:** `feat/fenster-narration-batching` (to be created)
**Context:** User (Ahmed Sabbour) requested batching and multi-modal narration inputs to fix long-run 400 errors from AI provider image limits

**Scope:**
- Replace hard 50-image trim in `ai_service.py` with sequential provider-safe batching
- Add pause between requests to mitigate rate limiting
- Expand narration inputs: mouse movement, clicks, keystrokes, frames, timestamps, zoom keyframes, annotations
- Convert annotations into explicit narrative cues
- Preserve manual voiceover and project persistence

**Affected modules:**
- `followcursor/app/ai_service.py` — Core narration generation
- `followcursor/app/models.py` — Enhanced narration input structures
- `followcursor/app/main_window.py` — Workflow integration
- `followcursor/app/project_file.py` — Persistence
- Tests: `test_ai_service.py`, `test_models.py`, `test_project_file.py`

**Status:** Spawned in background mode — awaiting implementation results
