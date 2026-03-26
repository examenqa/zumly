---
description: "Use when implementing a GitHub issue — covers branching strategy, commit conventions, testing requirements, and PR workflow for FollowCursor"
---

# Implementing a GitHub Issue

## Issue title convention

Issue titles use a conventional-commit prefix:

- `feat:` — new feature or capability
- `fix:` — bug fix
- `refactor:` — restructuring without behaviour change
- `chore:` — maintenance, CI, dependencies
- `docs:` — documentation only

## Branch naming

Create a branch from `main` before making changes. The branch prefix matches the issue title prefix:

- Bug fix: `fix/<short-kebab-description>` (e.g. `fix/export-silent-failure`)
- Feature: `feat/<short-kebab-description>` (e.g. `feat/scroll-wheel-zoom`)
- Refactor: `refactor/<short-kebab-description>`
- Docs-only: commit directly to `main` (no branch needed)

## Implementation checklist

1. **Read the issue** carefully — understand acceptance criteria before writing code
2. **Explore** relevant source files to understand current behaviour
3. **Implement** the change following project coding conventions (see [copilot-instructions.md](../copilot-instructions.md))
4. **Add or update tests** in `followcursor/tests/` when the change touches logic (models, zoom_engine, activity_analyzer, utils, frames, backgrounds, project_file, ai_service)
5. **Run tests** using the **Run Tests** VS Code task — all tests must pass
6. **Update documentation** if the change affects user-facing behaviour (USER_GUIDE.md, QUICKSTART.md, ARCHITECTURE.md, README.md, or instruction files)
7. **Commit** with a descriptive message referencing the issue: `fix: <description> (#<number>)` or `feat: <description> (#<number>)`

## Coding conventions (quick reference)

- Type hints on all function signatures
- `logging.getLogger(__name__)` — no bare `print()`
- Signals/slots for inter-component communication
- Background threads for heavy work (recording, export, hooks)
- PySide6 widgets only (no QML, no .ui files)
- Dark theme via QSS in `theme.py`

## What NOT to do

- Do not add features beyond the issue scope
- Do not refactor unrelated code
- Do not add comments or docstrings to code you didn't change
- Do not skip tests

## Avoiding merge conflicts

When working on multiple issues in parallel, follow the **Parallel Work & Merge Conflicts** section in [copilot-instructions.md](../copilot-instructions.md):
- Check which files the issue will touch and avoid overlapping with other in-progress PRs
- Prefer issues that target different file-area groups (UI, capture, export, zoom, data, docs)
- A GitHub Actions workflow automatically rebases open PRs when `main` changes
