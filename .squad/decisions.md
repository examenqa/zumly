# Squad Decisions

## Active Decisions

### 1. Keystroke Visualization Rendering Architecture

**Date:** 2026-01-25  
**Agent:** Fenster  
**Status:** Implemented (PR #60)  

Key decisions:
- Extended KeyEvent model with `vk_code` field (Windows virtual key codes)
- Dual rendering pipeline: `draw_keystrokes_qpainter()` (preview) + `draw_keystrokes_cv()` (export)
- Keystroke grouping within 100ms window for combos (e.g., "Ctrl+C")
- Configuration model with position, style, duration, filter mode, opacity
- Export integration after click rendering, before composition

See `.squad/decisions/inbox/fenster-keystroke-viz.md` for full rationale.

### 2. Keystroke Filter Security Architecture

**Date:** 2025-01-06  
**Agent:** Fenster  
**Status:** Implemented (PR #63)  

Key decisions:
- Three filter modes: "all", "modifiers-only", "shortcuts-only"
- **Default changed to "shortcuts-only"** (security-by-default)
- Pre-grouping filter (earliest possible point for security)
- Modifier VK sets: CTRL, ALT, WIN (Shift excluded — typing modifier)
- Dynamic tooltip warnings when filter mode changes

Rationale: Prevents accidental password leaks in tutorials. Filter before grouping eliminates transient password strings in memory.

See `.squad/decisions/inbox/fenster-keystroke-security.md` for full rationale.

### 3. Annotations Architecture

**Date:** 2026-04-06  
**Agent:** McManus  
**Status:** Implemented (PR #61)  

Key decisions:
- Normalized coordinate system (0-1 range) for resolution independence
- Dual rendering pipeline: `render_annotations_qpainter()` + `render_annotations_cv()`
- Annotation types: TextAnnotation, ArrowAnnotation, HighlightAnnotation
- Timeline bounds: `start_ms` and `end_ms` per annotation
- Rendering order: Highlights → Arrows → Text (visual hierarchy)
- Separate `AnnotationCollection` object in project files
- Scrollable list UI in editor panel (max-height 200px)
- Default colors: White text, Yellow arrows/highlights

Future work: editing UI, templates, advanced arrow types, animation effects.

See `.squad/decisions/inbox/mcmanus-annotations.md` for full rationale.

### 4. Chapter Detection: Heuristic vs. AI Analysis

**Date:** 2025-01-06  
**Agent:** Fenster  
**Status:** Implemented (PR #64)  

Key decisions:
- **Use heuristic-based chapter detection (no AI API calls)** as primary implementation
- Heuristic detects:
  - Extended inactivity (3s+ idle gaps)
  - Major position jumps (30%+ screen distance)
  - Filters out chapters <5s apart
- FFmpeg metadata export for MP4 chapters (YouTube/VLC support)
- `Chapter.auto_detected = True` flag for heuristic chapters
- AI naming enhancement deferred (optional v2 feature)

Rationale: Heuristic is instant, free, private, deterministic. Activity patterns naturally create scene boundaries. AI can be added later for optional semantic naming.

See `.squad/decisions/inbox/fenster-chapters.md` for full rationale.

## Governance

- All meaningful changes require team consensus
- Document architectural decisions here
- Keep history focused on work, decisions focused on direction
