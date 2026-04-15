# Fenster — Work History

## Current Focus: Narration Guidance Prompt

### 2026-04-15T23:04:02.621Z — Optional Narration Guidance Prompt

**Status:** ✅ Complete
**Validation:** 468 pytest tests passed

#### What Was Done

Added backend support for an optional user-entered narration guidance prompt that influences what the AI-generated script focuses on.

**New helper:** `_build_narration_system_prompt(guidance: str = "") -> str`
- Returns `_NARRATION_SYSTEM_PROMPT` unchanged when guidance is empty/whitespace
- Appends a "Creator guidance" block when non-empty
- Applied to all three AI call sites that can write or rewrite narration text:
  - `_generate_narration_segments` (single-pass and batch-synthesis paths)
  - `_polish_narration_segments_for_timing` (pacing/style rewrite pass)

**Persistence:** Session-only. Guidance is a generation-time parameter (same pattern as voice), not project content. Not written to `.fcproj`.

**Existing plumbing (already in branch):**
- `generate_narration(guidance_prompt: Optional[str] = None)` was already present
- `main_window._on_generate_narration_requested(voice, guidance)` was already present
- McManus had already wired the `Signal(str, str)` and `QPlainTextEdit` in `editor_panel.py`

#### Files Changed

- `followcursor/app/ai_service.py` — added `_build_narration_system_prompt`; updated `_generate_narration_segments` and `_polish_narration_segments_for_timing`
- `followcursor/tests/test_ai_service.py` — added `TestNarrationGuidancePrompt` (6 tests)
- `.squad/decisions/inbox/fenster-narration-guidance.md` — decision record

---

### 2026-04-15 — Narration Feature Spawn & Completion

**Status:** ✅ Complete  
**Validation:** All pytest (435 tests) passed

#### Implementation

Automated narration uses GPT-5.4 runtime with:
- Sequential multimodal batching (respects 50-image provider cap)
- Five-beat presentation structure: Context → Background → Prompt/Action → Walkthrough → Result
- Each beat becomes a `VoiceoverSegment` on voiceover track
- Combined markdown sidecar `<video_name>_voiceover.md`

#### Key Features

- **Multimodal inputs:** Zoom keyframes, annotations, mouse motion, clicks, keystrokes, sampled frames
- **Duration alignment:** Five-section timing plan + polish pass + TTS rate nudges
- **Ripple-delete safe:** Generated segments trimmed (not destroyed) during editing
- **Provider-safe:** Batching + rate-limiting pause for long runs

#### Decision

See `.squad/decisions.md`: *Narration Redesign — Backend & UI Alignment (2026-04-15)*

---

### 2026-04-15T22:56:10.313Z — Narration Timing & Chapter Knowledge Consolidation

**Mode:** Background agent spawned by Scribe; concurrent work with McManus
**Task:** Three backend sub-tasks: voiceover timing correction, chapter knowledge unification, keystroke/annotation removal

#### Scope Completed

1. **Voiceover Timing Alignment After Real TTS Duration Measurement**
   - Generated narration segments timestamped before actual TTS WAV durations existed; later clips overlapped once real audio landed
   - Fix: After AI worker finishes each segment, push later segments forward with measured durations (inferred placeholders for unsynthesized later beats)
   - Auto-TTS batch waits fully for each segment before queuing next retry/clip
   - Original planned timing windows preserved for one bounded retry even after later clips retimed forward (maintains subtle narration rate-correction)
   - Result: Voice track stays non-overlapping after real TTS durations land

2. **Voice Consistency Through Auto-Synthesis Batch**
   - Batch-selected/generated segment voice pinned through auto-synthesis and retries
   - Set Azure Speech's `speech_synthesis_voice_name` before constructing synthesizer
   - Avoids SDK/default fallback on plain-text TTS calls without SSML
   - Voice selection preserved across batch operations

3. **AI Chapters Now Unified with Narration Knowledge**
   - Chapter generation reuses `SharedRecordingKnowledge` artifact instead of separate heuristic/visual analysis pass
   - Shared artifact carries: frame samples, activity summary, click/key beats, zoom cues, annotations, provider-safe batch notes
   - Benefits: Chapters and narration aligned on same evidence (no drift); avoids paying twice for frame extraction + batch analysis; exported chapter beats stay aligned with narration story arc
   - Chapter behavior: Regeneration replaces only prior generated markers; manual chapters preserved; titles stay short/outcome-focused (no literal click/zoom narration)

