"""Mouse cursor renderer — draws a cursor overlay on video frames.

Provides both QPainter-based (for live preview) and numpy/OpenCV-based
(for export) cursor drawing using the recorded mouse track data.
Also renders click ripple effects at recorded click positions.
"""

import logging
import bisect
import math
from typing import List, Optional, Tuple

import cv2
import numpy as np



from .models import MousePosition, ClickEvent, ClickEffectPreset, DEFAULT_CLICK_EFFECT

logger = logging.getLogger(__name__)


# ── Cursor appearance ───────────────────────────────────────────────

CURSOR_COLOR = (255, 255, 255)       # white  (RGB for QPainter, BGR for CV)
CURSOR_OUTLINE = (30, 30, 30)        # near-black outline (softer than pure black)
CURSOR_OUTLINE_W = 2.0               # outline width
CURSOR_SHADOW_ALPHA = 80             # drop shadow opacity (0-255)

# ── Click effect appearance ─────────────────────────────────────────

CLICK_DURATION_MS = 400.0            # how long the click ripple is visible
CLICK_COLOR = (138, 92, 246)         # purple (#8b5cf6) in RGB
CLICK_COLOR_BGR = (246, 92, 138)     # purple in BGR for OpenCV
CLICK_MAX_RADIUS = 24.0              # max ripple radius (preview, scaled)


def _interp_mouse(track: List[MousePosition], time_ms: float) -> Optional[Tuple[float, float]]:
    """Interpolate mouse position at *time_ms* from recorded track.

    Returns (x, y) in absolute screen coordinates, or None if no data.
    """
    if not track:
        return None
    if time_ms <= track[0].timestamp:
        return track[0].x, track[0].y
    if time_ms >= track[-1].timestamp:
        return track[-1].x, track[-1].y

    # Binary search for the right interval
    lo, hi = 0, len(track) - 1
    while lo < hi - 1:
        mid = (lo + hi) // 2
        if track[mid].timestamp <= time_ms:
            lo = mid
        else:
            hi = mid

    a, b = track[lo], track[hi]
    dt = b.timestamp - a.timestamp
    if dt <= 0:
        return a.x, a.y
    t = (time_ms - a.timestamp) / dt
    return a.x + (b.x - a.x) * t, a.y + (b.y - a.y) * t


def _build_cursor_template(height: int) -> Tuple[np.ndarray, np.ndarray]:
    """Pre-render a cursor image + alpha mask at the given pixel height using OpenCV.

    Returns:
        cursor_bgr:   shape (H, W, 3) — BGR colour channels.
        cursor_alpha: shape (H, W)    — single-channel alpha mask (0-255).

    The cursor tip is at pixel (0, 0) in both returned arrays.
    """
    h = max(height, 8)
    scale = h / 20.0
    
    # Standard arrow polygon coordinates relative to tip at (0,0)
    pts = np.array([
        [0, 0], [0, 17], [4, 13], [7, 20], [9, 19], [6, 12], [12, 12]
    ], np.float32)
    pts = (pts * scale).astype(np.int32)
    
    render_w = int(14 * scale) + 4
    render_h = int(22 * scale) + 4
    
    cursor_bgr = np.zeros((render_h, render_w, 3), dtype=np.uint8)
    cursor_alpha = np.zeros((render_h, render_w), dtype=np.uint8)
    
    # Draw drop shadow / alpha mask
    cv2.fillPoly(cursor_alpha, [pts], 255)
    cv2.polylines(cursor_alpha, [pts], True, 255, thickness=int(2*scale)+1)
    
    # Draw white fill
    cv2.fillPoly(cursor_bgr, [pts], (255, 255, 255))
    # Draw black outline
    cv2.polylines(cursor_bgr, [pts], True, (30, 30, 30), thickness=max(1, int(1*scale)))
    
    return cursor_bgr, cursor_alpha


def draw_cursor_cv(
    frame_bgr: np.ndarray,
    track: List[MousePosition],
    time_ms: float,
    mon_left: int,
    mon_top: int,
    mon_w: int,
    mon_h: int,
    cursor_bgr: np.ndarray,
    cursor_alpha: np.ndarray,
) -> None:
    """Draw cursor onto *frame_bgr* in-place.

    *frame_bgr* is the raw video frame (same resolution as monitor).
    The cursor template is pre-built via _build_cursor_template().
    """
    pos = _interp_mouse(track, time_ms)
    if pos is None:
        return

    mx, my = pos
    fh, fw = frame_bgr.shape[:2]
    ch, cw = cursor_bgr.shape[:2]

    # Position in frame pixels
    px = int((mx - mon_left) / max(mon_w, 1) * fw)
    py = int((my - mon_top) / max(mon_h, 1) * fh)

    # Bounds check
    x1, y1 = px, py
    x2, y2 = px + cw, py + ch

    # Clip to frame
    src_x1 = max(0, -x1)
    src_y1 = max(0, -y1)
    x1 = max(0, x1)
    y1 = max(0, y1)
    x2 = min(fw, x2)
    y2 = min(fh, y2)
    src_x2 = src_x1 + (x2 - x1)
    src_y2 = src_y1 + (y2 - y1)

    if x2 <= x1 or y2 <= y1:
        return

    roi = frame_bgr[y1:y2, x1:x2]
    c_roi = cursor_bgr[src_y1:src_y2, src_x1:src_x2]
    a_roi = cursor_alpha[src_y1:src_y2, src_x1:src_x2]

    alpha = a_roi[:, :, np.newaxis].astype(np.float32) / 255.0
    blended = (c_roi.astype(np.float32) * alpha + roi.astype(np.float32) * (1 - alpha))
    np.copyto(roi, blended.astype(np.uint8))





