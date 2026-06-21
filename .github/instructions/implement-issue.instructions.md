---
description: "Use when implementing a GitHub issue — covers Zumly-specific conventions for branching, testing, labels, and documentation"
---

# Implementing a GitHub Issue — Zumly Conventions

> Generic workflow (fetching issues, creating branches, implementation checklist, conflict resolution) is handled by the **work-issues**, **create-issues**, and **resolve-conflicts** skills. This file covers **project-specific** conventions only.

## Testing

- Run tests via the **Run Tests** VS Code task (do not run pytest manually)
- Tests live in `zumly/tests/` — one `test_<module>.py` per source module
- Modules under test: models, zoom_engine, activity_analyzer, utils, frames, backgrounds, project_file, ai_service

## GitHub Issue Labels

**Type labels** (pick one): `bug`, `enhancement`, `documentation`.

**Area labels** (pick one or more):

| Label | Scope |
|-------|-------|
| `area/capture` | Screen/window capture and input tracking |
| `area/export` | Video/GIF export pipeline |
| `area/timeline` | Timeline, segments, and trimming |
| `area/zoom` | Zoom engine, keyframes, and activity analysis |
| `area/ui` | UI widgets, theme, and layout |
| `area/ai` | AI zoom analysis and voiceover |
| `area/ci` | CI/CD, build, packaging, and signing |
| `area/project` | Project file save/load and settings |

**Extra labels** (optional): `good first issue`, `help wanted`.

## Documentation updates

When a change affects user-facing behaviour, update the relevant docs:

- `docs/USER_GUIDE.md`, `docs/QUICKSTART.md`, `docs/ARCHITECTURE.md`
- `zumly/README.md`
- `.github/copilot-instructions.md` and `.github/instructions/` if conventions change

