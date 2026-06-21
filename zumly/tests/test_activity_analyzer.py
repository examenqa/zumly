"""Tests for app.activity_analyzer click-driven auto-zoom logic."""

import pytest

from app.activity_analyzer import analyze_activity, detect_chapters, _dampen_pan
from app.models import ClickEvent, KeyEvent, MousePosition


MONITOR = {"left": 0, "top": 0, "width": 1920, "height": 1080}


def _make_track(
    duration_ms: int,
    interval: int = 16,
    *,
    x: float = 500.0,
    y: float = 500.0,
) -> list[MousePosition]:
    """Generate a mostly stationary mouse track of given duration."""
    return [
        MousePosition(x=x, y=y, timestamp=float(t))
        for t in range(0, duration_ms + 1, interval)
    ]


def _make_shift_track() -> list[MousePosition]:
    """Generate a track with a large cursor move around the midpoint."""
    track: list[MousePosition] = []
    for t in range(0, 20001, 16):
        if t < 10000:
            x, y = 200.0, 200.0
        else:
            x, y = 1700.0, 900.0
        track.append(MousePosition(x=x, y=y, timestamp=float(t)))
    return track


def _extract_zoom_segments(keyframes: list) -> list[tuple[float, float]]:
    """Return (start, end) tuples for each zoom block."""
    segments: list[tuple[float, float]] = []
    start: float | None = None
    for keyframe in sorted(keyframes, key=lambda item: item.timestamp):
        if keyframe.zoom > 1.01 and start is None:
            start = float(keyframe.timestamp)
        elif keyframe.zoom <= 1.01 and start is not None:
            segments.append((start, float(keyframe.timestamp + keyframe.duration)))
            start = None
    return segments


class TestDampenPan:
    def test_no_zoom_returns_center(self) -> None:
        px, py = _dampen_pan(0.3, 0.7, zoom=1.0)
        assert px == 0.5
        assert py == 0.5

    def test_target_far_shifts_viewport(self) -> None:
        px, py = _dampen_pan(0.9, 0.9, zoom=2.0, from_x=0.5, from_y=0.5)
        assert px > 0.5
        assert py > 0.5

    def test_clamps_to_visible_bounds(self) -> None:
        px, py = _dampen_pan(1.0, 1.0, zoom=2.0)
        half = 0.5 / 2.0
        assert half <= px <= 1.0 - half
        assert half <= py <= 1.0 - half


