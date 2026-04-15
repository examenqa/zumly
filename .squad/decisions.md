

## UI & Voiceover Timing Fixes — Playback, Chapters, & Annotations (2026-04-15)

**Status:** Complete | **Implementation:** McManus (UI) & Fenster (Backend)

### McManus: Playback Time Readout & Timeline Glyphs

**Playback time display:** Changed from multiple transparent `QLabel`s to a single opaque, custom-painted widget with grayscale antialiasing. Rapidly updating text on composited Windows surfaces was ghosting/fringing; consolidating to one paintable region stabilizes the visual without changing backend timing logic.

**AI Chapters & Timeline:** Chapter generation now reuses the same `SharedRecordingKnowledge` as AI narration, keeping frame samples, activity cues, zoom beats, and batch notes aligned. Chapters replace only prior generated markers; manual markers are preserved. Timeline chapter flags stay reviewable in-place (hover for name, click to seek, right-click for actions).

**Annotations & Keystroke Removal:** UI now treats annotations and keystroke overlays as removed product features. Editor sections deleted, timeline Keys lane gone, preview/export never render these data. Project loading remains backward-compatible (clears saved state on load/save for legacy `.fcproj` files).

### Fenster: Voiceover Timing Alignment & Voice Consistency

**Narration retiming:** Generated segments must be retimed after actual TTS WAV durations are known. Draft timestamps are created before speech synthesis; the fix now pushes later segments forward with measured durations (and inferred placeholders for unsynthesized later beats) to prevent overlap on the voice track.

**Auto-TTS batch continuity:** After AI worker finishes each segment, auto-TTS batch waits fully before queuing the next retry or clip. Original planned timing windows are preserved for one bounded retry even after later clips are retimed forward, maintaining subtle narration rate-correction behavior.

**Voice consistency:** Batch-selected/generated segment voice stays pinned through auto-synthesis and retries. Set Azure Speech's `speech_synthesis_voice_name` before constructing the synthesizer to avoid SDK/default fallback on plain-text TTS calls.

**Shared chapter knowledge:** AI chapters reuse the cached `SharedRecordingKnowledge` instead of a separate heuristic pass. Avoids paying twice for frame extraction/batch analysis and keeps exported chapter beats aligned with narration story arc. Chapter titles remain short, navigation-friendly, and outcome-focused (no literal click/zoom narration).

**Backend contract:** `RecordingSession.key_events` is legacy-load-only; new sessions have no keystroke stream. `load_project()` returns `keystroke_config = None` and `annotations = None`. UI can remove controls and docs without waiting for further backend work. Legacy project files still load but dropped on save.

---

## Narration Redesign — Backend & UI Alignment (2026-04-15)

**Status:** Complete | **Implementation:** Fenster & McManus

### Fenster: Backend Redesign

Automated narration now uses a dedicated `gpt-5.4` runtime path with:
- Provider-safe multimodal batching (respects 50-image caps via sequential chunking)
- Presentation structure: Context, Background, Prompt/Action, Walkthrough, Result (five beats)
- Timing-aware polish pass for badly-drifting first drafts
- Handoff to existing Add voiceover flow: each beat becomes a `VoiceoverSegment`, synthesized via normal TTS pipeline
- Combined markdown sidecar preserves full narration history

**Key rationale:** Previous single-track narration remained too close to closed-caption recap. Multiple section-level assets preserve presentation arc, keep edits safer, fit existing persistence paths, and reuse proven export/rendering/manual-edit flows. Per-segment TTS rate nudges handle duration drift within the standard voiceover pipeline.

### McManus: UI & Docs Alignment

Generated narration is treated as **five presentation-style voiceover segments** on the existing voice lane:

1. **Section naming:** Context, Background, Prompt/Action, Walkthrough, Result (not generic Segment 1/2/3)
2. **Text rendering:** Show plain spoken narration line; keep section label as UI metadata (avoid leaking markdown headings into edit boxes)
3. **Synthesized copy:** Narration status says speech auto-starts when segments land on Voice track; calm fallback when TTS unconfigured or another AI task running
4. **Voice style:** Presenter-style that explains the point — explicitly avoid play-by-play narration of clicks, cursor motions, or camera moves
5. **Documentation:** Updated USER_GUIDE.md, QUICKSTART.md, and copilot-instructions.md to describe GPT-5.4 narration generation, one combined sidecar, and normal voiceover synthesis

**Validation:** Full pytest (435 tests) passed | compileall clean

### User Directives Consolidated (2026-04-15)

