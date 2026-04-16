# McManus — UI Dev History Archive

**Archived:** 2026-04-15T17:48:11.995Z  
**Reason:** Primary history.md reached 42KB; summary extracted for reference.

## Summary of Completed Major Work (Q1–Q2 2026)

### Design System & Theming
- **Fluent 2 Full Implementation (Issue #100)** — Typography ramp, shape tokens, spacing grid (2–64px), motion curves, animation framework
- **Dark Theme Calibration (Issue #113)** — BG_LAYER and accent color updates to match Fluent 2 spec
- **Spacing Token Expansion** — Added SPACE_NONE, SPACE_28, SPACE_36, SPACE_52, SPACE_56 to complete design token coverage

### Documentation & Icons
- **Issue #132 — Fluent Icons in MkDocs** — Integrated Fluent SVG icon set, created custom icon shortcodes (:fluent-*:), enabled rich media in docs
- **Issue #134 — User Guide Restructure** — Split USER_GUIDE.md into 8 pages under docs/user-guide/, established single-sentence leads and numbered steps pattern, updated mkdocs.yml navigation

### UI Fixes & Polish
- **PR #110 — Source Picker Icon State Management** — Fixed icon color inconsistencies; dynamically update tab/refresh icons based on state (FG_PRIMARY for selected, FG_2 for deselected)
- **PR #121 — Missing Token Updates** — Applied BG_LAYER values for dark theme calibration
- **PR #123 — Spacing Token Completion** — Added SPACE_NONE = 0 to complete Fluent 2 spacing token set

## Learnings & Conventions
- Fluent 2 motion: Use DURATION_FASTER (100ms) + CURVE_EASY_EASE for standard transitions
- Icon color state: Wire `currentChanged` signals to update colors dynamically rather than using fixed palettes
- Design token coverage: Always include "none" values (RADIUS_NONE, SPACE_NONE) alongside ranges for expressiveness

## Ongoing/Pending
- Auto Narration UI integration (2026-04-15 spawn)
