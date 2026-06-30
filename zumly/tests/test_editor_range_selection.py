from types import SimpleNamespace

from app.models import RecordingSession, VideoSegment
from app.widgets.editor_window import EditorWindow


class _StubPreview:
    _video_duration_ms = 10000.0

    def set_video_segments(self, segments):
        self.video_segments = segments


class _StubTimeline:
    def set_video_segments(self, segments, selected_index=-1):
        self.video_segments = segments
        self.selected_index = selected_index


class _StubEditor:
    def set_selected_segment_speed(self, speed, index=-1):
        self.speed = speed
        self.index = index


def _window_with_segments(segments: list[VideoSegment]) -> EditorWindow:
    window = EditorWindow.__new__(EditorWindow)
    window._session = RecordingSession(
        id="range-test",
        start_time=0.0,
        duration=10000.0,
        mouse_track=[],
        keyframes=[],
        video_segments=segments,
    )
    window._preview = _StubPreview()
    window._timeline = _StubTimeline()
    window._editor = _StubEditor()
    window._zoom_engine = SimpleNamespace(video_segments=[])
    window._selected_video_segment_index = -1
    window._project_path = None
    window._project_data = {}
    window._save_project = lambda: None
    return window


def test_range_selection_creates_exact_selected_segment_and_preserves_speeds() -> None:
    window = _window_with_segments(
        [
            VideoSegment.create(0.0, 3000.0, 2.0),
            VideoSegment.create(3000.0, 7000.0, 1.5),
            VideoSegment.create(7000.0, 10000.0, 4.0),
        ]
    )

    EditorWindow._on_range_selection_requested(window, 2000.0, 5000.0)

    segments = window._session.video_segments
    assert [(s.start_ms, s.end_ms, s.speed) for s in segments] == [
        (0.0, 2000.0, 2.0),
        (2000.0, 5000.0, 1.5),
        (5000.0, 7000.0, 1.5),
        (7000.0, 10000.0, 4.0),
    ]
    assert window._selected_video_segment_index == 1
    assert window._timeline.selected_index == 1
    assert window._editor.speed == 1.5