1. **2026-04-15T19:43:15.109Z:** AI narration should run on GPT-5.4, produce multiple WAV segments, and adopt presentation voice (not closed-caption style)
2. **2026-04-15T19:43:15.109Z:** Narration segments integrate with existing Add voiceover flow for TTS synthesis
3. **2026-04-15T20:58:42.932Z:** Keep spoken narration free of headings/dividers; auto-synthesize TTS as segments land on timeline; authentic, concise voice avoiding AI-isms
4. **2026-04-15T21:06:05.876Z:** Avoid literal narration of clicks/zooms; no "zooming in on" phrasing
5. **2026-04-15T21:27:39Z:** Update documentation when work is done

*All directives applied as of 2026-04-15T21:27:39Z*

---

## PR Review Fixes — UI & Docs (McManus, 2026-04-06)

**Status:** Applied | **Items:** 25 fixes + 2 prior resolutions + 1 new test

### Key Architectural Choices

1. **Keystroke rendering in preview**
   - Added `key_events` and `keystroke_config` parameters to `compose_scene()` and preview widget
   - Extends compositor's public API but keeps keystroke rendering consistent between preview and export
   - Alternative (rendering outside compositor) would duplicate zoom/clip logic

2. **Annotation z-order alignment**
   - Moved annotations to render **before** cursor and click effects in `compose_scene()`
   - Order: video → annotations → cursor → clicks → keystrokes
   - Ensures cursor/clicks always visible on top of annotations

3. **Single overlay allocation in annotation renderer**
   - `render_annotations_cv` allocates one shared overlay copy per frame call instead of per-annotation
   - Each annotation blends its region and resets overlay for next one
   - Avoids O(n) full-frame copies for n annotations per frame

4. **HighlightBox opacity precedence**
   - Documented that `opacity` takes precedence over color alpha in both QPainter and CV renderers
   - No runtime behavior change — both renderers already used `opacity`
   - Color's alpha channel effectively ignored

---
*All decisions applied as of 2026-04-07T01:30:06Z*


## Fluent 2 Design Research (McManus, 2026-04-07)

**Status:** Complete | **Research Type:** UI Design & Windows 11 Alignment

### Summary

Comprehensive analysis of Windows 11 Fluent 2 design system and PySide6 implementation strategies. Full research document in .squad/agents/mcmanus/fluent2-research.md.

### Key Findings

1. **PySide6-Fluent-Widgets:** Mature, GPLv3-licensed library with 50+ Fluent-styled components (navigation, dialogs, cards, inputs). Supports acrylic effects, animations, and light/dark themes.

2. **Current FollowCursor Theme:** 70% aligned with Fluent 2 — uses Segoe UI Variable and modern dark palette, but needs:
    - Spacing normalization (4px grid)
    - Corner radius unification (4px/8px)
    - Drop shadows and state animations
    - Status colors (Warning/Info)

3. **Recommended Hybrid Approach:**
    - **Library:** Navigation sidebar, dialogs, source picker, standard controls (combobox, slider)
    - **Custom QSS:** Timeline (specialized), preview widget (compositing), title bar (frameless)
    - **Design tokens:** Create 	okens.py for spacing, radius, colors

4. **Implementation Phases:**
    - Phase 1: Pilot PySide6-Fluent-Widgets in source picker (1–2 weeks)
    - Phase 2: Refine custom QSS with spacing/radius fixes (2–3 weeks)
    - Phase 3: Design token system (1 week)
    - Phase 4: Animations & shadows (1 week)
    - Phase 5: Accessibility & polish (1 week)

5. **Quick Wins:**
    - Normalize spacing to 4px grid
    - Unify corner radius (4px elements, 8px containers)
    - Add semantic color tokens


## User Directive (Ahmed Sabbour, 2026-04-07)

**Status:** Captured | **Type:** Development Workflow

### Directive

Create branches and worktrees before major work. All significant changes should be developed on a dedicated branch with a git worktree, not directly on main.

**Rationale:** User request — established to maintain clean main branch and enable parallel development.

*Updated: 2026-04-07T07:33:49Z*


## Fluent 2 Phase 1 — Design Token Architecture (McManus, 2026-04-07)

**Status:** Applied | **Implementation:** Complete

### Context

FollowCursor's theme.py used hardcoded hex colors, inconsistent spacing (2–28px, not on grid), and mixed corner radii (3–18px). Phase 1 of the Fluent 2 alignment creates a token-based architecture.

### Decisions

1. **Token module at `followcursor/app/tokens.py`** — all design constants live here, imported as `T` by theme.py. No other module should hardcode color hex or spacing values.

2. **Spacing normalized to 4px grid** — named tokens: XXS=4, XS=8, SM=12, MD=16, LG=24, XL=32, XXL=48. Padding/margin values that were off-grid (2, 6, 10, 14, 18px) snapped to the nearest token.

