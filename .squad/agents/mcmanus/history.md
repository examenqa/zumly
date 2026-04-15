# McManus — UI Dev History

## Recent Work

## 2026-04-15: Light Mode Theme Fix for Custom-Painted Widgets

**Mode:** Bug fix spawn  
**Task:** Fix timeline and transport controls rendering with dark colors in light mode

### Root Cause

Hard-coded dark paint values in custom `paintEvent` implementations. The widgets `_TimelineTrack`, `_TimelineTimeReadout`, and `_TimelineControlsHost` used literal hex colors (e.g., `#1b1a2e`, `#6c6890`) that bypassed the QSS-based theme switching.

### What Changed

- Added theme-aware color helpers to `tokens.py` (`bg_canvas()`, `bg_track()`, `fg_primary()`, etc.)
- Added `_dark_mode` state and `set_dark_mode()` method to all custom-painted timeline widgets
- Updated all paint methods to use the theme helpers
- Wired `MainWindow._apply_theme()` to propagate theme state to timeline
- Converted context menu styling from class constant to theme-aware method
- Added regression tests for theme propagation in `test_timeline_widget.py`

### Files Changed

- `followcursor/app/tokens.py`
- `followcursor/app/widgets/timeline_widget.py`
- `followcursor/app/main_window.py`
- `followcursor/tests/test_timeline_widget.py`

### Validation

✓ Full pytest (468 tests) passed  
✓ Compileall clean

---

## 2026-04-15: Auto Narration UI/Integration Spawn

**Mode:** Background agent spawned by Scribe  
**Task:** Implement editor UI for auto narration + timeline integration

### Scope
- Add **Generate narration** manual action to editor_panel.py
- Integrate narration worker from Fenster (ai_service.py)
- Add generated narration labels to timeline_widget.py
- Wire up progress/error UX for generation
- Guard against replacing prior generated narration (ask user)
- Update docs: USER_GUIDE, ARCHITECTURE, QUICKSTART, dedicated AI/projects guides
- Update README.md narration feature overview

### Coordination
- **Partner:** Fenster (Backend Dev) — develops narration worker + models + persistence
- **Decision:** Auto narration on existing voiceover track; editor asks before replacing generated; rewrites markdown sidecar after project load
- **Backward Compat:** Reuses export mixer, `.fcproj` persistence, timeline rendering; no new audio model

### Affected Files
- `followcursor/app/main_window.py` — wiring
- `followcursor/app/widgets/editor_panel.py` — **Generate narration** button
- `followcursor/app/widgets/timeline_widget.py` — narration segment labels
- `README.md`, `docs/ARCHITECTURE.md`, `docs/QUICKSTART.md`, `docs/USER_GUIDE.md`
- Optional: `docs/user-guide/ai.md`, `docs/user-guide/projects.md`

## Prior Work Summary

See `.squad/agents/mcmanus/history-archive.md` for detailed Q1–Q2 2026 work on Fluent 2 design system, dark theme calibration, documentation restructure, and icon integration.

## 2026-04-15: Narration Redesign Follow-up

**Mode:** UI/docs follow-up on top of Fenster's segmented narration backend  
**Task:** Reframe auto narration as presentation beats instead of a single recap track

### What changed
- Updated editor copy to say **NARRATION & VOICEOVER** and explain GPT-5.4-powered narration beats
- Updated timeline generated blocks to show section labels like **Context** and **Result**
- Updated main-window status, replace-confirmation, and edit/delete wording for generated narration **segments**
- Refreshed docs/README so they describe one combined markdown sidecar plus per-section WAV clips

### Coordination Notes
- Fenster's backend now emits generated narration as multiple `VoiceoverSegment` entries plus one combined markdown sidecar
- UI stays on the existing voice track, but copy now treats generated narration as a sequence of presentation beats
- Manual voiceover behavior remains unchanged and regeneration still replaces only generated narration

## 2026-04-15: Narration Flow Clarification

