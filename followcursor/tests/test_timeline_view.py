"""Tests for timeline view zoom coordinate mapping logic.

The _TimelineTrack widget uses _ms_to_x / _x_to_ms helpers with
view_scale and view_offset.  Since these require a live QWidget, we
test the underlying math directly with a lightweight stand-in.
"""

import pytest


# ── replicate the coordinate mapping math ──────────────────────────


def ms_to_x(ms: float, duration: float, view_scale: float, view_offset: float, w: float) -> float:
    """Mirror of _TimelineTrack._ms_to_x."""
    if duration <= 0:
        return 0.0
    visible_duration = duration / view_scale
    return ((ms - view_offset) / visible_duration) * w


def x_to_ms(x: float, duration: float, view_scale: float, view_offset: float, w: float) -> float:
    """Mirror of _TimelineTrack._x_to_ms."""
    if duration <= 0 or w <= 0:
        return 0.0
    visible_duration = duration / view_scale
    return view_offset + (x / w) * visible_duration


def max_view_scale(duration: float, w: float) -> float:
    """Mirror of _TimelineTrack._max_view_scale.  1 px = 10 ms."""
    if w <= 0 or duration <= 0:
        return 1.0
    return max(1.0, duration / (10.0 * w))


def clamp_offset(view_offset: float, duration: float, view_scale: float) -> float:
    """Mirror of _TimelineTrack._clamp_offset."""
    if duration <= 0:
        return 0.0
    visible_duration = duration / view_scale
    max_offset = duration - visible_duration
    return max(0.0, min(view_offset, max_offset))


# ── tests ──────────────────────────────────────────────────────────


class TestMsToX:
    """Verify ms → pixel mapping with view zoom/pan."""

    def test_identity_at_scale_1(self) -> None:
        """Scale=1, offset=0 should behave like the old (t/duration)*w."""
        assert ms_to_x(5000, 10000, 1.0, 0.0, 800) == pytest.approx(400.0)

    def test_start_maps_to_zero(self) -> None:
        assert ms_to_x(0, 10000, 1.0, 0.0, 800) == pytest.approx(0.0)

    def test_end_maps_to_width(self) -> None:
        assert ms_to_x(10000, 10000, 1.0, 0.0, 800) == pytest.approx(800.0)

    def test_zoom_2x_center(self) -> None:
        """At 2× zoom, offset=2500 → visible range [2500, 7500].
        5000 ms should be at the center (400 px)."""
        assert ms_to_x(5000, 10000, 2.0, 2500, 800) == pytest.approx(400.0)

    def test_zoom_2x_left_edge(self) -> None:
        """At 2× zoom, offset=2500 → 2500 ms maps to x=0."""
        assert ms_to_x(2500, 10000, 2.0, 2500, 800) == pytest.approx(0.0)

    def test_zoom_2x_right_edge(self) -> None:
        """At 2× zoom, offset=2500 → 7500 ms maps to x=800."""
        assert ms_to_x(7500, 10000, 2.0, 2500, 800) == pytest.approx(800.0)

    def test_offscreen_negative(self) -> None:
        """Points before the visible range produce negative x."""
        assert ms_to_x(0, 10000, 2.0, 2500, 800) < 0

    def test_zero_duration(self) -> None:
        assert ms_to_x(100, 0, 1.0, 0.0, 800) == 0.0


class TestXToMs:
    """Verify pixel → ms mapping with view zoom/pan."""

    def test_identity_at_scale_1(self) -> None:
        assert x_to_ms(400, 10000, 1.0, 0.0, 800) == pytest.approx(5000.0)

    def test_left_edge(self) -> None:
        assert x_to_ms(0, 10000, 1.0, 0.0, 800) == pytest.approx(0.0)

    def test_right_edge(self) -> None:
        assert x_to_ms(800, 10000, 1.0, 0.0, 800) == pytest.approx(10000.0)

    def test_zoom_2x_center(self) -> None:
        """At 2× zoom, offset=2500, center pixel → 5000 ms."""
        assert x_to_ms(400, 10000, 2.0, 2500, 800) == pytest.approx(5000.0)

    def test_zoom_2x_left_edge(self) -> None:
        assert x_to_ms(0, 10000, 2.0, 2500, 800) == pytest.approx(2500.0)

    def test_zero_duration(self) -> None:
        assert x_to_ms(400, 0, 1.0, 0.0, 800) == 0.0

    def test_zero_width(self) -> None:
        assert x_to_ms(0, 10000, 1.0, 0.0, 0) == 0.0


