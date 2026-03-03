---
applyTo: "**/{zoom_engine,activity_analyzer,timeline_widget,editor_panel}.py"
---

# Zoom System & Timeline

## Keyframe Structure

- `ZoomKeyframe` has: time_s, target_x, target_y, scale, duration
- **Ease-out** easing (`1 - (1-t)^5`) for all zoom and pan transitions — quintic curve, ~80% of movement in first 40% of duration
- Default transition duration: 600ms
- Old `smooth_step` name kept as alias for backward compatibility

## Anticipation

- Zoom-in and pan transitions complete `ANTICIPATION_MS` (200ms) *before* the activity starts, so the viewer sees the trigger from the beginning

## Activity Analyzer

- Generates keyframes from typing clusters and click events (mouse settlements are **not** used as zoom triggers)
- **Signal priority**: clicks (`WEIGHT_CLICK = 1.2`) > typing (`WEIGHT_TYPING = 1.0`). Single clicks generate zoom events.
- **Typing position**: when `KeyEvent` objects carry `x`/`y`, uses those coordinates directly instead of inferring from the mouse track
- Spatial-aware clustering merges same-type peaks close in screen position
- **Max cluster duration**: clusters split at `MAX_CLUSTER_DURATION_MS` (8000ms)

## Auto-Generate Confirmation

- When the user clicks "Auto-generate zoom keyframes" and zoom sections already exist, a `QMessageBox` confirms before replacing them (Replace / Cancel)

## Pan-While-Zoomed Chains

- Consecutive clusters within `PAN_MERGE_GAP_MS` (1500ms) are grouped
- Camera zooms in at first cluster, pans smoothly to each subsequent cluster while staying zoomed, then zooms out after the last cluster
- Chains capped at `MAX_CHAIN_LENGTH = 4` clusters
- Gap is measured from actual activity end to next activity start (hold period excluded)
- Pan duration scales with distance (`PAN_TRANSITION_MS = 400` – `PAN_TRANSITION_MAX_MS = 700` ms)

## Overlap Prevention (Two-Phase)

1. **Chain-level pass**: before keyframe generation, reduces hold times or pushes zoom-in times for non-overlapping visual spans
2. **Segment-based post-processing**: ensures each segment's zoom-out completes before the next zoom-in starts

## Pan Path Points

- Intermediate `ZoomKeyframe` entries with same zoom level but different `(x, y)` — creates panning path within a zoomed segment
- Regular keyframes with `reason="Pan point"` — no engine changes needed
- Added via right-click on the preview surface while zoomed in (not from the timeline segment menu)
- Preview emits `pan_point_requested(time_ms, pan_x, pan_y)` signal; main window finds the containing segment and creates the keyframe
- Timeline draws numbered yellow circle markers with dashed connecting line
- Right-click: pick center on preview, move earlier/later (swap timestamps), delete
- Draggable horizontally within segment bounds

## Manual Keyframes

- Created via right-click on timeline (empty space or zoom segment), preview, or editor panel
- **Defaults**: hold time 1500ms, zoom-out transition 600ms
- Overlap prevention clamps zoom-out to stay before next zoom-in
- Viewport centering: zoom targets clamped so the viewport stays within source bounds

## Segment Edge Dragging

- Right-edge drags account for zoom-out keyframe's transition duration so the visual edge follows the mouse
- Minimum keyframe gap during drag: 100ms

## Undo/Redo

- `ZoomEngine` has snapshot-based undo/redo (deep-copy, MAX_UNDO=50)
- Snapshots capture both zoom keyframes and click events (click deletions are undoable)
- `push_undo()` called before every mutation
- Drag operations debounced via `_drag_undo_pushed` flag

## Dirty Tracking

- `_unsaved_changes` flag set by `_mark_dirty()` after every mutation; cleared on save

## Trimming

- Timeline has draggable trim handles at both edges (yellow bars, dimmed overlay)
- `RecordingSession` stores `trim_start_ms` and `trim_end_ms` (persisted in .fcproj)
- `VideoExporter` skips frames outside trim range
- 500ms minimum constraint on trimmed duration

## Debug Overlay

- Enabled by default — shows colored zoom markers on the preview
