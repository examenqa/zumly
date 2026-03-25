"""Pure coordinate-mapping math for the timeline view zoom/pan.

These functions are used by ``_TimelineTrack`` and can be imported
independently (no Qt dependency) for unit testing.
"""


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
