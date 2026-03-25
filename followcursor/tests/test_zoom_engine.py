"""Tests for app.zoom_engine — interpolation, undo/redo."""

import pytest

from app.zoom_engine import ease_out, smooth_step, speed_at_time, ZoomEngine, MAX_UNDO
from app.models import ClickEvent, ZoomKeyframe


# ── ease_out ────────────────────────────────────────────────────────


class TestEaseOut:
    def test_boundaries(self) -> None:
        assert ease_out(0.0) == pytest.approx(0.0)
        assert ease_out(1.0) == pytest.approx(1.0)

    def test_midpoint_above_linear(self) -> None:
        """Ease-out should be above linear at t=0.5 (fast start)."""
        assert ease_out(0.5) > 0.5

    def test_monotonic(self) -> None:
        """ease_out must be strictly increasing on [0, 1]."""
        prev = ease_out(0.0)
        for i in range(1, 101):
            t = i / 100.0
            curr = ease_out(t)
            assert curr > prev, f"Not monotonic at t={t}"
            prev = curr

    def test_quintic_value(self) -> None:
        """Verify the formula: 1 - (1-t)^5."""
        t = 0.3
        expected = 1.0 - (0.7 ** 5)
        assert ease_out(t) == pytest.approx(expected)

    def test_smooth_step_alias(self) -> None:
        assert smooth_step is ease_out


# ── ZoomEngine — basic operations ───────────────────────────────────


class TestZoomEngineBasics:
    def test_initial_state(self) -> None:
        engine = ZoomEngine()
        assert engine.keyframes == []
        assert engine.current_zoom == 1.0
        assert engine.current_pan_x == 0.5
        assert engine.current_pan_y == 0.5

    def test_compute_at_empty(self) -> None:
        engine = ZoomEngine()
        z, px, py = engine.compute_at(500)
        assert z == 1.0
        assert px == 0.5
        assert py == 0.5

    def test_add_keyframe_sorts(self) -> None:
        engine = ZoomEngine()
        kf_late = ZoomKeyframe.create(timestamp=2000, zoom=1.0)
        kf_early = ZoomKeyframe.create(timestamp=500, zoom=1.5)
        engine.add_keyframe(kf_late)
        engine.add_keyframe(kf_early)
        assert engine.keyframes[0].timestamp == 500
        assert engine.keyframes[1].timestamp == 2000

    def test_remove_keyframe(self) -> None:
        engine = ZoomEngine()
        kf = ZoomKeyframe.create(timestamp=100, zoom=1.5)
        engine.add_keyframe(kf)
        engine.remove_keyframe(kf.id)
        assert len(engine.keyframes) == 0

    def test_remove_nonexistent(self) -> None:
        engine = ZoomEngine()
        kf = ZoomKeyframe.create(timestamp=100, zoom=1.5)
        engine.add_keyframe(kf)
        engine.remove_keyframe("no-such-id")
        assert len(engine.keyframes) == 1

    def test_clear(self) -> None:
        engine = ZoomEngine()
        engine.add_keyframe(ZoomKeyframe.create(timestamp=100, zoom=1.5))
        engine.current_zoom = 2.0
        engine.clear()
        assert engine.keyframes == []
        assert engine.current_zoom == 1.0
        assert engine.current_pan_x == 0.5


# ── ZoomEngine — compute_at interpolation ───────────────────────────


