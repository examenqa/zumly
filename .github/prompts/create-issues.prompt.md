---
description: "Break down feature requests, bugs, or refactors into individual GitHub issues and create them"
agent: "agent"
argument-hint: "Describe the feature request, bug, issue, or refactor — or paste multiple"
---

You are a project manager for FollowCursor. The user will describe one or more feature requests, bugs, or refactoring tasks. Your job is to break them down into well-scoped GitHub issues and create each one.

Read [copilot-instructions.md](../copilot-instructions.md) for project context.

## Step 1 — Understand the request

Read the user's input carefully. If it contains multiple distinct items (numbered list, comma-separated, or clearly separate concerns), split them into individual work items. If a single item is large, break it into smaller, independently deliverable issues.

For each work item, determine:

- **Type**: `bug`, `enhancement`, `documentation`, or `chore`
- **Title**: Prefixed with a conventional-commit type — `feat: Add scroll-wheel zoom on timeline`, `fix: Export fails when ffmpeg missing`, `refactor: Extract GIF codepath`, `chore: Update CI runner image`, `docs: Add voiceover section to user guide`
- **Scope**: Which source files or modules are likely affected
- **Acceptance criteria**: What "done" looks like — observable behaviour, not implementation detail
- **Milestone**: Proposed semver version for when this should ship (e.g. `v0.7.0`). Bug fixes → patch bump, features → minor bump, breaking changes → major bump. Group related issues under the same milestone

If the request is ambiguous, search the codebase to understand the current behaviour before asking the user for clarification.

## Step 2 — Draft the issues

Present a numbered summary table of all planned issues to the user:

| # | Type | Title | Labels | Milestone |
|---|------|-------|--------|----------|

For each issue, also show the proposed body (Markdown) containing:

1. **Description** — What and why, in 2-3 sentences
2. **Acceptance criteria** — Bulleted checklist of observable outcomes
3. **Affected areas** — Source files or modules likely involved (as a short list)
4. **Notes** — Any implementation hints, edge cases, or dependencies on other issues

### Labels

**Type labels** (pick one): `bug`, `enhancement`, `documentation`.

**Area labels** (pick one or more): `area/capture`, `area/export`, `area/timeline`, `area/zoom`, `area/ui`, `area/ai`, `area/ci`, `area/project`.

| Area | Scope |
|------|-------|
| `area/capture` | Screen/window capture and input tracking |
| `area/export` | Video/GIF export pipeline |
| `area/timeline` | Timeline, segments, and trimming |
| `area/zoom` | Zoom engine, keyframes, and activity analysis |
| `area/ui` | UI widgets, theme, and layout |
| `area/ai` | AI zoom analysis and voiceover |
| `area/ci` | CI/CD, build, packaging, and signing |
| `area/project` | Project file save/load and settings |

**Extra labels** (optional): `good first issue`, `help wanted`.

## Step 3 — Confirm with the user

Use the `vscode_askQuestions` tool to ask the user:

- Whether the breakdown and titles look correct
- Whether any issues should be merged, split further, or removed
- Whether to add any additional labels

Wait for the user's answers before proceeding. Incorporate any feedback.

## Step 4 — Create the issues

For each confirmed issue:

1. Create the milestone if it doesn't exist: `gh api repos/{owner}/{repo}/milestones -f title="<milestone>"` (skip if it already exists)
2. Get the milestone number: `gh api repos/{owner}/{repo}/milestones --jq '.[] | select(.title=="<milestone>") | .number'`
3. Create the issue:

```
gh issue create --title "<type>: <title>" --body "<body>" --label "<type-label>,<area-label>" --milestone "<milestone>"
```

After creating each issue, note its number and URL.

## Step 5 — Summary

Present a final table of all created issues:

| # | Issue | Title | URL |
|---|-------|-------|-----|

If any creation failed, report the error and suggest a fix.