**Mode:** UI/docs/wiring refinement on top of the segmented narration backend  
**Task:** Make **Generate narration** explicitly build on the existing **Add voiceover** flow

### What changed
- Rewired narration generation to draft timestamped generated voiceover segments first, without using a narration-owned TTS pass
- Reused the normal voiceover synthesis path to turn those generated segments into WAV files in sequence
- Updated editor, timeline, status, and docs copy to say **generated voiceover segments** instead of narration clips where that framing matters

### Coordination Notes
- GPT-5.4 still owns the presentation-style script drafting, but the regular voiceover pipeline now owns placement + TTS synthesis
- Generated narration remains persisted as ordinary `VoiceoverSegment` entries plus one combined markdown sidecar

## Learnings

### 2026-04-15T21:06:05.876Z

- Generated narration feels much cleaner when the edit dialog opens `VoiceoverSegment.text` as the spoken line and keeps the section label separate from the saved markdown heading.
- Auto-TTS messaging needs to say speech starts on its own once generated segments land on the Voice track, then explain blockers in plain language instead of surfacing generic AI error copy.
- Visible narration copy works best when clicks and zooms stay behind-the-scenes cues instead of becoming spoken play-by-play.

## 2026-04-15T21:27:39Z — Scribe Roundup

Narration UI/docs alignment complete. Decision archived in .squad/decisions.md consolidated entry (Narration Redesign — Backend & UI Alignment).

**Status:** All pytest validations passed (435 tests) | compileall clean.

- Editor/timeline show plain spoken narration (no markdown headings)
- Section labels as metadata only
- Narration status copy: automatic TTS on Voice track placement
- Voice guidelines: presenter-style, avoid clicks/zoom narration
- Documentation updated: USER_GUIDE.md, QUICKSTART.md, copilot-instructions.md

Orchestration log: .squad/orchestration-log/2026-04-15T21-27-39Z-mcmanus.md

## 2026-04-15T22:56:10.313Z — Consolidated UI Spawns: Timeline, Chapters, Annotations Removal

**Mode:** Background agent spawned by Scribe; concurrent work with Fenster
**Task:** Three concurrent UI sub-tasks: playback time readout ghosting, AI chapters alignment, annotations/keystrokes removal

### Scope Completed

1. **Playback Time Readout Ghosting Fix**
   - Changed from multiple transparent `QLabel`s to single opaque custom-painted widget with grayscale antialiasing
   - Rapidly updating text on Windows composited surfaces was causing fringing artifacts
   - Regression test added to `followcursor/tests/test_timeline_widget.py`
   - Verified: timeline repaint ghost is compositing artifact in UI layer, not duplicate widgets

2. **AI Chapters & Timeline Integration**
   - Chapter generation now reuses `SharedRecordingKnowledge` from narration pipeline (same frame samples, activity cues, zoom beats, batch notes)
   - Chapters replace only prior generated markers; manual markers stay in place
   - Timeline chapter flags reviewable in-place: hover → show name, left-click → seek, right-click → jump/delete actions
   - Keeps chapters and narration aligned on same evidence instead of drifting from separate AI passes

3. **Annotations & Keystroke Overlays Removal (UI)**
   - Editor sections deleted (no annotation or keystroke editing)
   - Timeline Keys lane removed
   - Preview and export never render annotation or keystroke overlay data
   - Project loading stays backward-compatible: clears saved annotation/keystroke state on load and save
   - Legacy `.fcproj` files still load without crashing but removed data is normalized away

### Coordination Notes

- **Partner:** Fenster (Backend) — retiming voiceover segments, consolidating chapter knowledge, removing backend keystroke/annotation plumbing
- **Decision entries (now merged to decisions.md):**
  - `.squad/decisions/inbox/mcmanus-overlap-ui.md`
  - `.squad/decisions/inbox/mcmanus-ai-chapters.md`
  - `.squad/decisions/inbox/mcmanus-remove-annotations-keystrokes.md`