class TestZoomEngineInterpolation:
    def test_before_first_keyframe(self) -> None:
        """Before any keyframe, state should be default."""
        engine = ZoomEngine()
        engine.add_keyframe(ZoomKeyframe.create(timestamp=1000, zoom=1.5))
        z, px, py = engine.compute_at(500)
        assert z == 1.0
        assert px == 0.5
        assert py == 0.5

    def test_at_keyframe_start(self) -> None:
        """At the exact keyframe timestamp, transition is 0% complete."""
        engine = ZoomEngine()
        engine.add_keyframe(ZoomKeyframe.create(timestamp=1000, zoom=2.0, duration=600))
        z, _, _ = engine.compute_at(1000)
        # progress = 0 → eased = 0 → zoom still at previous (1.0)
        assert z == pytest.approx(1.0)

    def test_after_transition_complete(self) -> None:
        """After transition duration, state should be at target."""
        engine = ZoomEngine()
        engine.add_keyframe(
            ZoomKeyframe.create(timestamp=1000, zoom=2.0, x=0.3, y=0.7, duration=600)
        )
        z, px, py = engine.compute_at(1600)  # 1000 + 600
        assert z == pytest.approx(2.0)
        assert px == pytest.approx(0.3)
        assert py == pytest.approx(0.7)

    def test_well_after_transition(self) -> None:
        """Way past the transition, values remain at target."""
        engine = ZoomEngine()
        engine.add_keyframe(
            ZoomKeyframe.create(timestamp=1000, zoom=1.5, duration=600)
        )
        z, _, _ = engine.compute_at(5000)
        assert z == pytest.approx(1.5)

    def test_mid_transition(self) -> None:
        """During transition, zoom should be between prev and target."""
        engine = ZoomEngine()
        engine.add_keyframe(
            ZoomKeyframe.create(timestamp=1000, zoom=2.0, duration=600)
        )
        z, _, _ = engine.compute_at(1300)  # 50% through
        assert 1.0 < z < 2.0

    def test_zoom_in_then_out(self, zoom_in_out_pair: list[ZoomKeyframe]) -> None:
        """After zoom-out transition completes, zoom should be back to 1.0."""
        engine = ZoomEngine()
        for kf in zoom_in_out_pair:
            engine.add_keyframe(kf)

        # During zoom-in hold (after transition)
        z, _, _ = engine.compute_at(1800)
        assert z == pytest.approx(1.5)

        # After zoom-out completion (4000 + 1200 = 5200)
        z, px, py = engine.compute_at(5200)
        assert z == pytest.approx(1.0)
        assert px == pytest.approx(0.5)
        assert py == pytest.approx(0.5)

    def test_update_caches_result(self) -> None:
        engine = ZoomEngine()
        engine.add_keyframe(
            ZoomKeyframe.create(timestamp=0, zoom=2.0, duration=0)
        )
        engine.update(100)
        assert engine.current_zoom == pytest.approx(2.0)

    def test_zero_duration_snaps(self) -> None:
        """Duration=0 should snap immediately to the target."""
        engine = ZoomEngine()
        engine.add_keyframe(
            ZoomKeyframe.create(timestamp=100, zoom=3.0, duration=0)
        )
        z, _, _ = engine.compute_at(100)
        assert z == pytest.approx(3.0)

    def test_pan_interpolation(self) -> None:
        """Pan coordinates should interpolate between keyframes."""
        engine = ZoomEngine()
        engine.add_keyframe(
            ZoomKeyframe.create(timestamp=0, zoom=1.5, x=0.2, y=0.3, duration=0)
        )
        engine.add_keyframe(
            ZoomKeyframe.create(timestamp=1000, zoom=1.5, x=0.8, y=0.9, duration=1000)
        )
        # At midpoint of second transition
        _, px, py = engine.compute_at(1500)
        assert 0.2 < px < 0.8
        assert 0.3 < py < 0.9


# ── ZoomEngine — undo / redo ────────────────────────────────────────