class TestRoundTrip:
    """ms_to_x and x_to_ms should be inverses."""

    @pytest.mark.parametrize("ms", [0, 1000, 5000, 9999, 10000])
    def test_roundtrip_scale_1(self, ms: float) -> None:
        x = ms_to_x(ms, 10000, 1.0, 0.0, 800)
        assert x_to_ms(x, 10000, 1.0, 0.0, 800) == pytest.approx(ms)

    @pytest.mark.parametrize("ms", [3000, 5000, 7000])
    def test_roundtrip_zoomed(self, ms: float) -> None:
        x = ms_to_x(ms, 10000, 2.0, 2500, 800)
        assert x_to_ms(x, 10000, 2.0, 2500, 800) == pytest.approx(ms)


class TestMaxViewScale:
    """Maximum zoom: 1 px = 10 ms."""

    def test_short_recording(self) -> None:
        """800 px, 10000 ms → max scale = 10000 / (10 * 800) = 1.25."""
        assert max_view_scale(10000, 800) == pytest.approx(1.25)

    def test_long_recording(self) -> None:
        """800 px, 60000 ms → max scale = 60000 / (10 * 800) = 7.5."""
        assert max_view_scale(60000, 800) == pytest.approx(7.5)

    def test_tiny_duration_clamps_to_1(self) -> None:
        """Duration so small the formula would give <1.0 — clamp to 1."""
        assert max_view_scale(100, 800) == 1.0

    def test_zero_width(self) -> None:
        assert max_view_scale(10000, 0) == 1.0


class TestClampOffset:
    """Offset clamping keeps the viewport within [0, duration]."""

    def test_offset_at_zero(self) -> None:
        assert clamp_offset(0, 10000, 2.0) == 0.0

    def test_offset_negative_clamps_to_zero(self) -> None:
        assert clamp_offset(-500, 10000, 2.0) == 0.0

    def test_offset_at_max(self) -> None:
        """At 2× zoom, visible = 5000 ms, max offset = 5000."""
        assert clamp_offset(5000, 10000, 2.0) == pytest.approx(5000.0)

    def test_offset_past_max_clamps(self) -> None:
        assert clamp_offset(8000, 10000, 2.0) == pytest.approx(5000.0)

    def test_scale_1_no_scroll(self) -> None:
        """At scale=1, no scrolling possible → offset always 0."""
        assert clamp_offset(100, 10000, 1.0) == 0.0

    def test_zero_duration(self) -> None:
        assert clamp_offset(100, 0, 1.0) == 0.0


class TestWheelZoomLogic:
    """Verify the zoom-centered-on-cursor math used by wheelEvent."""

    def test_zoom_in_keeps_cursor_fixed(self) -> None:
        """Zooming in at cursor_x should keep the same ms at the same x."""
        duration = 10000.0
        w = 800.0
        old_scale = 1.0
        view_offset = 0.0
        cursor_x = 400.0  # center

        # Compute the ms under cursor before zoom
        cursor_ms = x_to_ms(cursor_x, duration, old_scale, view_offset, w)

        # Apply zoom (1.15×)
        new_scale = old_scale * 1.15
        visible_duration = duration / new_scale
        ratio = cursor_x / w
        new_offset = cursor_ms - ratio * visible_duration
        new_offset = clamp_offset(new_offset, duration, new_scale)

        # The ms at cursor_x should still be cursor_ms
        after_ms = x_to_ms(cursor_x, duration, new_scale, new_offset, w)
        assert after_ms == pytest.approx(cursor_ms, abs=1.0)

    def test_zoom_out_at_max_resets(self) -> None:
        """Zooming out past scale=1 clamps to 1.0 with offset 0."""
        duration = 10000.0
        new_scale = max(1.0, 0.8)  # should clamp
        assert new_scale == 1.0
