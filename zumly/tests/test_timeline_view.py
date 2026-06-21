"""Tests for timeline view zoom coordinate mapping logic.

The _TimelineTrack widget uses _ms_to_x / _x_to_ms helpers that delegate
to the pure functions in ``app.widgets.timeline_math``.  The tests import
those functions directly so they assert against the real implementation.
"""

import pytest

from app.widgets.timeline_math import (
    view_ms_to_x as ms_to_x,
    view_x_to_ms as x_to_ms,
    view_max_scale as max_view_scale,
    view_clamp_offset as clamp_offset,
)


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

    def test_1px_equals_10ms(self) -> None:
        """At max zoom each pixel represents exactly 10 ms.
        1000 px, 100000 ms → scale = 100000 / (10 * 1000) = 10.0.
        Visible duration = 100000 / 10.0 = 10000 ms over 1000 px → 10 ms/px."""
        scale = max_view_scale(100000, 1000)
        assert scale == pytest.approx(10.0)
        visible_ms = 100000 / scale
        ms_per_px = visible_ms / 1000
        assert ms_per_px == pytest.approx(10.0)

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
        w = 800.0
        old_scale = 2.0
        view_offset = 3000.0
        cursor_x = 400.0  # center

        # Compute the ms under cursor before zoom
        cursor_ms = x_to_ms(cursor_x, duration, old_scale, view_offset, w)

        # Apply zoom out (0.5×) which would drop below scale=1, then clamp
        zoom_factor = 0.5
        new_scale = max(1.0, old_scale * zoom_factor)
        assert new_scale == 1.0

        # Recompute offset based on the clamped scale and clamp it
        visible_duration = duration / new_scale
        ratio = cursor_x / w
        new_offset = cursor_ms - ratio * visible_duration
        new_offset = clamp_offset(new_offset, duration, new_scale)

        # When scale returns to 1.0, no scrolling is possible → offset should be 0
        assert new_offset == pytest.approx(0.0)