class TestZoomEngineUndoRedo:
    def test_undo_restores_state(self) -> None:
        engine = ZoomEngine()
        kf = ZoomKeyframe.create(timestamp=100, zoom=1.5)
        engine.push_undo()
        engine.add_keyframe(kf)
        assert len(engine.keyframes) == 1
        assert engine.undo()
        assert len(engine.keyframes) == 0

    def test_redo_restores_undone(self) -> None:
        engine = ZoomEngine()
        engine.push_undo()
        engine.add_keyframe(ZoomKeyframe.create(timestamp=100, zoom=1.5))
        engine.undo()
        assert len(engine.keyframes) == 0
        assert engine.redo()
        assert len(engine.keyframes) == 1

    def test_undo_empty_returns_false(self) -> None:
        engine = ZoomEngine()
        assert not engine.undo()

    def test_redo_empty_returns_false(self) -> None:
        engine = ZoomEngine()
        assert not engine.redo()

    def test_can_undo_redo_properties(self) -> None:
        engine = ZoomEngine()
        assert not engine.can_undo
        assert not engine.can_redo
        engine.push_undo()
        engine.add_keyframe(ZoomKeyframe.create(timestamp=0, zoom=1.5))
        assert engine.can_undo
        engine.undo()
        assert engine.can_redo

    def test_push_undo_clears_redo(self) -> None:
        engine = ZoomEngine()
        engine.push_undo()
        engine.add_keyframe(ZoomKeyframe.create(timestamp=0, zoom=1.5))
        engine.undo()
        assert engine.can_redo
        engine.push_undo()  # new edit branch → redo cleared
        assert not engine.can_redo

    def test_max_undo_depth(self) -> None:
        engine = ZoomEngine()
        for i in range(MAX_UNDO + 10):
            engine.push_undo()
            engine.add_keyframe(ZoomKeyframe.create(timestamp=i * 100, zoom=1.5))
        assert len(engine._undo_stack) == MAX_UNDO

    def test_clear_history(self) -> None:
        engine = ZoomEngine()
        engine.push_undo()
        engine.add_keyframe(ZoomKeyframe.create(timestamp=0, zoom=1.5))
        engine.clear_history()
        assert not engine.can_undo
        assert not engine.can_redo

    def test_undo_deep_copies(self) -> None:
        """Undo snapshots must be independent copies — mutating the engine
        after push_undo shouldn't change the snapshot."""
        engine = ZoomEngine()
        kf = ZoomKeyframe.create(timestamp=100, zoom=1.5)
        engine.add_keyframe(kf)
        engine.push_undo()
        engine.keyframes[0] = ZoomKeyframe.create(timestamp=999, zoom=3.0)
        engine.undo()
        assert engine.keyframes[0].timestamp == 100

    def test_undo_restores_click_events(self) -> None:
        """Undoing after a click deletion restores the click event."""
        engine = ZoomEngine()
        engine.click_events = [
            ClickEvent(x=100, y=200, timestamp=500),
            ClickEvent(x=300, y=400, timestamp=1500),
        ]
        engine.push_undo()
        engine.click_events.pop(0)
        assert len(engine.click_events) == 1
        assert engine.undo()
        assert len(engine.click_events) == 2
        assert engine.click_events[0].x == 100

    def test_redo_restores_click_events(self) -> None:
        """Redo re-applies the click deletion."""
        engine = ZoomEngine()
        engine.click_events = [
            ClickEvent(x=100, y=200, timestamp=500),
        ]
        engine.push_undo()
        engine.click_events.pop(0)
        engine.undo()
        assert len(engine.click_events) == 1
        assert engine.redo()
        assert len(engine.click_events) == 0

    def test_undo_restores_both_keyframes_and_clicks(self) -> None:
        """A single undo restores both keyframes and click events."""
        engine = ZoomEngine()
        engine.click_events = [ClickEvent(x=10, y=20, timestamp=100)]
        kf = ZoomKeyframe.create(timestamp=100, zoom=1.5)
        engine.add_keyframe(kf)
        engine.push_undo()
        # Mutate both
        engine.click_events.pop(0)
        engine.keyframes.clear()
        assert len(engine.click_events) == 0
        assert len(engine.keyframes) == 0
        assert engine.undo()
        assert len(engine.click_events) == 1
        assert len(engine.keyframes) == 1

    def test_click_events_deep_copied(self) -> None:
        """Click events in snapshot must be independent copies."""
        engine = ZoomEngine()
        engine.click_events = [ClickEvent(x=10, y=20, timestamp=100)]
        engine.push_undo()
        engine.click_events[0] = ClickEvent(x=999, y=999, timestamp=999)
        engine.undo()
        assert engine.click_events[0].x == 10


# ── Pan point interpolation ────────────────────────────────────────


