"""Qt-level tests for the timeline widget readout."""

import pytest
from PySide6.QtCore import Qt
from PySide6.QtGui import QFont
from PySide6.QtWidgets import QApplication, QPushButton

from app.models import Chapter, VoiceoverSegment
from app.widgets.timeline_widget import TimelineWidget, _TimelineTimeReadout, _TimelineTrack


@pytest.fixture(scope="module")
def qapp():
    """Create QApplication instance for Qt widget tests."""
    app = QApplication.instance()
    if app is None:
        app = QApplication([])
    yield app


class TestTimelineTimeReadout:
    """Verify the custom time display stays stable during playback updates."""

    def test_disables_subpixel_antialiasing(self, qapp):
        readout = _TimelineTimeReadout()

        strategy = readout.font().styleStrategy()

        assert bool(strategy & QFont.StyleStrategy.NoSubpixelAntialias)

    def test_updates_current_and_total_text(self, qapp):
        readout = _TimelineTimeReadout()

        readout.set_times(41250.0, 387500.0)

        assert readout.current_text == "0:41.25"
        assert readout.total_text == "6:27.50"
        assert readout.width() >= readout.fontMetrics().horizontalAdvance(
            "0:41.25 / 6:27.50"
        )


class TestTimelineWidget:
    """Verify TimelineWidget wires the custom readout from set_data()."""

    def test_set_data_updates_time_display(self, qapp):
        widget = TimelineWidget()

        widget.set_data(
            duration=387500.0,
            current_time=41250.0,
            keyframes=[],
            mouse_track=[],
        )

        assert widget._time_display.current_text == "0:41.25"
        assert widget._time_display.total_text == "6:27.50"

    def test_chapters_property_updates_track(self, qapp):
        widget = TimelineWidget()
        chapters = [Chapter(timestamp_ms=1200, name="Set up the workflow", auto_detected=True)]

        widget.chapters = chapters

        assert widget._track.chapters == chapters

    def test_playback_controls_use_single_opaque_host(self, qapp):
        widget = TimelineWidget()

        play_buttons = [
            button
            for button in widget._controls_host.findChildren(QPushButton)
            if button.objectName() == "PlayBtn"
        ]

        assert widget._controls_host.objectName() == "PlaybackControls"
        assert widget._controls_host.testAttribute(Qt.WidgetAttribute.WA_OpaquePaintEvent)
        assert len(play_buttons) == 1


class TestTimelineTrackChapters:
    """Verify chapter hit-testing uses the painted marker cache."""

    def test_hit_test_returns_chapter_marker(self, qapp):
        track = _TimelineTrack()
        chapter = Chapter(timestamp_ms=9000, name="Review the result", auto_detected=True)
        track._chapter_markers = [(48.0, 120.0, 16.0, chapter)]

        assert track._chapter_hit_test(50.0, 126.0) == chapter
        assert track._chapter_hit_test(5.0, 20.0) is None


def _make_vo_seg(tts_generating: bool = False, audio_path: str = "") -> VoiceoverSegment:
    """Helper to create a minimal VoiceoverSegment for tests."""
    seg = VoiceoverSegment.create(timestamp=1000.0, text="Hello world")
    seg.tts_generating = tts_generating
    seg.audio_path = audio_path
    return seg


class TestVoiceoverGenerationSpinner:
    """Verify the spinner timer starts/stops based on generating segment state."""

    def test_spinner_timer_starts_when_segment_is_generating(self, qapp):
        track = _TimelineTrack()
        track.voiceover_segments = [_make_vo_seg(tts_generating=True)]

        track._update_spinner_timer()

        assert track._spinner_timer.isActive()

    def test_spinner_timer_stops_when_no_generating_segments(self, qapp):
        track = _TimelineTrack()
        track.voiceover_segments = [_make_vo_seg(tts_generating=True)]
        track._update_spinner_timer()
        # now all segments are done
        track.voiceover_segments = [_make_vo_seg(tts_generating=False)]

        track._update_spinner_timer()

        assert not track._spinner_timer.isActive()

    def test_spinner_timer_not_started_with_no_generating_segments(self, qapp):
        track = _TimelineTrack()
        track.voiceover_segments = [_make_vo_seg(tts_generating=False)]

        track._update_spinner_timer()

        assert not track._spinner_timer.isActive()

    def test_spinner_phase_advances_on_tick(self, qapp):
        track = _TimelineTrack()
        track._spinner_phase = 0

        track._on_spinner_tick()

        assert track._spinner_phase == 36

    def test_spinner_phase_wraps_at_360(self, qapp):
        track = _TimelineTrack()
        track._spinner_phase = 352

        track._on_spinner_tick()

        assert track._spinner_phase == 28  # (352 + 36) % 360

    def test_set_data_starts_spinner_timer_for_generating_segment(self, qapp):
        widget = TimelineWidget()
        seg = _make_vo_seg(tts_generating=True)

        widget.set_data(
            duration=5000.0,
            current_time=0.0,
            keyframes=[],
            mouse_track=[],
            voiceover_segments=[seg],
        )

        assert widget._track._spinner_timer.isActive()

    def test_set_data_stops_spinner_timer_when_generation_done(self, qapp):
        widget = TimelineWidget()
        generating_seg = _make_vo_seg(tts_generating=True)
        widget.set_data(
            duration=5000.0,
            current_time=0.0,
            keyframes=[],
            mouse_track=[],
            voiceover_segments=[generating_seg],
        )
        done_seg = _make_vo_seg(tts_generating=False, audio_path="/fake/vo.wav")

        widget.set_data(
            duration=5000.0,
            current_time=0.0,
            keyframes=[],
            mouse_track=[],
            voiceover_segments=[done_seg],
        )

        assert not widget._track._spinner_timer.isActive()

    def test_tts_generating_not_serialized(self, qapp):
        """tts_generating is a transient flag and must not appear in the saved dict."""
        seg = _make_vo_seg(tts_generating=True)
        d = seg.to_dict()

        assert "ttsGenerating" not in d
        assert "tts_generating" not in d
