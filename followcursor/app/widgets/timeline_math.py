"""Pure coordinate-mapping math for the timeline.

These helpers are used by both ``_TimelineTrack`` (the Qt widget) and
the test suite so the tests exercise the exact same code path without
requiring a PySide6 dependency.

Two groups of functions live here:

1. **Trim helpers** — map between absolute ms and pixel x within the
   trimmed viewport (``trim_eff_start``, ``trim_ms_to_x``, …).
2. **View-zoom helpers** — map between ms and pixel x accounting for
   the scroll-wheel zoom/pan state (``view_ms_to_x``, …).
"""


# ── View zoom/pan helpers ──────────────────────────────────────────


def view_ms_to_x(ms: float, duration: float, view_scale: float, view_offset: float, w: float) -> float:
    """Map a time in milliseconds to a pixel x-coordinate.

    Args:
        ms: Time in milliseconds.
        duration: Total recording duration in ms.
        view_scale: Zoom factor (1.0 = fit-all).
        view_offset: Left-edge offset of the viewport in ms.
        w: Widget width in pixels.
    """
    if duration <= 0:
        return 0.0
    visible_duration = duration / view_scale
    return ((ms - view_offset) / visible_duration) * w


def view_x_to_ms(x: float, duration: float, view_scale: float, view_offset: float, w: float) -> float:
    """Map a pixel x-coordinate to a time in milliseconds.

    Args:
        x: Pixel x-coordinate.
        duration: Total recording duration in ms.
        view_scale: Zoom factor (1.0 = fit-all).
        view_offset: Left-edge offset of the viewport in ms.
        w: Widget width in pixels.
    """
    if duration <= 0 or w <= 0:
        return 0.0
    visible_duration = duration / view_scale
    return view_offset + (x / w) * visible_duration


def view_max_scale(duration: float, w: float) -> float:
    """Maximum view scale so that 1 pixel = 10 ms."""
    if w <= 0 or duration <= 0:
        return 1.0
    return max(1.0, duration / (10.0 * w))


def view_clamp_offset(view_offset: float, duration: float, view_scale: float) -> float:
    """Clamp *view_offset* so the viewport stays within [0, duration].

    Returns the clamped offset value.
    """
    if duration <= 0:
        return 0.0
    visible_duration = duration / view_scale
    max_offset = duration - visible_duration
    return max(0.0, min(view_offset, max_offset))


# ── Trim helpers ───────────────────────────────────────────────────


def trim_eff_start(trim_start_ms: float) -> float:
    """Effective start of the visible timeline range (ms)."""
    return trim_start_ms


def trim_eff_end(trim_end_ms: float, duration: float) -> float:
    """Effective end of the visible timeline range (ms).

    When *trim_end_ms* is 0 (no trim set), falls back to *duration*.
    """
    return trim_end_ms if trim_end_ms > 0 else duration


def trim_eff_dur(trim_start_ms: float, trim_end_ms: float, duration: float) -> float:
    """Effective visible duration (ms)."""
    return trim_eff_end(trim_end_ms, duration) - trim_eff_start(trim_start_ms)


def trim_ms_to_x(
    time_ms: float,
    w: int,
    trim_start_ms: float,
    trim_end_ms: float,
    duration: float,
) -> float:
    """Convert absolute time (ms) to x-pixel within the trimmed viewport."""
    ed = trim_eff_dur(trim_start_ms, trim_end_ms, duration)
    if ed <= 0:
        return 0.0
    return ((time_ms - trim_eff_start(trim_start_ms)) / ed) * w


def trim_x_to_ms(
    x: float,
    w: int,
    trim_start_ms: float,
    trim_end_ms: float,
    duration: float,
) -> float:
    """Convert x-pixel position to absolute time (ms) in the trimmed viewport."""
    if w <= 0:
        return trim_eff_start(trim_start_ms)
    return (x / w) * trim_eff_dur(trim_start_ms, trim_end_ms, duration) + trim_eff_start(trim_start_ms)