class TestPanPointInterpolation:
    """Verify that intermediate pan-point keyframes interpolate correctly."""

    def test_pan_point_between_zoom_in_out(self) -> None:
        """A pan point at t=2000 should redirect the camera mid-zoom."""
        engine = ZoomEngine()
        # Zoom-in at t=1000
        engine.add_keyframe(
            ZoomKeyframe.create(timestamp=1000, zoom=1.5, x=0.3, y=0.3, duration=400)
        )
        # Pan point at t=2000
        engine.add_keyframe(
            ZoomKeyframe.create(timestamp=2000, zoom=1.5, x=0.7, y=0.7, duration=400,
                                reason="Pan point")
        )
        # Zoom-out at t=3500
        engine.add_keyframe(
            ZoomKeyframe.create(timestamp=3500, zoom=1.0, x=0.5, y=0.5, duration=600)
        )

        # After zoom-in transition completes (1000+400=1400), should be at (0.3, 0.3)
        z, px, py = engine.compute_at(1500)
        assert z == pytest.approx(1.5)
        assert px == pytest.approx(0.3, abs=0.05)
        assert py == pytest.approx(0.3, abs=0.05)

        # After pan point transition completes (2000+400=2400), should be at (0.7, 0.7)
        z, px, py = engine.compute_at(2500)
        assert z == pytest.approx(1.5)
        assert px == pytest.approx(0.7, abs=0.05)
        assert py == pytest.approx(0.7, abs=0.05)

    def test_multiple_pan_points(self) -> None:
        """Multiple pan points create a path through the zoomed view."""
        engine = ZoomEngine()
        engine.add_keyframe(
            ZoomKeyframe.create(timestamp=1000, zoom=2.0, x=0.2, y=0.2, duration=300)
        )
        engine.add_keyframe(
            ZoomKeyframe.create(timestamp=2000, zoom=2.0, x=0.5, y=0.5, duration=300,
                                reason="Pan point")
        )
        engine.add_keyframe(
            ZoomKeyframe.create(timestamp=3000, zoom=2.0, x=0.8, y=0.8, duration=300,
                                reason="Pan point")
        )
        engine.add_keyframe(
            ZoomKeyframe.create(timestamp=4000, zoom=1.0, x=0.5, y=0.5, duration=600)
        )

        # After first pan completes
        _, px1, py1 = engine.compute_at(2400)
        assert px1 == pytest.approx(0.5, abs=0.05)

        # After second pan completes
        _, px2, py2 = engine.compute_at(3400)
        assert px2 == pytest.approx(0.8, abs=0.05)

    def test_pan_point_zoom_remains_constant(self) -> None:
        """Pan points should maintain the same zoom level as the segment."""
        engine = ZoomEngine()
        engine.add_keyframe(
            ZoomKeyframe.create(timestamp=1000, zoom=1.8, x=0.3, y=0.3, duration=400)
        )
        engine.add_keyframe(
            ZoomKeyframe.create(timestamp=2000, zoom=1.8, x=0.7, y=0.7, duration=400,
                                reason="Pan point")
        )
        engine.add_keyframe(
            ZoomKeyframe.create(timestamp=3500, zoom=1.0, x=0.5, y=0.5, duration=600)
        )

        # Zoom should stay at 1.8 between zoom-in completion and zoom-out start
        z1, _, _ = engine.compute_at(1500)
        z2, _, _ = engine.compute_at(2500)
        z3, _, _ = engine.compute_at(3000)
        assert z1 == pytest.approx(1.8)
        assert z2 == pytest.approx(1.8)
        assert z3 == pytest.approx(1.8)


# ── Segment Speed ───────────────────────────────────────────────────