- Timeline widget updates handle opaque custom paint, chapter flag rendering, and Keys lane removal
- Editor panel updates remove annotation and keystroke sections
- Project file loading normalizes away legacy keystroke_config and annotations on load/save

### Validation

✓ Full pytest (435 tests) passed
✓ Compileall clean
✓ VS Code Run Tests task verified

### Learnings

- Custom-painted opaque widget stabilizes rapidly-updating text rendering on Windows composited surfaces (single paintable region beats multiple transparent labels)
- Shared recording knowledge artifact (frame samples + activity + batch notes) aligns chapters and narration naturally; separate analysis passes caused temporal drift
- Backward-compatibility on legacy project files (load-ok, clear-on-save) lets UI safely remove features without crashing old workflows

### Handoff

Fenster to follow with backend cleanup: dormant serialization (`AnnotationCollection` / `KeystrokeOverlayConfig`), AI-service annotation plumbing, legacy hook/model helpers — once branch no longer needs to open older `.fcproj` files.

Orchestration log: `.squad/orchestration-log/20260415T225610-mcmanus.md`

---

## 2026-04-15T23:04:02.621Z — Voiceover Generation Indicator + Narration Guidance

**Status:** ✅ Complete  
**Session:** voiceover-generation-indicator  
**Coordination:** Fenster (state machine) + Editor Panel guidance field

### Part 1: TTS Generation Spinner

Added looping amber spinner arc on voiceover segment pill whenever Fenster's `tts_generating` flag is `True`.

**Visual design:**
- Amber arc (`#fbbf24`) at right end of pill
- 120° sweep rotating continuously
- 80 ms timer (≈ 12.5 fps) → one rotation per 800 ms
- Suppresses static "…" ellipsis during generation

**Implementation:**
- `QTimer` only runs while any segment has `tts_generating=True`
- Called from `TimelineWidget.set_data()` when `voiceover_segments` provided
- Existing colour logic (teal filled / grey pending / teal-bright selected) unchanged

### Part 2: Narration Guidance Prompt

Added optional `QPlainTextEdit` in editor panel between voiceover description and **Generate narration** button.

**UI:**
- Label: **"Guidance (optional)"** — clearly non-required
- Placeholder: example-led, user-friendly language
- Height: fixed 64 px (one or two lines)
- Not persisted (per-recording, session-only)

**Signal change:**
- `generate_narration_requested` changed `Signal(str)` → `Signal(str, str)` (voice, guidance)

**Backend wiring:**
- `ai_service.generate_narration()` receives `guidance_prompt` parameter
- Forwarded to `_generate_narration_segments()` as `guidance` kwarg
- `_build_narration_system_prompt()` appends creator guidance block when non-empty

### Files Changed

- `followcursor/app/widgets/timeline_widget.py` — spinner timer, arc rendering
- `followcursor/app/widgets/editor_panel.py` — guidance field, label, signal update
- `followcursor/app/main_window.py` — `_on_generate_narration_requested` signature
- `followcursor/tests/test_timeline_widget.py` — 8 regression tests
- `followcursor/tests/test_editor_panel.py` — 6 regression tests

### Validation

✓ 14 new tests pass (8 timeline + 6 editor)  
✓ Signal routing backward-compatible  
✓ Spinner visual design aligns with Fluent 2 amber

---


---

## 2026-04-15T23:04:02.621Z — Narration Guidance Prompt UI (Background Session)

**Status:** ✅ Complete | **Validation:** 435+ pytest tests passed

### Outcome

Added a `Guidance (optional)` field in `followcursor/app/widgets/editor_panel.py` to let users steer narration generation. Guidance is session-only (not persisted).

### Changes

- **editor_panel.py:** New guidance QPlainTextEdit in voice section, optional flow
- **main_window.py:** Updated wiring to collect and pass guidance to AI service
- **test_editor_panel.py:** Regression tests for UI control

### Coordination

Fenster completed backend guidance threading in ai_service.py. Feature works end-to-end: users can emphasize what narration focuses on.

