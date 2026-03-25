"""Tests for app.models — dataclass serialization roundtrips."""

import json
import uuid

import pytest

from app.models import (
    MousePosition,
    KeyEvent,
    ClickEvent,
    ZoomKeyframe,
    RecordingSession,
    VoiceoverSegment,
    VideoSegment,
    DEFAULT_FPS,
    DEFAULT_MOUSE_INTERVAL,
)


# ── MousePosition ──────────────────────────────────────────────────


class TestMousePosition:
    def test_roundtrip(self) -> None:
        mp = MousePosition(x=123.5, y=456.7, timestamp=789.0)
        d = mp.to_dict()
        mp2 = MousePosition.from_dict(d)
        assert mp2.x == mp.x
        assert mp2.y == mp.y
        assert mp2.timestamp == mp.timestamp

    def test_dict_keys(self) -> None:
        d = MousePosition(x=1, y=2, timestamp=3).to_dict()
        assert set(d.keys()) == {"x", "y", "timestamp"}


# ── KeyEvent ────────────────────────────────────────────────────────


class TestKeyEvent:
    def test_roundtrip(self) -> None:
        ke = KeyEvent(timestamp=42.0)
        d = ke.to_dict()
        ke2 = KeyEvent.from_dict(d)
        assert ke2.timestamp == ke.timestamp
        assert ke2.x is None
        assert ke2.y is None

    def test_roundtrip_with_position(self) -> None:
        ke = KeyEvent(timestamp=42.0, x=960.0, y=540.0)
        d = ke.to_dict()
        ke2 = KeyEvent.from_dict(d)
        assert ke2.timestamp == ke.timestamp
        assert ke2.x == 960.0
        assert ke2.y == 540.0

    def test_dict_keys_without_position(self) -> None:
        d = KeyEvent(timestamp=0).to_dict()
        assert set(d.keys()) == {"timestamp"}

    def test_dict_keys_with_position(self) -> None:
        d = KeyEvent(timestamp=0, x=100.0, y=200.0).to_dict()
        assert set(d.keys()) == {"timestamp", "x", "y"}

    def test_from_dict_backward_compat(self) -> None:
        """Old projects without x/y in KeyEvent should still load."""
        d = {"timestamp": 99.0}
        ke = KeyEvent.from_dict(d)
        assert ke.timestamp == 99.0
        assert ke.x is None
        assert ke.y is None


# ── ClickEvent ──────────────────────────────────────────────────────


class TestClickEvent:
    def test_roundtrip(self) -> None:
        ce = ClickEvent(x=10, y=20, timestamp=30)
        d = ce.to_dict()
        ce2 = ClickEvent.from_dict(d)
        assert ce2.x == ce.x
        assert ce2.y == ce.y
        assert ce2.timestamp == ce.timestamp


# ── ZoomKeyframe ────────────────────────────────────────────────────


class TestZoomKeyframe:
    def test_create_generates_uuid(self) -> None:
        kf = ZoomKeyframe.create(timestamp=100, zoom=1.5)
        # Must be a valid UUID4
        uuid.UUID(kf.id)

    def test_create_defaults(self) -> None:
        kf = ZoomKeyframe.create(timestamp=100, zoom=1.5)
        assert kf.x == 0.5
        assert kf.y == 0.5
        assert kf.duration == 600.0
        assert kf.reason == ""

    def test_create_custom(self) -> None:
        kf = ZoomKeyframe.create(
            timestamp=200, zoom=2.0, x=0.3, y=0.7,
            duration=400, reason="test"
        )
        assert kf.timestamp == 200
        assert kf.zoom == 2.0
        assert kf.x == 0.3
        assert kf.y == 0.7
        assert kf.duration == 400
        assert kf.reason == "test"

    def test_roundtrip(self) -> None:
        kf = ZoomKeyframe.create(
            timestamp=500, zoom=1.25, x=0.2, y=0.8, duration=300, reason="r"
        )
        d = kf.to_dict()
        kf2 = ZoomKeyframe.from_dict(d)
        assert kf2.id == kf.id
        assert kf2.timestamp == kf.timestamp
        assert kf2.zoom == kf.zoom
        assert kf2.x == kf.x
        assert kf2.y == kf.y
        assert kf2.duration == kf.duration
        assert kf2.reason == kf.reason

    def test_reason_omitted_when_empty(self) -> None:
        kf = ZoomKeyframe.create(timestamp=0, zoom=1.0)
        d = kf.to_dict()
        assert "reason" not in d

    def test_from_dict_ignores_unknown_keys(self) -> None:
        d = {
            "id": "abc",
            "timestamp": 10,
            "zoom": 1.0,
            "x": 0.5,
            "y": 0.5,
            "duration": 600,
            "future_field": True,
        }
        kf = ZoomKeyframe.from_dict(d)
        assert kf.id == "abc"
        assert not hasattr(kf, "future_field")


# ── RecordingSession ────────────────────────────────────────────────