# ── OpenCV/numpy-based click effects (for export) ──────────────────


def draw_clicks_cv(
    frame_bgr: np.ndarray,
    click_events: List[ClickEvent],
    time_ms: float,
    mon_left: int,
    mon_top: int,
    mon_w: int,
    mon_h: int,
    preset: Optional[ClickEffectPreset] = None,
) -> None:
    """Draw click effects onto *frame_bgr* in-place.

    Supported styles:
    - ``"ripple"`` (default): expanding ring + solid inner dot.
    - ``"burst"``: radiating lines from click point.
    - ``"highlight"``: filled circle that fades out.
    """
    if not click_events:
        return

    if preset is None:
        preset = DEFAULT_CLICK_EFFECT

    # Invisible preset or zero alpha — skip drawing
    if preset.color[3] == 0 or preset.duration_ms <= 0:
        return

    fh, fw = frame_bgr.shape[:2]
    max_r = max(preset.radius, int(fh * 0.025))

    # Convert RGB to BGR for OpenCV
    color_bgr = (preset.color[2], preset.color[1], preset.color[0])
    base_alpha = preset.color[3] / 255.0
    style = preset.style if preset.style in ("ripple", "burst", "highlight") else "ripple"

    # Use binary search to limit iteration to clicks in the visible window
    window_start = time_ms - preset.duration_ms
    lo = bisect.bisect_left(click_events, window_start, key=lambda c: c.timestamp)
    hi = bisect.bisect_right(click_events, time_ms, key=lambda c: c.timestamp)

    for click in click_events[lo:hi]:
        age = time_ms - click.timestamp
        if age < 0 or age > preset.duration_ms:
            continue

        t = age / preset.duration_ms

        # Position in frame pixels
        px = int((click.x - mon_left) / max(mon_w, 1) * fw)
        py = int((click.y - mon_top) / max(mon_h, 1) * fh)

        if style == "burst":
            _draw_burst_cv(frame_bgr, px, py, t, max_r, color_bgr, base_alpha)
        elif style == "highlight":
            _draw_highlight_cv(frame_bgr, px, py, t, max_r, color_bgr, base_alpha)
        else:
            _draw_ripple_cv(frame_bgr, px, py, t, max_r, color_bgr, base_alpha)


def _draw_ripple_cv(
    frame_bgr: np.ndarray, px: int, py: int, t: float,
    max_r: int, color_bgr: tuple, base_alpha: float,
) -> None:
    """Expanding ring + inner dot (default click style)."""
    radius = int(max_r * (0.3 + 0.7 * t))
    ring_alpha = base_alpha * (1.0 - t)
    if ring_alpha > 0.05:
        thickness = max(1, int(3.0 * (1.0 - t)))
        color_scaled = tuple(int(c * ring_alpha * 0.85) for c in color_bgr)
        cv2.circle(frame_bgr, (px, py), radius, color_scaled, thickness, cv2.LINE_AA)
    dot_alpha = base_alpha * max(0.0, 1.0 - t * 1.8)
    if dot_alpha > 0.05:
        dot_r = max(2, int(5.0 * (1.0 - t * 0.5)))
        color_dot = tuple(int(c * dot_alpha * 0.8) for c in color_bgr)
        cv2.circle(frame_bgr, (px, py), dot_r, color_dot, -1, cv2.LINE_AA)


def _draw_burst_cv(
    frame_bgr: np.ndarray, px: int, py: int, t: float,
    max_r: int, color_bgr: tuple, base_alpha: float,
) -> None:
    """Radiating lines from click point."""
    num_rays = 8
    ray_alpha = base_alpha * (1.0 - t)
    if ray_alpha < 0.05:
        return
    thickness = max(1, int(2.5 * (1.0 - t)))
    color_scaled = tuple(int(c * ray_alpha * 0.85) for c in color_bgr)
    inner_r = max_r * 0.2 * (1.0 + t)
    outer_r = max_r * (0.4 + 0.8 * t)
    for i in range(num_rays):
        angle = 2.0 * math.pi * i / num_rays
        x1 = int(px + math.cos(angle) * inner_r)
        y1 = int(py + math.sin(angle) * inner_r)
        x2 = int(px + math.cos(angle) * outer_r)
        y2 = int(py + math.sin(angle) * outer_r)
        cv2.line(frame_bgr, (x1, y1), (x2, y2), color_scaled, thickness, cv2.LINE_AA)


def _draw_highlight_cv(
    frame_bgr: np.ndarray, px: int, py: int, t: float,
    max_r: int, color_bgr: tuple, base_alpha: float,
) -> None:
    """Filled circle that fades out."""
    radius = int(max_r * 0.8)
    fill_alpha = base_alpha * 0.5 * (1.0 - t)
    if fill_alpha < 0.05:
        return
    color_scaled = tuple(int(c * fill_alpha) for c in color_bgr)
    cv2.circle(frame_bgr, (px, py), radius, color_scaled, -1, cv2.LINE_AA)