class TestAnalyzeActivity:
    def test_empty_track_returns_no_keyframes(self) -> None:
        assert analyze_activity([], MONITOR) == []

    def test_too_few_samples_returns_no_keyframes(self) -> None:
        short = [MousePosition(x=0, y=0, timestamp=float(i * 16)) for i in range(5)]
        assert analyze_activity(short, MONITOR) == []

    def test_stationary_mouse_without_clicks_returns_no_keyframes(self) -> None:
        assert analyze_activity(_make_track(5000), MONITOR) == []

    def test_removed_keystrokes_are_ignored(self) -> None:
        track = _make_track(10000, x=960, y=540)
        keys = [KeyEvent(timestamp=3000.0 + i * 50.0) for i in range(20)]
        assert analyze_activity(track, MONITOR, key_events=keys) == []

    def test_zoom_level_zero_does_not_raise(self) -> None:
        track = _make_track(10000, x=960, y=540)
        clicks = [ClickEvent(x=960, y=540, timestamp=3000.0)]
        result = analyze_activity(track, MONITOR, click_events=clicks, zoom_level=0.0)
        assert isinstance(result, list)

    def test_single_click_generates_zoom(self) -> None:
        track = _make_track(10000, x=960, y=540)
        clicks = [ClickEvent(x=800, y=400, timestamp=5000.0)]
        keyframes = analyze_activity(track, MONITOR, click_events=clicks)
        assert len(keyframes) >= 2
        assert any(keyframe.zoom > 1.01 for keyframe in keyframes)

    def test_click_cluster_targets_click_position(self) -> None:
        track = _make_track(10000, x=100, y=100)
        clicks = [
            ClickEvent(x=1600, y=820, timestamp=5000.0),
            ClickEvent(x=1620, y=830, timestamp=5200.0),
        ]
        keyframes = analyze_activity(track, MONITOR, click_events=clicks)
        zoom_ins = [keyframe for keyframe in keyframes if keyframe.zoom > 1.01]
        assert zoom_ins
        assert zoom_ins[0].x > 0.6
        assert zoom_ins[0].y > 0.6

    def test_follow_cursor_false_centers_zoom(self) -> None:
        track = _make_track(10000, x=100, y=100)
        clicks = [ClickEvent(x=1600, y=820, timestamp=5000.0)]
        keyframes = analyze_activity(
            track,
            MONITOR,
            click_events=clicks,
            follow_cursor=False,
        )
        zoom_ins = [keyframe for keyframe in keyframes if keyframe.zoom > 1.01]
        assert zoom_ins
        assert zoom_ins[0].x == pytest.approx(0.5, abs=0.1)
        assert zoom_ins[0].y == pytest.approx(0.5, abs=0.1)

    def test_custom_zoom_level_is_respected(self) -> None:
        track = _make_track(10000, x=960, y=540)
        clicks = [ClickEvent(x=960, y=540, timestamp=5000.0)]
        keyframes = analyze_activity(track, MONITOR, click_events=clicks, zoom_level=2.5)
        zoom_ins = [keyframe for keyframe in keyframes if keyframe.zoom > 1.01]
        assert zoom_ins
        assert all(keyframe.zoom == pytest.approx(2.5) for keyframe in zoom_ins)

    def test_keyframes_are_sorted(self) -> None:
        track = _make_track(10000, x=960, y=540)
        clicks = [
            ClickEvent(x=300, y=200, timestamp=2000.0),
            ClickEvent(x=1600, y=800, timestamp=7000.0),
        ]
        keyframes = analyze_activity(track, MONITOR, click_events=clicks)
        assert keyframes == sorted(keyframes, key=lambda item: item.timestamp)

    def test_zoom_segments_do_not_overlap(self) -> None:
        track = _make_track(20000, x=960, y=540)
        clicks = [
            ClickEvent(x=300, y=200, timestamp=3000.0),
            ClickEvent(x=1600, y=800, timestamp=11000.0),
        ]
        segments = _extract_zoom_segments(analyze_activity(track, MONITOR, click_events=clicks))
        assert len(segments) >= 2
        for previous, current in zip(segments, segments[1:]):
            assert previous[1] <= current[0] + 1

    def test_far_apart_click_clusters_create_multiple_zoom_blocks(self) -> None:
        track = _make_shift_track()
        clicks = [
            ClickEvent(x=200, y=200, timestamp=2000.0),
            ClickEvent(x=1700, y=900, timestamp=14000.0),
        ]
        segments = _extract_zoom_segments(analyze_activity(track, MONITOR, click_events=clicks))
        assert len(segments) >= 2

    def test_max_clusters_limits_zoom_blocks(self) -> None:
        track = _make_track(30000, x=960, y=540)
        clicks = [
            ClickEvent(x=250, y=200, timestamp=2000.0),
            ClickEvent(x=1650, y=250, timestamp=8000.0),
            ClickEvent(x=250, y=850, timestamp=14000.0),
            ClickEvent(x=1650, y=850, timestamp=20000.0),
            ClickEvent(x=960, y=540, timestamp=26000.0),
        ]
        segments = _extract_zoom_segments(
            analyze_activity(track, MONITOR, click_events=clicks, max_clusters=3)
        )
        assert len(segments) <= 3


class TestDetectChapters:
    def test_click_gaps_create_boundaries(self) -> None:
        mouse_events = [
            MousePosition(100, 100, 0.0),
            MousePosition(100, 100, 500.0),
            MousePosition(100, 100, 6000.0),
            MousePosition(100, 100, 6500.0),
        ]

        chapters = detect_chapters(mouse_events, None, None, 10000.0)

        assert [chapter.timestamp_ms for chapter in chapters] == [0, 6000]

    def test_removed_keystrokes_do_not_change_chapters(self) -> None:
        mouse_events = [
            MousePosition(100, 100, 0.0),
            MousePosition(100, 100, 500.0),
            MousePosition(100, 100, 6000.0),
            MousePosition(100, 100, 6500.0),
        ]
        key_events = [KeyEvent(timestamp=2500.0), KeyEvent(timestamp=7000.0)]

        without_keys = detect_chapters(mouse_events, None, None, 10000.0)
        with_keys = detect_chapters(mouse_events, key_events, None, 10000.0)

        assert with_keys == without_keys