3. **Corner radii unified** — RADIUS_SMALL=4px for buttons/inputs, RADIUS_MEDIUM=8px for containers/dialogs. Circular elements (status dots, scrollbar) keep half-width radius since those are shapes, not corners.

4. **Warning (#f59e0b) and Info (#3b82f6) status colors added** — with hover variants. Status dot QSS rules added so widgets can use `#StatusDotWarning` / `#StatusDotInfo`.

5. **Disabled states added** — ExportBtn, DiscardBtn, CtrlBtn, PlayBtn now have `:disabled` QSS rules using `BRAND_DISABLED`/`FG_DISABLED` tokens.

### Impact

- Theme refactor only — no widget code changes, no behavioral changes
- All 334 tests pass unchanged
- Future theme work should modify `tokens.py` values, not hardcode in QSS
- Branch: `feat/fluent2-phase1`

---
*All decisions applied as of 2026-04-07T07:49:22Z*


## Fluent 2 Color System & Elevation Adoption (McManus, 2026-07-23)

**Status:** Implemented | **PR:** #102 | **Issue:** #98

### Context

FollowCursor's Phase 1 token system (created Jan 2026) introduced semantic color names and spacing normalization, but used a custom purple-tinted dark palette. Phase 3 replaces these with authentic Fluent 2 values from Microsoft's design spec.

### Research Findings

#### Official Fluent 2 Palette Structure

1. **Neutral Colors**: grey[0] (black) through grey[100] (white)
   - Dark theme backgrounds use grey[4] → grey[20] range (lighter = elevated)
   - Dark theme text uses white → grey[36] range (darker = disabled)
   - Strokes use grey[4] (subtle) → grey[68] (accessible)

2. **Elevation System**: 5 shadow layers (not 2)
   - Shadow2: 2px blur, 1px offset — buttons, minimal cards
   - Shadow4: 4px blur, 2px offset — cards, list items
   - Shadow8: 8px blur, 4px offset — command bars, tooltips
   - Shadow16: 16px blur, 8px offset — dialogs, flyouts
   - Dark theme uses 28% key + 24% ambient shadow opacity

3. **Semantic Status**: Fluent 2 uses cranberry (danger), green (success), orange (warning) with tint/shade variants

### Design Decisions

#### 1. Backwards Compatibility via Aliases

**Decision**: Keep all Phase 1 token names (BG_CANVAS, BG_PANEL, FG_PRIMARY, etc.) as aliases mapping to the new Fluent 2 tokens.

**Rationale**: Existing QSS rules in theme.py, editor_panel.py, source_picker.py reference the old names. Breaking those would require touching ~300 lines of QSS and widget code. Aliases preserve backward compat while adopting the new system.

#### 2. Explicit Layer Naming (not "subtle"/"medium")

**Decision**: Use `layer0`, `layer1`, `layer2`, `layer3`, `layer4` for shadow levels instead of custom descriptive names.

**Rationale**: Direct 1:1 mapping with Fluent 2 documentation (Shadow2 = layer1, Shadow4 = layer2, etc.) makes cross-referencing easier. Developers can look up Fluent 2 elevation spec and know exactly which token to use.

**Legacy support**: Kept `subtle` → `layer2` and `medium` → `layer3` aliases for existing `apply_shadow()` calls.

#### 3. Grey Ramp Abstraction

**Decision**: Expose semantic names (BG_LAYER_1, FG_1, STROKE_1) instead of raw `GREY_4`, `GREY_84` tokens.

**Rationale**: Fluent 2 web components use `colorNeutralBackground1`, not `grey[16]`. Our Python tokens follow the same semantic abstraction. Comments document the grey[N] mapping for reference.

**Trade-off**: Slightly less obvious which exact grey shade is used, but better aligns with Fluent 2's intent-based token system.

#### 4. No Visual Redesign

**Decision**: This is a *spec alignment* pass, not a UX overhaul. The theme should look nearly identical before/after.

**Rationale**: FollowCursor's dark theme was already ~70% Fluent-aligned. This change makes the palette *officially correct* without disrupting users or requiring new screenshots/docs. Visual changes can come later as a separate design iteration.

**Result**: Colors shifted slightly (e.g., BG_PANEL #131221 → #1f1f1f), but the overall vibe remains the same dark purple-accented theme.

#### 5. Material Effects Tokens (Stub)

**Decision**: Added `MATERIAL_OVERLAY_ALPHA = 0.92` and `MATERIAL_CARD_ALPHA = 0.98` tokens, but didn't implement acrylic/mica blur yet.

**Rationale**: Fluent 2 has a material system (acrylic for transient UI, mica for app backgrounds). The tokens are placeholders for Phase 4 when we add backdrop blur effects.

### Implementation Notes

- **All 375 tests pass** (3 updated, 5 new)
- **No breaking changes** — all existing QSS works via aliases
- **Better Windows 11 alignment** — colors now match native Windows apps
- **Foundation for Phase 4** — material effects (acrylic/mica) can layer on top

### References

- [Fluent 2 Color System](https://fluent2.microsoft.design/color)
- [Web Alias Color Tokens](https://fluent2.microsoft.design/color-tokens/)
- [Elevation Spec](https://fluent2.microsoft.design/elevation)

---
*Applied as of 2026-04-07T10:34:00Z*

## User Directive: PR Review Thread Resolution Comments

**Status:** Captured | **Date:** 2026-04-07T08:51:57Z | **By:** Ahmed Sabbour

When resolving PR review threads after fixing them, always add a reply comment explaining what was done to address the feedback before resolving the thread.

**Rationale:** User request — captured for team memory

---
*Captured as of 2026-04-07T08:51:57Z*

## User Directive: GitHub Pages Documentation Site

**Status:** Captured | **Date:** 2026-04-07T08:55:38Z | **By:** Ahmed Sabbour

Create a real GitHub Pages docs site. The squad-docs.yml workflow should build and deploy documentation to GitHub Pages, not just validate markdown.

**Rationale:** User request — captured for team memory

---
*Captured as of 2026-04-07T08:55:38Z*

## User Directive: Stop Using Git Worktrees

**Status:** Captured | **Date:** 2026-04-07T09:49:15Z | **By:** Ahmed Sabbour

Stop using git worktrees. Work on branches in the main repo checkout instead so changes are visible in the editor and runnable locally.

**Rationale:** User request — worktrees are confusing because files aren't visible in the editor and can't be run locally.

---
*Captured as of 2026-04-07T09:49:15Z*

## Decision: Batch-Install Focus Rings via findChildren

**Status:** Applied | **Date:** 2026-07-22 | **Author:** McManus (UI Dev)

Use `self.findChildren((QPushButton, QComboBox))` at the end of `EditorPanel.__init__` to batch-install focus rings on all interactive children. This auto-covers future additions without per-widget boilerplate.

For `main_window.py` and `title_bar.py`, explicit calls are used since the buttons are fewer and spread across builder methods.

### Trade-off

- **Pro**: Zero maintenance — any new QPushButton or QComboBox added to EditorPanel automatically gets a focus ring.
- **Con**: No opt-out mechanism for individual widgets. If a button needs a shadow instead, it would need the focus ring removed manually. Currently not a problem since all EditorPanel controls are interactive.

---
*Applied as of 2026-07-22*

## Squad Workflow Architecture (Fenster, PR #93)

**Status:** Complete | **Date:** 2026-07-18

### Decisions

1. **squad-release.yml creates tags only, does not build** — build.yml already handles `refs/tags/v*` releases. Duplicating the build/MSIX/release logic would create maintenance burden. squad-release.yml extracts the version from `version.py`, checks if the tag exists, and creates it. build.yml takes over from there.

2. **squad-ci.yml excludes PRs to main** — build.yml triggers on `pull_request: branches: [main]` with full build + artifacts. squad-ci.yml only covers PRs to dev/preview/insider with fast test-only runs to avoid redundant CI minutes.

3. **squad-docs.yml uses ubuntu-latest** — Only workflow not on `windows-latest`. Markdown validation doesn't need Windows, and ubuntu runners are faster and cheaper.

4. **Insider releases use `vX.Y.Z-insider.<short-sha>` tag format** — Distinguishes insider builds from stable releases and provides commit traceability without bumping version.py.

### Impact

These workflows complete the Squad branch promotion pipeline: dev → (squad-ci) → preview → (squad-preview validates) → main → (squad-release tags) → build.yml releases.

---
*Complete as of 2026-07-18*

## User Directive: gh CLI Auth Switching

**Status:** Captured | **Date:** 2026-04-07T11:31:00Z | **By:** Ahmed Sabbour (via Copilot)

The `gh` CLI default auth is an EMU account (`asabbour_microsoft`) that cannot write to the personal `sabbour/followcursor` repo. All agents performing GitHub write operations (PR comments, thread resolution, issue edits, merges) MUST run `gh auth switch --user sabbour` first, then switch back afterward. Read `.squad/skills/gh-auth-switching/SKILL.md` for full details.

**Rationale:** EMU accounts are restricted from writing to personal/public repos. Agents were failing silently on PR comment/resolve operations until this was diagnosed.

---
*Captured as of 2026-04-07T11:31:00Z*

## User Directive: Automated Release on Milestone Completion

**Status:** Captured | **Date:** 2026-04-07T11:38:00Z | **By:** Ahmed Sabbour (via Copilot)

When all issues in a milestone are closed, Ralph should bump the version in `followcursor/app/version.py`, update `CHANGELOG.md`, commit, tag the release (`git tag vX.Y.Z`), push the tag, and create a GitHub release. Follow the release checklist in `.github/instructions/` and use the `release` skill if available.

**Rationale:** User request — releases should be automated as part of the Ralph work loop, not a separate manual step.

---
*Captured as of 2026-04-07T11:38:00Z*

## Fluent 2 Typography, Shapes, Spacing & Motion Token Adoption

**Status:** Implemented — PR #105 | **Author:** McManus (UI Dev) | **Date:** 2026-04-07 | **Issue:** #100

### Context

FollowCursor's Phase 1 token system (created Jan 2026) introduced semantic color names and basic spacing (4px grid), but typography and spacing used simplified values that didn't match Microsoft's official Fluent 2 specifications. Issue #100 required full alignment with Fluent 2's type ramp, shape system, spacing tokens, and motion design.

### Research Summary

#### Official Fluent 2 Specifications

1. **Typography** (https://fluent2.microsoft.design/typography)
   - Type ramp: Caption2 (10/14) → Display (68/92)
   - Font family: Segoe UI Variable (Windows 11), fallback to Segoe UI (Windows 10)
   - Font weights: Regular (400), Medium (500), Semibold (600), Bold (700)
   - Line heights critical for vertical rhythm (e.g., Body1 = 14px / 20px)

2. **Shapes** (https://fluent2.microsoft.design/shapes)
   - Global-Corner-Radius tokens: None (0px), 20 (2px), 40 (4px), 80 (8px), 120 (12px), Circular (9999px)
   - Default: 4px for most elements, 8px for large surfaces, 0px for edge-aligned UI

3. **Spacing** (https://fluent2.microsoft.design/layout)
   - 4px base grid with granular steps: 2, 4, 6, 8, 10, 12, 16, 20, 24, 32, 40, 48, 64
   - Odd values (2, 6, 10) for tight icon/text alignment
   - Size tokens: size20, size40, size60, size80, size100, size120, size160, etc.

4. **Motion** (https://fluent2.microsoft.design/motion)
   - 8 duration levels: Ultra Fast (50ms) → Ultra Slow (500ms)
   - 5 easing curves: EasyEase, EasyEaseMax, Decelerate (entering), Accelerate (exiting), Linear
   - Intent-based: use Decelerate for elements appearing, Accelerate for elements disappearing

### Design Decisions

#### 1. Complete Type Ramp with Line Heights

**Decision:** Implement all 10 Fluent 2 type levels (Caption2, Caption1, Body1, Body2, Subtitle2, Subtitle1, Title3, Title2, Title1, Display) with paired line heights.

**Rationale:**
- Fluent 2's type ramp is designed for hierarchy and scannability
- Line heights ensure proper vertical rhythm and readability
- Having the full ramp available supports future UI scaling and accessibility features

**Implementation:**
- Added 10 FONT_SIZE_* and 10 FONT_LINE_HEIGHT_* constants
- Legacy aliases (FONT_SIZE_BODY = FONT_SIZE_BODY_1) preserve backward compat

#### 2. Extended Spacing Tokens (13 levels)

**Decision:** Expand from 7 spacing tokens to 13, covering the full Fluent 2 spacer ramp (2px to 64px).

**Rationale:**
- Fluent 2's 4px grid includes odd values (2, 6, 10) for precision alignment
- More granular steps enable tighter control without hardcoded pixel values
- Maps 1:1 to Fluent 2 size tokens (size20, size40, size60, etc.)

**Trade-off:**
- SPACE_XXS changed from 4px → 2px (very tight spacing)
- Existing QSS using SPACE_XXS now gets 2px instead of 4px
- Risk: layouts may be too tight
- Mitigation: Reviewed all 8 usages in theme.py — all appropriate for 2px (status dot margins, small padding)

#### 3. Shape Token Naming and Aliases

**Decision:** Use human-readable names (RADIUS_SMALL, RADIUS_MEDIUM, RADIUS_LARGE) with Fluent 2 token references in comments.

**Rationale:**
- RADIUS_SMALL is clearer than GLOBAL_CORNER_RADIUS_40 in Python code
- Comments document the official Fluent 2 mapping (e.g., "Global-Corner-Radius-40")
- Developers can cross-reference the spec when needed

**Implementation:**
- 6 shape tokens: RADIUS_NONE (0), RADIUS_SMALL (4), RADIUS_MEDIUM (8), RADIUS_LARGE (12), RADIUS_XLARGE (16), RADIUS_CIRCULAR (9999)

#### 4. Motion Token Organization

**Decision:** Expose both duration constants and easing curve strings, with Qt helper functions for widget animations.

**Rationale:**
- Duration tokens (DURATION_ULTRA_FAST, DURATION_FASTER, etc.) are integers for Qt animations
- Easing curve strings (CURVE_EASY_EASE, etc.) are CSS cubic-bezier for future HTML/web export
- Helper functions (get_entering_curve, get_exiting_curve) abstract the Qt enum mapping

**Implementation:**
- 8 DURATION_* constants (50ms to 500ms)
- 5 CURVE_* string constants (cubic-bezier values)
- 3 helper functions in fluent_effects.py returning QEasingCurve.Type enums

#### 5. Animation API Enhancement

**Decision:** Add optional `easing` parameter to `install_hover_animation()` and `install_hover_bg_animation()`, defaulting to OutCubic (Fluent 2's curveEasyEase).

**Rationale:**
- Existing code can pass custom easing curves for entering/exiting animations
- Default remains OutCubic (smooth, general-purpose) for backward compat
- Enables intent-based motion: pass `get_entering_curve()` for fade-ins, `get_exiting_curve()` for fade-outs

**Backward compatibility:**
- Existing calls without `easing` param continue to work unchanged
- Default duration updated from DURATION_FAST (was 100ms) to DURATION_FASTER (100ms) — no change in value, just token rename for consistency

#### 6. Font Family Fallback Strategy

**Decision:** Use `"Segoe UI Variable", "Segoe UI", -apple-system, BlinkMacSystemFont, sans-serif` as the font stack.

**Rationale:**
- Segoe UI Variable is Windows 11 only (variable font with optical sizing)
- Windows 10 systems fall back to Segoe UI (static font)
- Cross-platform fallback (-apple-system, BlinkMacSystemFont) for potential future macOS support
- sans-serif ensures a readable fallback on any system

### Impact

- **No breaking changes** — all legacy token names preserved as aliases
- **375 tests pass** — no regressions in existing functionality
- **Theme.py inherits updates** — all QSS rules using token references automatically get new values
- **Foundation for Phase 4** — material effects (acrylic, mica) and advanced animations can now use motion tokens

### References

- [Fluent 2 Typography](https://fluent2.microsoft.design/typography)
- [Fluent 2 Shapes](https://fluent2.microsoft.design/shapes)
- [Fluent 2 Layout](https://fluent2.microsoft.design/layout)
- [Fluent 2 Motion](https://fluent2.microsoft.design/motion)
- [Qt QEasingCurve Documentation](https://doc.qt.io/qt-6/qeasingcurve.html)

---
*Applied as of 2026-04-07, PR #105*

## User Directives (Ahmed Sabbour, 2026-04-07)

**Status:** Captured | **Type:** Development Workflow — PR Review & Issue Management

### Three Directives

1. **PR Review Feedback Processing (11:08 UTC)**
   - Ralph must check open PRs for review comments (especially from Copilot PR reviewer) before reporting the board as clear
   - PRs with unresolved review threads are actionable work, not "awaiting review"
   - Ralph should spawn agents to address review feedback in the main triage loop

2. **Milestone Assignment During Triage (11:15 UTC)**
   - Ralph must assign a milestone to every issue during triage
   - Ensures every issue has a milestone set for project tracking and release planning

3. **GitHub Issue Assignment on Pickup (11:20 UTC)**
   - When Ralph picks up an issue for a squad member, immediately assign the issue on GitHub
   - Signals that work is in progress and prevents duplicate work from other agents
   - Use `gh issue edit N --add-assignee @me` or set "in progress" label

**Rationale:** User requests — these are workflow policies to prevent missed PR review feedback, ensure release planning visibility, and avoid duplicate work across parallel agent spawns.


## Release Trigger Policy (asabbour, 2026-04-07)

**Status:** Captured | **Type:** Development Workflow — Release Management

### Directive

Ralph should always trigger a release at the end of milestone work — defined as when the board is cleared (all PRs merged, no open issues).

**Implementation:**
- Ralph's work-check loop should invoke the `release` skill after confirming the board is clear
- Release is the final step before entering idle-watch
- This ensures milestone work naturally concludes with a versioned release for users

**Rationale:** User request — provides a clear handoff point between milestone completion and release, enabling automated release workflow.


## User Directive: Squad State Separation (asabbour, 2026-04-07)

**Status:** Captured | **Type:** Development Workflow — Commit Organization

### Directive

Never mix `.squad/` state changes with feature or code changes in the same commit or PR — **except** for `.squad/agents/*/history.md` files.

**Exception — agent history files:** `.squad/agents/*/history.md` documents an agent's understanding of the code it worked on. These files are contextually tied to the code changes they accompany and may travel in the same commit or PR as the code they describe.

**Must always be separate:** `decisions.md`, `orchestration-log/`, `log/`, `ceremonies.md`, `team.md`, `routing.md`, and any other squad orchestration/state files must be committed separately from code, documentation, or config changes.

**Rationale:** User request — mixing squad state with feature commits trips up reviewers who can't distinguish squad bookkeeping from real change content. History files are the exception because they are a direct artifact of the code work being reviewed.

## Copilot Directive — Squad/History separation (2026-04-07T22:15:03Z)

**By:** Ahmed Sabbour
**What:** `.squad/agents/*/history.md` changes ARE allowed alongside code changes in the same PR/commit — history files are contextually related to the code work. The rule against mixing squad state with code only applies to orchestration logs, decisions, and other `.squad/` state files (NOT history files).
**Why:** User request — captured for team memory


## Auto Narration — Architecture Decision (Fenster, 2026-04-15T17:48:11.995Z)

**Decision:** Persist automated narration on the existing voiceover path as a `VoiceoverSegment` with `source="generated"`.
- Store generated narration markdown in `script_markdown`
- Keep spoken TTS source text in `text`
- Keep last exported markdown location in `script_path`
- Replace prior generated narration while preserving manual voiceover segments

**Why:** Reuses current export mixer, `.fcproj` JSON persistence, and voiceover audio packaging without duplicating timeline or audio logic. Backward compatible (new fields optional).


## Auto Narration — UI/Timeline Decision (McManus, 2026-04-15T17:48:11.995Z)

**Decision:** Automated narration stays on the existing voiceover track as a single generated `VoiceoverSegment`. Editor asks before replacing existing generated narration, preserves manual segments, rewrites markdown sidecar beside the active video after project load.
**Why:** Keeps export, save/load, and timeline rendering on one proven path instead of duplicating the audio model. Users get first-class narration flow while implementation remains compatible with existing voiceover tooling and `.fcproj` persistence.
**Affected:** `main_window.py`, `editor_panel.py`, `timeline_widget.py`

## User Directive: AI Narration Batching (Ahmed Sabbour, 2026-04-15)

**Status:** Captured | **Type:** Feature Request

### Directive

For AI narration, consider batching multiple requests instead of hard-capping at 50 images, but do it in a way that avoids rate limiting.

**Rationale:** User request — captured for team memory to guide AI narration improvements.

*Captured: 2026-04-15T19:11:54.918Z*

## User Directive: AI Narration Multi-Modal (Ahmed Sabbour, 2026-04-15)

**Status:** Captured | **Type:** Feature Request

### Directive

AI narration should account for zooms and annotations too, not just mouse, clicks, and keystrokes.

**Rationale:** User request — captured for team memory to guide narration generation strategy.

*Captured: 2026-04-15T19:16:47.060Z*

## Fenster: Narration Image Cap Handling (2026-04-15)

**Status:** Implementation Spec | **Agent:** Fenster | **Module:** `ai_service.py`

### Design

Handle long automated narration runs in shared `ai_service.py` with sequential multimodal batching instead of a single global trim:
- Keep the full 5-second-plus-cues frame plan
- Split extracted frames into batches below the provider's 50-image cap
- Pause between requests to reduce burst rate-limiting risk
- Synthesize final narration from batch notes
- Pass both authoritative zoom keyframes and structured annotations into narration generation
- Convert annotations into explicit cue moments
- Include annotation summaries in both slice-analysis and final synthesis prompts
- Preserve manual voiceover/project persistence behavior unchanged

### Rationale

This fixes the provider 400 at the core narration path without throwing away later parts of the recording, preserves narration quality on long videos, and makes narration reflect deliberate zoom emphasis plus explicit text/arrow/highlight callouts in addition to mouse, clicks, and keystrokes.

*Captured: 2026-04-15T19:11:54.918Z*

## Voiceover TTS Generation State (Fenster, 2026-04-15T23:01:28.412Z)

**Status:** Implemented | **Type:** Backend State Signal

### Context

When the AI narration pipeline generates voiceover segments, those segments land on the timeline immediately while TTS audio synthesis runs in a sequential background batch. During that gap, a segment exists on the voice track with no audio but no first-class signal that synthesis was *actively in progress* versus *not yet requested* or *permanently unsynthesized*.

### Decision

Added a **runtime-only boolean field** `tts_generating: bool` to `VoiceoverSegment`.

**Rules:**
- Segment just created (manual or generated narration): `False`
- `_synthesize_voiceover` hands the segment ID to `AIWorker.run_tts`: **`True`**
- `_on_ai_tts_result` receives the completed audio path: `False`
- `_on_ai_error` fires for any task: `False` on all segments

**Persistence:** Never serialized (`to_dict` omits it; `from_dict` ignores it). Segments loaded from `.fcproj` always start `False`.

**Equality:** Carries `compare=False` — two segments that differ only in synthesis state are considered equal.

**Files changed:**
- `followcursor/app/models.py` — `tts_generating` field on `VoiceoverSegment`
- `followcursor/app/main_window.py` — set/clear around `_synthesize_voiceover` and in `_on_ai_tts_result` / `_on_ai_error`
- `followcursor/tests/test_models.py` — 5 focused regression tests


## Voiceover Generation UI Indicator (McManus, 2026-04-15T23:01:28.412Z)

**Status:** Implemented | **Type:** Timeline Widget Visual Feedback

### Context

Users need visual feedback while TTS audio is being synthesized for a voiceover segment. Without an indicator, the segment appears identical to a completed one, leaving uncertainty about whether the app is working.

### Decision

### Spinner animation in `_TimelineTrack`

- A `QTimer` at 80 ms (≈ 12.5 fps) advances `_spinner_phase` by 36° per tick → one full rotation every 800 ms.
- Timer only runs while at least one segment has `tts_generating=True`; idle otherwise.
- Called from `TimelineWidget.set_data()` whenever `voiceover_segments` is provided.

### Visual treatment

- Amber arc (`#fbbf24`) drawn at the right end of the pill, 120° sweep rotating continuously.
- Suppresses the static "…" ellipsis icon during generation (same slot).
- Existing colour logic (teal filled / grey pending / teal-bright selected) unchanged.

**Files changed:**
- `followcursor/app/widgets/timeline_widget.py` — spinner timer, arc rendering
- `followcursor/tests/test_timeline_widget.py` — 8 regression tests


## Narration Guidance Prompt UI (McManus, 2026-04-15T23:04:02.621Z)

**Status:** Implemented | **Type:** Editor Panel Feature

### Context

Users requested an optional per-recording guidance prompt in the Narration Voiceover panel to steer what a generated narration focuses on — without making the UI noisy.

### Decision

### UI placement

A `QPlainTextEdit` named `_narration_guidance` is placed between the voiceover description label and the **Generate narration** button, inside the existing Voiceover collapsible section. Label reads **"Guidance (optional)"** so the field is unambiguously non-required.

Placeholder text example:
> *"Steer what the narration focuses on — e.g. "lead with the time saved" or "emphasize this is a one-click flow"."*

Height fixed at 64 px.

### Signal change

`generate_narration_requested` changed from `Signal(str)` (voice only) to `Signal(str, str)` (voice, guidance). Minimal change carrying text to main_window.

### Backend wiring

`generate_narration()` in `ai_service.py` receives `guidance_prompt` parameter, forwarded as `guidance` keyword argument to `_generate_narration_segments()`, which passes it to `_build_narration_system_prompt()`. System prompt appends creator guidance block when non-empty.

**Files changed:**
- `followcursor/app/widgets/editor_panel.py` — guidance field, label, signal update
- `followcursor/app/main_window.py` — `_on_generate_narration_requested` signature, `guidance_prompt` kwarg forwarded
- `followcursor/app/ai_service.py` — `guidance_prompt` param on `generate_narration`
- `followcursor/tests/test_editor_panel.py` — 6 regression tests


## User Directive: Narration Prompt Enrichment (Ahmed Sabbour, 2026-04-15T23:04:02.621Z)

**Status:** Captured | **Type:** Feature Guidance

### Directive

Update narration prompting so it captures the feature, end-user benefit, and meta takeaway — especially how easy something is — instead of narrating literal on-screen steps.

**Rationale:** User request — captured for team memory to guide narration generation strategy.

*Captured: 2026-04-15T23:04:02.621Z*


---

## Light Mode Theme Support for Custom-Painted Widgets (McManus, 2026-04-15)

**Status:** Implemented

### Context

The timeline widget and transport controls were rendering with dark-mode colors even when the app was switched to light mode. Custom `paintEvent` methods had hard-coded dark color values instead of using design tokens.

### Solution

1. Added theme-aware color helpers to `tokens.py` (bg_canvas, bg_track, fg_primary, etc.)
2. Added `_dark_mode` state and `set_dark_mode()` methods to custom-painted widgets
3. Wired `TimelineWidget.set_dark_mode()` to propagate to all children
4. Updated `MainWindow._apply_theme()` to call `self._timeline.set_dark_mode()`

### Files Changed

- `followcursor/app/tokens.py` — theme-aware color helpers
- `followcursor/app/widgets/timeline_widget.py` — custom widget theme propagation
- `followcursor/app/main_window.py` — theme propagation wiring
- `followcursor/tests/test_timeline_widget.py` — regression tests

**Rationale:** Custom-painted widgets must follow theme propagation pattern for light/dark mode consistency.

*Captured: 2026-04-15T23:04:02.621Z*
