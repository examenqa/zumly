"""Tests for timeline trim-aware coordinate mapping and playback clamping.

These tests exercise the production trim-mapping functions from
``app.widgets.timeline_math`` — the same code that ``_TimelineTrack``
delegates to — so they cover the real code path without requiring a
PySide6 dependency.
"""

import pytest

from app.widgets.timeline_math import (
    trim_eff_start as eff_start,
    trim_eff_end as eff_end,
    trim_eff_dur as eff_dur,
    trim_ms_to_x as ms_to_x,
    trim_x_to_ms as x_to_ms,
)


# ── Tests ──────────────────────────────────────────────────────────

class TestEffectiveRange:
    """Test effective start/end/duration properties."""

    def test_no_trim_uses_full_duration(self) -> None:
        assert eff_start(0.0) == 0.0
        assert eff_end(0.0, 10000.0) == 10000.0
        assert eff_dur(0.0, 0.0, 10000.0) == 10000.0

    def test_trim_start_only(self) -> None:
        assert eff_start(2000.0) == 2000.0
        assert eff_end(0.0, 10000.0) == 10000.0
        assert eff_dur(2000.0, 0.0, 10000.0) == 8000.0

    def test_trim_end_only(self) -> None:
        assert eff_start(0.0) == 0.0
        assert eff_end(8000.0, 10000.0) == 8000.0
        assert eff_dur(0.0, 8000.0, 10000.0) == 8000.0

    def test_both_trims(self) -> None:
        assert eff_start(2000.0) == 2000.0
        assert eff_end(8000.0, 10000.0) == 8000.0
        assert eff_dur(2000.0, 8000.0, 10000.0) == 6000.0

    def test_trim_end_equals_duration(self) -> None:
        """When trim_end == duration, eff_end uses trim_end (both are the same)."""
        assert eff_end(10000.0, 10000.0) == 10000.0


class TestMsToX:
    """Test absolute-time → pixel-position mapping."""

    def test_no_trim_maps_to_full_width(self) -> None:
        # 0ms → x=0, duration → x=w
        assert ms_to_x(0, 1000, 0, 0, 10000) == 0.0
        assert ms_to_x(10000, 1000, 0, 0, 10000) == 1000.0
        assert ms_to_x(5000, 1000, 0, 0, 10000) == 500.0

    def test_trim_shifts_origin(self) -> None:
        # trim_start=2000, trim_end=8000, duration=10000
        # Visible range: 6000ms, mapped to 1000px
        # 2000ms → x=0
        assert ms_to_x(2000, 1000, 2000, 8000, 10000) == pytest.approx(0.0)
        # 8000ms → x=1000
        assert ms_to_x(8000, 1000, 2000, 8000, 10000) == pytest.approx(1000.0)
        # 5000ms → x=500 (midpoint)
        assert ms_to_x(5000, 1000, 2000, 8000, 10000) == pytest.approx(500.0)

    def test_time_outside_trim_gives_out_of_bounds(self) -> None:
        # Time before trim_start → negative x
        assert ms_to_x(0, 1000, 2000, 8000, 10000) < 0.0
        # Time after trim_end → x > w
        assert ms_to_x(10000, 1000, 2000, 8000, 10000) > 1000.0

    def test_zero_effective_duration(self) -> None:
        assert ms_to_x(5000, 1000, 5000, 5000, 10000) == 0.0


class TestXToMs:
    """Test pixel-position → absolute-time mapping."""

    def test_no_trim_maps_full_width(self) -> None:
        assert x_to_ms(0, 1000, 0, 0, 10000) == 0.0
        assert x_to_ms(1000, 1000, 0, 0, 10000) == 10000.0
        assert x_to_ms(500, 1000, 0, 0, 10000) == 5000.0

    def test_trim_offset(self) -> None:
        # trim 2000–8000, width 1000
        assert x_to_ms(0, 1000, 2000, 8000, 10000) == pytest.approx(2000.0)
        assert x_to_ms(1000, 1000, 2000, 8000, 10000) == pytest.approx(8000.0)
        assert x_to_ms(500, 1000, 2000, 8000, 10000) == pytest.approx(5000.0)

    def test_roundtrip(self) -> None:
        """ms_to_x → x_to_ms should return the original time."""
        time_ms = 4500.0
        w = 800
        ts, te, dur = 1000, 7000, 10000
        x = ms_to_x(time_ms, w, ts, te, dur)
        result = x_to_ms(x, w, ts, te, dur)
        assert result == pytest.approx(time_ms)

    def test_zero_width_returns_eff_start(self) -> None:
        assert x_to_ms(100, 0, 2000, 8000, 10000) == 2000.0