4. **Backend Contract: Keystroke & Annotation Removal**
   - `RecordingSession.key_events` is legacy-load-only; new sessions have no keystroke stream
   - `load_project()` returns `keystroke_config = None` and `annotations = None`
   - Keyboard capture remains as compatibility shim only; no new keystroke events collected
   - Auto-zoom and chapter heuristics no longer use keystrokes (clicks stay as only input-activity cue)
   - AI narration/chapter context no longer uses keystrokes or annotations
   - Compatibility: Legacy `.fcproj` files load but data ignored and normalized away; dropped on save
   - UI can safely remove controls/docs without waiting on further backend work

#### Coordination Notes

- **Partner:** McManus (UI) — playback time readout custom paint, chapter flag rendering, annotations/keystroke section removal, backward-compatible project loading
- **Decision entries (now merged to decisions.md):**
  - `.squad/decisions/inbox/fenster-overlap-fix.md`
  - `.squad/decisions/inbox/fenster-ai-chapters.md`
  - `.squad/decisions/inbox/fenster-remove-annotations-keystrokes.md`
- Persistence layer (project_file.py) normalizes away keystroke_config and annotations on load/save
- AI service module stops requesting keystroke context for narration and chapter generation
- Voice track timing logic now measures actual WAV durations and retimes downstream segments

#### Validation

✓ Full pytest (435 tests) passed
✓ Compileall clean
✓ Timing logic verified with real TTS measurements

#### Learnings

- Draft timestamps before TTS synthesis + synchronous TTS queueing = overlap risk; measuring actual WAV durations and retiming downstream fixes the issue
- Shared recording-knowledge artifact (single cache of frame samples + activity + batch notes) naturally aligns chapters and narration; separate passes cause drift
- Backward-compatible keystroke_config/annotations handling (load-ok, clear-on-save) lets backend safely remove features without breaking old workflows

#### Handoff

Backend cleanup follow-up: Delete dormant compatibility pieces (`AnnotationCollection` / `KeystrokeOverlayConfig` serialization, AI-service annotation plumbing, legacy hook/model helpers) once branch no longer needs to open older `.fcproj` files.

Orchestration log: `.squad/orchestration-log/20260415T225610-fenster.md`

---

**For archived work history, see:** `history-archive.md`

---

## 2026-04-15T23:04:02.621Z — Voiceover Generation State Machine

**Status:** ✅ Complete  
**Session:** voiceover-generation-indicator  
**Coordination:** McManus (UI spinner + guidance field)

### What Was Done

Added `VoiceoverSegment.tts_generating` runtime-only flag to signal TTS synthesis in progress. State machine:
- Created segments: `False`
- Handed to `AIWorker.run_tts`: **`True`**
- `_on_ai_tts_result` completes: `False`
- `_on_ai_error` fires: `False` on all segments

**Persistence:** Never serialized. Segments loaded from `.fcproj` always start `False`.

**Equality:** `compare=False` — two segments differing only in synthesis state are equal (same authored content).

### Files Changed

- `followcursor/app/models.py` — `tts_generating` field with `compare=False`
- `followcursor/app/main_window.py` — toggle around synthesis workflow
- `followcursor/tests/test_models.py` — 5 regression tests

### Integration Point

McManus timeline renderer reads `tts_generating` flag to draw looping spinner overlay while synthesis is in flight.

### Validation

✓ 5 tests pass (default, equality, persistence, toggle, error handling)

---


---

## 2026-04-15T23:04:02.621Z — Narration Guidance Prompt Backend (Background Session)

**Status:** ✅ Complete | **Validation:** 468 pytest tests passed

### Outcome

Confirmed and completed guidance plumbing in ai_service.py. Guidance is injected into main narration generation pass, synthesis/batch pass, and timing-polish rewrite pass.

### Implementation

- **ai_service.py:** Guidance threaded through all three narration generation phases
- **test_ai_service.py:** Focused regression tests for guidance-aware narration

### Key Behavior

Guidance is optional, session-only (not persisted to `.fcproj`). Reuses existing `_build_narration_system_prompt()` pattern.

### Coordination

McManus built the UI field. Feature complete and working end-to-end.