class TestSegmentSpeed:
    """Tests for per-segment playback speed helpers."""

    def test_standalone_speed_at_time(self) -> None:
        kfs = [
            ZoomKeyframe.create(timestamp=1000, zoom=2.0, duration=400, speed=3.0),
            ZoomKeyframe.create(timestamp=3000, zoom=1.0, duration=600),
        ]
        assert speed_at_time(kfs, 2000, 10000) == 3.0
        assert speed_at_time(kfs, 500, 10000) == 1.0
        assert speed_at_time([], 500, 10000) == 1.0

    def test_speed_default_outside_segments(self) -> None:
        engine = ZoomEngine()
        assert engine.get_speed_at(500, 10000) == 1.0

    def test_speed_inside_segment(self) -> None:
        engine = ZoomEngine()
        engine.add_keyframe(
            ZoomKeyframe.create(timestamp=1000, zoom=2.0, duration=400, speed=2.0)
        )
        engine.add_keyframe(
            ZoomKeyframe.create(timestamp=3000, zoom=1.0, duration=600)
        )
        # Inside the segment
        assert engine.get_speed_at(2000, 10000) == 2.0
        # Outside the segment (before)
        assert engine.get_speed_at(500, 10000) == 1.0
        # Outside the segment (after zoom-out completes)
        assert engine.get_speed_at(4000, 10000) == 1.0

    def test_speed_at_segment_boundary(self) -> None:
        engine = ZoomEngine()
        engine.add_keyframe(
            ZoomKeyframe.create(timestamp=1000, zoom=2.0, duration=400, speed=0.5)
        )
        engine.add_keyframe(
            ZoomKeyframe.create(timestamp=3000, zoom=1.0, duration=600)
        )
        # Exactly at start
        assert engine.get_speed_at(1000, 10000) == 0.5
        # Exactly at end (zoom-out + duration)
        assert engine.get_speed_at(3600, 10000) == 0.5

    def test_multiple_segments_different_speeds(self) -> None:
        engine = ZoomEngine()
        # Segment 1: 1000-2600 at 2x
        engine.add_keyframe(
            ZoomKeyframe.create(timestamp=1000, zoom=2.0, duration=400, speed=2.0)
        )
        engine.add_keyframe(
            ZoomKeyframe.create(timestamp=2000, zoom=1.0, duration=600)
        )
        # Segment 2: 4000-5600 at 0.5x
        engine.add_keyframe(
            ZoomKeyframe.create(timestamp=4000, zoom=1.5, duration=400, speed=0.5)
        )
        engine.add_keyframe(
            ZoomKeyframe.create(timestamp=5000, zoom=1.0, duration=600)
        )
        assert engine.get_speed_at(1500, 10000) == 2.0
        assert engine.get_speed_at(3000, 10000) == 1.0
        assert engine.get_speed_at(4500, 10000) == 0.5

    def test_output_duration_no_speed_changes(self) -> None:
        engine = ZoomEngine()
        assert engine.compute_output_duration(10000) == pytest.approx(10000)

    def test_output_duration_with_speed(self) -> None:
        engine = ZoomEngine()
        # Segment from 2000-4000ms (zoom-out at 3000 + 1000ms duration) at 2x
        engine.add_keyframe(
            ZoomKeyframe.create(timestamp=2000, zoom=2.0, duration=0, speed=2.0)
        )
        engine.add_keyframe(
            ZoomKeyframe.create(timestamp=3000, zoom=1.0, duration=1000)
        )
        # Segment is 2000ms long (2000 to 4000), at 2x → output = 1000ms
        # Rest of 10000ms recording: 0-2000 (2000ms) + 4000-10000 (6000ms) = 8000ms
        # Total output: 8000 + 1000 = 9000ms
        result = engine.compute_output_duration(10000)
        assert result == pytest.approx(9000)

    def test_output_duration_with_trim(self) -> None:
        engine = ZoomEngine()
        engine.add_keyframe(
            ZoomKeyframe.create(timestamp=2000, zoom=2.0, duration=0, speed=2.0)
        )
        engine.add_keyframe(
            ZoomKeyframe.create(timestamp=3000, zoom=1.0, duration=1000)
        )
        # Trim to 1000-5000: includes segment 2000-4000 at 2x
        # 1000-2000 = 1000ms at 1x → 1000ms
        # 2000-4000 = 2000ms at 2x → 1000ms
        # 4000-5000 = 1000ms at 1x → 1000ms
        # Total: 3000ms
        result = engine.compute_output_duration(10000, 1000, 5000)
        assert result == pytest.approx(3000)

    def test_output_duration_slow_motion(self) -> None:
        engine = ZoomEngine()
        # Segment at 0.5x (slow motion)
        engine.add_keyframe(
            ZoomKeyframe.create(timestamp=0, zoom=2.0, duration=0, speed=0.5)
        )
        engine.add_keyframe(
            ZoomKeyframe.create(timestamp=5000, zoom=1.0, duration=0)
        )
        # 5000ms at 0.5x → 10000ms output for the segment
        # 5000-10000ms at 1x → 5000ms
        # Total: 15000ms
        result = engine.compute_output_duration(10000)
        assert result == pytest.approx(15000)

    def test_speed_preserved_in_undo(self) -> None:
        engine = ZoomEngine()
        engine.add_keyframe(
            ZoomKeyframe.create(timestamp=1000, zoom=2.0, duration=400, speed=3.0)
        )
        engine.push_undo()
        engine.keyframes[0].speed = 1.0
        engine.undo()
        assert engine.keyframes[0].speed == 3.0