class TestRecordingSession:
    def test_json_roundtrip(self, sample_session: RecordingSession) -> None:
        s = sample_session.to_json()
        s2 = RecordingSession.from_json(s)
        assert s2.id == sample_session.id
        assert s2.start_time == sample_session.start_time
        assert s2.duration == sample_session.duration
        assert len(s2.mouse_track) == len(sample_session.mouse_track)
        assert len(s2.keyframes) == len(sample_session.keyframes)

    def test_json_includes_key_events(self, sample_session: RecordingSession) -> None:
        s = sample_session.to_json()
        d = json.loads(s)
        assert "keyEvents" in d
        assert len(d["keyEvents"]) == 2

    def test_json_includes_click_events(self, sample_session: RecordingSession) -> None:
        s = sample_session.to_json()
        d = json.loads(s)
        assert "clickEvents" in d
        assert len(d["clickEvents"]) == 1

    def test_json_includes_frame_timestamps(self, sample_session: RecordingSession) -> None:
        s = sample_session.to_json()
        d = json.loads(s)
        assert "frameTimestamps" in d
        assert len(d["frameTimestamps"]) == 20

    def test_json_includes_trim(self, sample_session: RecordingSession) -> None:
        s = sample_session.to_json()
        d = json.loads(s)
        assert d["trimStartMs"] == 32.0
        assert d["trimEndMs"] == 288.0

    def test_json_omits_defaults(self) -> None:
        """Optional fields should be absent when they hold default values."""
        session = RecordingSession(
            id="bare",
            start_time=0,
            duration=100,
            mouse_track=[MousePosition(0, 0, 0)],
            keyframes=[],
        )
        d = json.loads(session.to_json())
        assert "keyEvents" not in d
        assert "clickEvents" not in d
        assert "frameTimestamps" not in d
        assert "trimStartMs" not in d
        assert "trimEndMs" not in d

    def test_roundtrip_preserves_mouse_positions(self, sample_session: RecordingSession) -> None:
        s2 = RecordingSession.from_json(sample_session.to_json())
        for orig, loaded in zip(sample_session.mouse_track, s2.mouse_track):
            assert orig.x == loaded.x
            assert orig.y == loaded.y
            assert orig.timestamp == loaded.timestamp

    def test_roundtrip_preserves_keyframe_fields(self, sample_session: RecordingSession) -> None:
        s2 = RecordingSession.from_json(sample_session.to_json())
        for orig, loaded in zip(sample_session.keyframes, s2.keyframes):
            assert orig.id == loaded.id
            assert orig.zoom == loaded.zoom

    def test_roundtrip_preserves_trim(self, sample_session: RecordingSession) -> None:
        s2 = RecordingSession.from_json(sample_session.to_json())
        assert s2.trim_start_ms == sample_session.trim_start_ms
        assert s2.trim_end_ms == sample_session.trim_end_ms

    def test_json_includes_voiceover_segments(self) -> None:
        seg = VoiceoverSegment.create(timestamp=1000, text="Hello world", voice="echo")
        session = RecordingSession(
            id="vo", start_time=0, duration=5000,
            mouse_track=[MousePosition(0, 0, 0)],
            keyframes=[],
            voiceover_segments=[seg],
        )
        d = json.loads(session.to_json())
        assert "voiceoverSegments" in d
        assert len(d["voiceoverSegments"]) == 1
        assert d["voiceoverSegments"][0]["text"] == "Hello world"

    def test_json_roundtrip_voiceover(self) -> None:
        seg = VoiceoverSegment.create(timestamp=2000, text="Test narration", voice="nova")
        session = RecordingSession(
            id="vo2", start_time=0, duration=5000,
            mouse_track=[MousePosition(0, 0, 0)],
            keyframes=[],
            voiceover_segments=[seg],
        )
        s2 = RecordingSession.from_json(session.to_json())
        assert s2.voiceover_segments is not None
        assert len(s2.voiceover_segments) == 1
        assert s2.voiceover_segments[0].text == "Test narration"
        assert s2.voiceover_segments[0].voice == "nova"
        assert s2.voiceover_segments[0].timestamp == 2000

    def test_json_omits_voiceover_when_empty(self) -> None:
        session = RecordingSession(
            id="bare2", start_time=0, duration=100,
            mouse_track=[MousePosition(0, 0, 0)],
            keyframes=[],
        )
        d = json.loads(session.to_json())
        assert "voiceoverSegments" not in d


# ── VoiceoverSegment ────────────────────────────────────────────────


