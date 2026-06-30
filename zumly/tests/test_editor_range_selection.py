from app.models import RecordingSession, VideoSegment, ZoomKeyframe
from app.widgets.editor_window import EditorWindow
from app.zoom_engine import ZoomEngine


class _StubPreview:
    _video_duration_ms = 10000.0
    _current_time_ms = 0.0

    def set_video_segments(self, segments):
        self.video_segments = segments

    def set_cursor_data(self, mouse_track, monitor_rect, click_events=None):
        self.cursor_data = (mouse_track, monitor_rect, click_events or [])

    def set_debug_keyframes(self, keyframes):
        self.keyframes = keyframes

    def set_zoom(self, zoom, pan_x, pan_y):
        self.zoom = (zoom, pan_x, pan_y)


class _StubTimeline:
    def set_data(self, **kwargs):
        self.data = kwargs

    def set_current_time(self, time_ms):
        self.current_time_ms = time_ms

    def set_video_segments(self, segments, selected_index=-1):
        self.video_segments = segments
        self.selected_index = selected_index


class _StubEditor:
    def set_selected_segment_speed(self, speed, index=-1):
        self.speed = speed
        self.index = index

    def refresh(self, **kwargs):
        self.info = kwargs

    def set_undo_redo_enabled(self, can_undo, can_redo):
        self.can_undo = can_undo
        self.can_redo = can_redo


def _window_with_segments(segments: list[VideoSegment]) -> EditorWindow:
    return _window_with_state(video_segments=segments)


def _window_with_state(
    keyframes: list[ZoomKeyframe] | None = None,
    video_segments: list[VideoSegment] | None = None,
) -> EditorWindow:
    window = EditorWindow.__new__(EditorWindow)
    window._session = RecordingSession(
        id="range-test",
        start_time=0.0,
        duration=10000.0,
        mouse_track=[],
        keyframes=keyframes or [],
        video_segments=video_segments or [],
    )
    window._preview = _StubPreview()
    window._timeline = _StubTimeline()
    window._editor = _StubEditor()
    window._zoom_engine = ZoomEngine()
    window._zoom_engine.keyframes = window._session.keyframes
    window._zoom_engine.video_segments = window._session.video_segments or []
    window._selected_video_segment_index = -1
    window._timeline_drag_undo_pushed = False
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


def test_zoom_segment_move_updates_start_and_end_keyframes_together() -> None:
    zoom_in = ZoomKeyframe.create(timestamp=1000.0, zoom=1.6, duration=500.0)
    pan_point = ZoomKeyframe.create(timestamp=2500.0, zoom=1.6, duration=500.0)
    zoom_out = ZoomKeyframe.create(timestamp=4000.0, zoom=1.0, duration=600.0)
    window = _window_with_state(keyframes=[zoom_in, pan_point, zoom_out])

    EditorWindow._on_zoom_segment_moved(window, zoom_in.id, zoom_out.id, 2000.0, 5000.0)

    assert zoom_in.timestamp == 2000.0
    assert pan_point.timestamp == 3500.0
    assert zoom_out.timestamp == 5000.0
    assert window._zoom_engine.keyframes == [zoom_in, pan_point, zoom_out]
    assert window._timeline.data["keyframes"] == [zoom_in, pan_point, zoom_out]
    assert window._editor.can_undo


def test_zoom_segment_move_clamps_visible_end_to_recording_duration() -> None:
    zoom_in = ZoomKeyframe.create(timestamp=1000.0, zoom=1.6, duration=500.0)
    zoom_out = ZoomKeyframe.create(timestamp=4000.0, zoom=1.0, duration=600.0)
    window = _window_with_state(keyframes=[zoom_in, zoom_out])

    EditorWindow._on_zoom_segment_moved(window, zoom_in.id, zoom_out.id, 8000.0, 11000.0)

    assert zoom_in.timestamp == 6400.0
    assert zoom_out.timestamp == 9400.0


def test_zoom_resize_clamps_against_neighbor_and_duration() -> None:
    zoom_in = ZoomKeyframe.create(timestamp=1000.0, zoom=1.6, duration=500.0)
    zoom_out = ZoomKeyframe.create(timestamp=4000.0, zoom=1.0, duration=600.0)
    window = _window_with_state(keyframes=[zoom_in, zoom_out])

    EditorWindow._on_zoom_keyframe_moved(window, zoom_in.id, 3950.0)
    EditorWindow._on_zoom_keyframe_moved(window, zoom_out.id, 9700.0)

    assert zoom_in.timestamp == 3900.0
    assert zoom_out.timestamp == 9400.0


def test_zoom_segment_delete_removes_pan_points_inside_segment() -> None:
    zoom_in = ZoomKeyframe.create(timestamp=1000.0, zoom=1.6, duration=500.0)
    pan_point = ZoomKeyframe.create(timestamp=2500.0, zoom=1.6, duration=500.0)
    zoom_out = ZoomKeyframe.create(timestamp=4000.0, zoom=1.0, duration=600.0)
    next_zoom = ZoomKeyframe.create(timestamp=7000.0, zoom=1.4, duration=500.0)
    window = _window_with_state(keyframes=[zoom_in, pan_point, zoom_out, next_zoom])

    EditorWindow._on_zoom_segment_deleted(window, zoom_in.id)

    assert window._session.keyframes == [next_zoom]


def test_undo_redo_restores_zoom_edit_state() -> None:
    zoom_in = ZoomKeyframe.create(timestamp=1000.0, zoom=1.6, duration=500.0)
    zoom_out = ZoomKeyframe.create(timestamp=4000.0, zoom=1.0, duration=600.0)
    window = _window_with_state(keyframes=[zoom_in, zoom_out])

    EditorWindow._on_zoom_segment_moved(window, zoom_in.id, zoom_out.id, 2000.0, 5000.0)
    EditorWindow._on_timeline_drag_finished(window)
    EditorWindow._on_undo_requested(window)

    assert [kf.timestamp for kf in window._session.keyframes] == [1000.0, 4000.0]
    assert window._editor.can_redo

    EditorWindow._on_redo_requested(window)

    assert [kf.timestamp for kf in window._session.keyframes] == [2000.0, 5000.0]


def test_undo_redo_restores_range_speed_state() -> None:
    segment = VideoSegment.create(0.0, 10000.0, 1.0)
    window = _window_with_segments([segment])
    window._selected_video_segment_index = 0

    EditorWindow._on_segment_speed_changed(window, 4.0)
    EditorWindow._on_undo_requested(window)

    assert window._session.video_segments[0].speed == 1.0
    assert window._editor.can_redo

    EditorWindow._on_redo_requested(window)

    assert window._session.video_segments[0].speed == 4.0
