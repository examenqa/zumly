---
description: "Prepare a new release — bump the semver version, update the changelog, create a release branch, and run tests"
agent: "agent"
argument-hint: "Optional: any extra context or changes not captured in commit history"
---

You are preparing a new release of FollowCursor.

Read [release instructions](../instructions/release.instructions.md) for the full release checklist and conventions.

## Step 1 — Gather context

1. Read `followcursor/app/version.py` to get the **current version**.
2. Find the latest release tag: `git describe --tags --abbrev=0` (if no tags exist, use the initial commit).
3. Run `git log <last-tag>..HEAD --oneline` to get all commits since the last release.
4. For each commit, read its full message (`git log <hash> -1 --format=%B`) when the oneline summary is unclear.
5. Read the top of `CHANGELOG.md` to see how previous entries are formatted.

## Step 2 — Classify changes and draft changelog

Analyse each commit and classify it into changelog categories:

- **Added** — new features, new commands, new UI elements, new export formats
- **Changed** — behavioural changes, performance improvements, refactors with user-visible effects
- **Fixed** — bug fixes, crash fixes, correctness improvements
- **Removed** — removed features, deprecated items dropped

Ignore commits that are purely internal (CI config, merge commits, doc-only changes with no user impact).

Group related commits into a single changelog entry when they contribute to the same feature or fix.

## Step 3 — Ask for clarification

Use the `vscode_askQuestions` tool to ask the user:

- **Bump type**: Whether this is a **major**, **minor**, or **patch** release. Pre-select the most likely bump based on the classified changes:
  - If any changes are in **Added** → recommend **minor**
  - If changes are only in **Fixed** / **Changed** (no new features) → recommend **patch**
  - If any commits mention breaking changes or incompatible format changes → recommend **major**
- **Changelog review**: Present the full draft changelog (formatted exactly as it will appear in CHANGELOG.md) and ask the user to confirm, edit, or add entries. Include this as a free-text question so the user can adjust the wording.

Wait for the user's answers before proceeding.

## Step 4 — Apply the release

Using the user's confirmed bump type and changelog entries:

1. **Create a release branch**: `release/vX.Y.Z`
2. **Update `followcursor/app/version.py`** — set `__version__` to the new version.
3. **Update `CHANGELOG.md`** — insert a new section at the top (below the header) using today's date and the confirmed entries. Follow the Keep a Changelog format with bold feature names.
4. **Run tests** using the **Run Tests** VS Code task. Report results.
5. **Commit** with message `release: vX.Y.Z`

## Step 5 — Report next steps

Tell the user:
- The release branch is ready to merge to `main`
- After merging, tag with `git tag vX.Y.Z` and push: `git push origin vX.Y.Z`
- CI will automatically build the release artifacts, sign the MSIX, and create a GitHub Release
