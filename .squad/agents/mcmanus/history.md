# McManus — UI Dev History

## Recent Work

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
