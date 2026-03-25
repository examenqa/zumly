"""Pure-function trim-aware coordinate mapping for the timeline.

These helpers are used by both ``_TimelineTrack`` (the Qt widget) and
the test suite so the tests exercise the exact same code path without
requiring a PySide6 dependency.
"""


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