class TestVoiceoverSegment:
    def test_create_generates_uuid(self) -> None:
        seg = VoiceoverSegment.create(timestamp=1000, text="Hello")
        assert seg.id
        uuid.UUID(seg.id)  # validates format

    def test_create_defaults(self) -> None:
        seg = VoiceoverSegment.create(timestamp=500, text="Test")
        assert seg.voice == "en-US-Ava:DragonHDLatestNeural"
        assert seg.audio_path == ""
        assert seg.duration_ms == 0.0
        assert seg.rate == 1.0
        assert seg.volume == 1.0

    def test_roundtrip(self) -> None:
        seg = VoiceoverSegment.create(timestamp=1500, text="Test text", voice="echo")
        d = seg.to_dict()
        seg2 = VoiceoverSegment.from_dict(d)
        assert seg2.id == seg.id
        assert seg2.timestamp == seg.timestamp
        assert seg2.text == seg.text
        assert seg2.voice == seg.voice

    def test_dict_omits_zero_duration(self) -> None:
        seg = VoiceoverSegment.create(timestamp=0, text="Test")
        d = seg.to_dict()
        assert "durationMs" not in d

    def test_dict_includes_nonzero_duration(self) -> None:
        seg = VoiceoverSegment.create(timestamp=0, text="Test")
        seg.duration_ms = 3000.0
        d = seg.to_dict()
        assert d["durationMs"] == 3000.0

    def test_from_dict_backward_compat(self) -> None:
        d = {"id": "abc", "timestamp": 100, "text": "Hi"}
        seg = VoiceoverSegment.from_dict(d)
        assert seg.voice == "en-US-Ava:DragonHDLatestNeural"
        assert seg.duration_ms == 0.0
        assert seg.rate == 1.0
        assert seg.volume == 1.0


# ── VideoSegment ────────────────────────────────────────────────────


class TestVideoSegment:
    def test_create_generates_uuid(self) -> None:
        seg = VideoSegment.create(start_ms=0, end_ms=5000)
        assert seg.id
        uuid.UUID(seg.id)  # validates format

    def test_create_defaults(self) -> None:
        seg = VideoSegment.create(start_ms=1000, end_ms=3000)
        assert seg.start_ms == 1000
        assert seg.end_ms == 3000
        assert seg.speed == 1.0

    def test_create_custom_speed(self) -> None:
        seg = VideoSegment.create(start_ms=0, end_ms=5000, speed=2.0)
        assert seg.speed == 2.0

    def test_roundtrip(self) -> None:
        seg = VideoSegment.create(start_ms=100, end_ms=4000, speed=0.5)
        d = seg.to_dict()
        seg2 = VideoSegment.from_dict(d)
        assert seg2.id == seg.id
        assert seg2.start_ms == seg.start_ms
        assert seg2.end_ms == seg.end_ms
        assert seg2.speed == seg.speed

    def test_dict_omits_default_speed(self) -> None:
        seg = VideoSegment.create(start_ms=0, end_ms=5000)
        d = seg.to_dict()
        assert "speed" not in d

    def test_dict_includes_nondefault_speed(self) -> None:
        seg = VideoSegment.create(start_ms=0, end_ms=5000, speed=1.5)
        d = seg.to_dict()
        assert d["speed"] == 1.5

    def test_from_dict_backward_compat(self) -> None:
        d = {"id": "abc", "startMs": 0, "endMs": 5000}
        seg = VideoSegment.from_dict(d)
        assert seg.speed == 1.0


# ── RecordingSession + VideoSegments ─────────────────────────────────


class TestRecordingSessionVideoSegments:
    def test_json_includes_video_segments(self) -> None:
        seg = VideoSegment.create(start_ms=0, end_ms=5000)
        session = RecordingSession(
            id="vs1", start_time=0, duration=5000,
            mouse_track=[MousePosition(0, 0, 0)],
            keyframes=[],
            video_segments=[seg],
        )
        d = json.loads(session.to_json())
        assert "videoSegments" in d
        assert len(d["videoSegments"]) == 1
        assert d["videoSegments"][0]["startMs"] == 0
        assert d["videoSegments"][0]["endMs"] == 5000

    def test_json_roundtrip_video_segments(self) -> None:
        seg1 = VideoSegment.create(start_ms=0, end_ms=2500)
        seg2 = VideoSegment.create(start_ms=2500, end_ms=5000, speed=2.0)
        session = RecordingSession(
            id="vs2", start_time=0, duration=5000,
            mouse_track=[MousePosition(0, 0, 0)],
            keyframes=[],
            video_segments=[seg1, seg2],
        )
        s2 = RecordingSession.from_json(session.to_json())
        assert s2.video_segments is not None
        assert len(s2.video_segments) == 2
        assert s2.video_segments[0].end_ms == 2500
        assert s2.video_segments[1].start_ms == 2500
        assert s2.video_segments[1].speed == 2.0

    def test_json_omits_video_segments_when_empty(self) -> None:
        session = RecordingSession(
            id="vs3", start_time=0, duration=100,
            mouse_track=[MousePosition(0, 0, 0)],
            keyframes=[],
        )
        d = json.loads(session.to_json())
        assert "videoSegments" not in d

    def test_backward_compat_no_video_segments(self) -> None:
        """Old projects without videoSegments should load fine."""
        session = RecordingSession(
            id="old", start_time=0, duration=100,
            mouse_track=[MousePosition(0, 0, 0)],
            keyframes=[],
        )
        s2 = RecordingSession.from_json(session.to_json())
        assert s2.video_segments is None


# ── Constants ───────────────────────────────────────────────────────


class TestConstants:
    def test_default_fps(self) -> None:
        assert DEFAULT_FPS == 60

    def test_default_mouse_interval(self) -> None:
        assert DEFAULT_MOUSE_INTERVAL == 16
