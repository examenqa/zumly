---
description: "Use when releasing a new version, bumping the version number, updating the changelog, or tagging a release. Covers semver conventions, version file locations, and the full release checklist."
applyTo: "**/version.py"
---

# Release & Versioning

## Single Source of Truth

`followcursor/app/version.py` contains the canonical version:

```python
__version__ = "X.Y.Z"
```

CI and the MSIX build script (`Build-Msix.ps1`) read this value automatically.
Do **not** hard-code version strings anywhere else.

## Semantic Versioning Rules

| Bump   | When                                                         |
|--------|--------------------------------------------------------------|
| MAJOR  | Breaking changes to project file format, CLI args, or public API |
| MINOR  | New features, new export formats, new UI panels              |
| PATCH  | Bug fixes, performance improvements, documentation fixes     |

## Files That Must Be Updated

1. **`followcursor/app/version.py`** — bump `__version__`
2. **`CHANGELOG.md`** — add a new `## [X.Y.Z] — YYYY-MM-DD` section at the top with categorized entries (Added, Changed, Fixed, Removed)

## CHANGELOG Format

Follow [Keep a Changelog](https://keepachangelog.com/):

```markdown
## [X.Y.Z] — YYYY-MM-DD

### Added
- **Feature name** — short description

### Changed
- **Area** — what changed and why

### Fixed
- **Bug area** — what was broken and how it's fixed

### Removed
- **Item** — what was removed (if any)
```

Only include categories that have entries. Bold the feature/area name, then dash, then description.

## Release Checklist

1. Create a release branch: `release/vX.Y.Z`
2. Update `followcursor/app/version.py` with the new version
3. Add a new section to `CHANGELOG.md` with today's date
4. Run the **Run Tests** VS Code task — all tests must pass
5. Commit with message: `release: vX.Y.Z`
6. Merge to `main`
7. Tag: `git tag vX.Y.Z` on `main` — CI will build the release artifacts

## What CI Does on a Tag Push

- Runs tests
- Builds PyInstaller executable
- Builds & signs MSIX package (using version from `version.py`)
- Creates a GitHub Release with zip + MSIX assets
