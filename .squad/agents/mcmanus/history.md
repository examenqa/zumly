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