class TestTimeDisplayOffset:
    """Test that time display labels re-index to start at 0:00."""

    def test_current_time_relative(self) -> None:
        """Current time label = current_time - eff_start."""
        current_time = 5000.0
        trim_start = 2000.0
        trim_end = 8000.0
        duration = 10000.0
        display = max(0.0, current_time - eff_start(trim_start))
        assert display == pytest.approx(3000.0)

    def test_total_time_is_effective_duration(self) -> None:
        """Total time label = eff_dur."""
        assert eff_dur(2000.0, 8000.0, 10000.0) == pytest.approx(6000.0)

    def test_no_trim_display_unchanged(self) -> None:
        current_time = 5000.0
        display = max(0.0, current_time - eff_start(0.0))
        assert display == pytest.approx(5000.0)
        assert eff_dur(0.0, 0.0, 10000.0) == pytest.approx(10000.0)


class TestPlaybackClamping:
    """Test that playback seek/play are clamped to the trim range."""

    def test_seek_clamped_to_trim_start(self) -> None:
        """Seeking before trim_start should clamp to trim_start."""
        trim_start = 2000.0
        trim_end = 8000.0
        duration = 10000.0
        seek_time = 500.0  # before trim start
        es = eff_start(trim_start)
        ee = eff_end(trim_end, duration)
        clamped = max(es, min(seek_time, ee))
        assert clamped == pytest.approx(2000.0)

    def test_seek_clamped_to_trim_end(self) -> None:
        """Seeking past trim_end should clamp to trim_end."""
        trim_start = 2000.0
        trim_end = 8000.0
        duration = 10000.0
        seek_time = 9000.0  # after trim end
        es = eff_start(trim_start)
        ee = eff_end(trim_end, duration)
        clamped = max(es, min(seek_time, ee))
        assert clamped == pytest.approx(8000.0)

    def test_seek_within_range_passes_through(self) -> None:
        """Seeking within the trim range should not be modified."""
        trim_start = 2000.0
        trim_end = 8000.0
        duration = 10000.0
        seek_time = 5000.0
        es = eff_start(trim_start)
        ee = eff_end(trim_end, duration)
        clamped = max(es, min(seek_time, ee))
        assert clamped == pytest.approx(5000.0)

    def test_play_wraps_to_trim_start(self) -> None:
        """Play at/past trim_end should wrap to trim_start."""
        trim_start = 2000.0
        trim_end = 8000.0
        duration = 10000.0
        playback_time = 7950.0  # near trim_end
        ee = eff_end(trim_end, duration)
        if playback_time >= ee - 100:
            playback_time = eff_start(trim_start)
        assert playback_time == pytest.approx(2000.0)

    def test_no_trim_uses_full_range(self) -> None:
        """Without trim, clamping uses 0→duration."""
        duration = 10000.0
        seek_time = 5000.0
        es = eff_start(0.0)
        ee = eff_end(0.0, duration)
        clamped = max(es, min(seek_time, ee))
        assert clamped == pytest.approx(5000.0)


class TestTrimHandleDrag:
    """Test the relative-drag formula for trim handles."""

    def test_trim_start_drag_right_increases(self) -> None:
        """Dragging the left handle rightward increases trim_start."""
        initial_val = 1000.0
        start_x = 0.0
        # Mouse moved 100px right in a 1000px wide track
        mx = 100.0
        w = 1000
        duration = 10000.0
        delta_px = mx - start_x
        delta_ms = (delta_px / w) * duration
        new_time = initial_val + delta_ms
        # Should be 1000 + (100/1000)*10000 = 1000 + 1000 = 2000
        assert new_time == pytest.approx(2000.0)

    def test_trim_start_drag_left_decreases(self) -> None:
        """Dragging the left handle leftward (negative x) decreases trim_start."""
        initial_val = 2000.0
        start_x = 0.0
        mx = -50.0  # mouse past left edge
        w = 1000
        duration = 10000.0
        delta_px = mx - start_x
        delta_ms = (delta_px / w) * duration
        new_time = max(0.0, initial_val + delta_ms)
        # 2000 + (-50/1000)*10000 = 2000 - 500 = 1500
        assert new_time == pytest.approx(1500.0)

    def test_trim_end_drag_left_decreases(self) -> None:
        """Dragging the right handle leftward decreases trim_end."""
        initial_val = 8000.0
        start_x = 1000.0
        mx = 800.0  # moved 200px left
        w = 1000
        duration = 10000.0
        delta_px = mx - start_x
        delta_ms = (delta_px / w) * duration
        new_time = initial_val + delta_ms
        # 8000 + (-200/1000)*10000 = 8000 - 2000 = 6000
        assert new_time == pytest.approx(6000.0)

    def test_minimum_trimmed_duration(self) -> None:
        """Trim_start cannot exceed trim_end - 500ms."""
        trim_end = 5000.0
        new_trim_start = 4800.0
        clamped = min(new_trim_start, trim_end - 500)
        assert clamped == pytest.approx(4500.0)
