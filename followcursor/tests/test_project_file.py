"""Tests for app.project_file — save / load .fcproj bundles."""

import json
import os
import tempfile
import wave
import zipfile

import pytest

from app.project_file import save_project, load_project, PROJ_EXT, _JSON_NAME, _VIDEO_NAME
from app.models import (
    MousePosition,
    KeyEvent,
    ClickEvent,
    ZoomKeyframe,
    RecordingSession,
    VoiceoverSegment,
)
from app.backgrounds import BackgroundPreset
from app.frames import FramePreset, DEFAULT_FRAME


# ── Helpers ─────────────────────────────────────────────────────────

@pytest.fixture
def tmp_dir(tmp_path):
    return tmp_path


@pytest.fixture
def dummy_video(tmp_path) -> str:
    """Create a small dummy AVI file for testing."""
    path = str(tmp_path / "test_recording.avi")
    with open(path, "wb") as f:
        f.write(b"\x00" * 256)  # minimal placeholder
    return path


@pytest.fixture
def full_session() -> RecordingSession:
    return RecordingSession(
        id="proj-test-001",
        start_time=0.0,
        duration=5000.0,
        mouse_track=[
            MousePosition(x=100, y=200, timestamp=0),
            MousePosition(x=110, y=210, timestamp=16),
            MousePosition(x=120, y=220, timestamp=32),
        ],
        keyframes=[
            ZoomKeyframe.create(timestamp=1000, zoom=1.5, x=0.3, y=0.4, duration=600),
            ZoomKeyframe.create(timestamp=3000, zoom=1.0, x=0.5, y=0.5, duration=1200),
        ],
        key_events=[KeyEvent(timestamp=500)],
        click_events=[ClickEvent(x=105, y=205, timestamp=600)],
        frame_timestamps=[0, 16, 32],
        trim_start_ms=100,
        trim_end_ms=4500,
    )


@pytest.fixture
def sample_bg() -> BackgroundPreset:
    return BackgroundPreset("Test BG", "gradient", (255, 0, 0), (0, 0, 255))


# ── save_project ────────────────────────────────────────────────────


class TestSaveProject:
    def test_creates_zip(self, tmp_dir, dummy_video, full_session) -> None:
        out = save_project(str(tmp_dir / "test"), dummy_video, full_session)
        assert out.endswith(PROJ_EXT)
        assert os.path.isfile(out)
        assert zipfile.is_zipfile(out)

    def test_appends_extension(self, tmp_dir, dummy_video, full_session) -> None:
        out = save_project(str(tmp_dir / "noext"), dummy_video, full_session)
        assert out.endswith(PROJ_EXT)

    def test_does_not_double_extension(self, tmp_dir, dummy_video, full_session) -> None:
        out = save_project(str(tmp_dir / "already.fcproj"), dummy_video, full_session)
        assert out.endswith(PROJ_EXT)
        assert not out.endswith(PROJ_EXT + PROJ_EXT)

    def test_zip_contains_json_and_video(self, tmp_dir, dummy_video, full_session) -> None:
        out = save_project(str(tmp_dir / "test"), dummy_video, full_session)
        with zipfile.ZipFile(out, "r") as zf:
            names = zf.namelist()
            assert _JSON_NAME in names
            assert _VIDEO_NAME in names

    def test_json_content_valid(self, tmp_dir, dummy_video, full_session) -> None:
        out = save_project(str(tmp_dir / "test"), dummy_video, full_session)
        with zipfile.ZipFile(out, "r") as zf:
            data = json.loads(zf.read(_JSON_NAME))
        assert data["id"] == "proj-test-001"
        assert data["duration"] == 5000.0
        assert len(data["mouseTrack"]) == 3
        assert len(data["keyframes"]) == 2
        assert "keyEvents" not in data
        assert "keystrokeConfig" not in data
        assert "annotations" not in data

    def test_includes_monitor_rect(self, tmp_dir, dummy_video, full_session) -> None:
        mon = {"left": 0, "top": 0, "width": 1920, "height": 1080}
        out = save_project(str(tmp_dir / "test"), dummy_video, full_session,
                           monitor_rect=mon)
        with zipfile.ZipFile(out, "r") as zf:
            data = json.loads(zf.read(_JSON_NAME))
        assert data["monitorRect"] == mon

    def test_includes_actual_fps(self, tmp_dir, dummy_video, full_session) -> None:
        out = save_project(str(tmp_dir / "test"), dummy_video, full_session,
                           actual_fps=59.94)
        with zipfile.ZipFile(out, "r") as zf:
            data = json.loads(zf.read(_JSON_NAME))
        assert data["actualFps"] == 59.94

    def test_includes_bg_preset(self, tmp_dir, dummy_video, full_session, sample_bg) -> None:
        out = save_project(str(tmp_dir / "test"), dummy_video, full_session,
                           bg_preset=sample_bg)
        with zipfile.ZipFile(out, "r") as zf:
            data = json.loads(zf.read(_JSON_NAME))
        assert data["bgPreset"]["name"] == "Test BG"

    def test_includes_frame_preset(self, tmp_dir, dummy_video, full_session) -> None:
        out = save_project(str(tmp_dir / "test"), dummy_video, full_session,
                           frame_preset=DEFAULT_FRAME)
        with zipfile.ZipFile(out, "r") as zf:
            data = json.loads(zf.read(_JSON_NAME))
        assert data["framePreset"]["name"] == "Wide Bezel"

    def test_missing_video(self, tmp_dir, full_session) -> None:
        """Should still create the ZIP without the AVI if the video is missing."""
        out = save_project(str(tmp_dir / "test"), "/nonexistent.avi", full_session)
        with zipfile.ZipFile(out, "r") as zf:
            names = zf.namelist()
            assert _JSON_NAME in names
            assert _VIDEO_NAME not in names


# ── load_project ────────────────────────────────────────────────────


class TestLoadProject:
    def test_roundtrip(self, tmp_dir, dummy_video, full_session) -> None:
        out = save_project(str(tmp_dir / "rt"), dummy_video, full_session,
                           monitor_rect={"left": 0, "top": 0, "width": 1920, "height": 1080},
                           actual_fps=60.0)
        result = load_project(out)

        assert result["session"].id == full_session.id
        assert result["session"].duration == full_session.duration
        assert len(result["session"].mouse_track) == len(full_session.mouse_track)
        assert len(result["session"].keyframes) == len(full_session.keyframes)
        assert result["monitor_rect"]["width"] == 1920
        assert result["actual_fps"] == 60.0

    def test_roundtrip_with_presets(self, tmp_dir, dummy_video, full_session, sample_bg) -> None:
        out = save_project(str(tmp_dir / "rt"), dummy_video, full_session,
                           bg_preset=sample_bg, frame_preset=DEFAULT_FRAME)
        result = load_project(out)
        assert result["bg_preset"].name == "Test BG"
        assert result["frame_preset"].name == "Wide Bezel"

    def test_video_extracted(self, tmp_dir, dummy_video, full_session) -> None:
        out = save_project(str(tmp_dir / "rt"), dummy_video, full_session)
        result = load_project(out)
        assert result["video_path"] != ""
        assert os.path.isfile(result["video_path"])

    def test_trim_preserved(self, tmp_dir, dummy_video, full_session) -> None:
        out = save_project(str(tmp_dir / "rt"), dummy_video, full_session)
        result = load_project(out)
        assert result["session"].trim_start_ms == 100
        assert result["session"].trim_end_ms == 4500

    def test_key_events_removed_on_roundtrip(self, tmp_dir, dummy_video, full_session) -> None:
        out = save_project(str(tmp_dir / "rt"), dummy_video, full_session)
        result = load_project(out)
        assert result["session"].key_events is None
        assert result["keystroke_config"] is None
        assert result["annotations"] is None

    def test_click_events_preserved(self, tmp_dir, dummy_video, full_session) -> None:
        out = save_project(str(tmp_dir / "rt"), dummy_video, full_session)
        result = load_project(out)
        assert len(result["session"].click_events) == 1

    def test_legacy_removed_payloads_load_safely(self, tmp_dir) -> None:
        legacy_path = tmp_dir / "legacy.fcproj"
        legacy_data = {
            "id": "legacy-project",
            "startTime": 0.0,
            "duration": 5000.0,
            "mouseTrack": [{"x": 100, "y": 200, "timestamp": 0.0}],
            "keyframes": [],
            "keyEvents": [{"timestamp": 500.0}],
            "clickEvents": [{"x": 105, "y": 205, "timestamp": 600.0}],
            "keystrokeConfig": {"enabled": True},
            "annotations": {"texts": [{}], "arrows": [{}], "highlights": []},
        }
        with zipfile.ZipFile(legacy_path, "w") as zf:
            zf.writestr(_JSON_NAME, json.dumps(legacy_data))

        result = load_project(str(legacy_path))

        assert result["session"].id == "legacy-project"
        assert result["session"].key_events is None
        assert len(result["session"].click_events) == 1
        assert result["keystroke_config"] is None
        assert result["annotations"] is None

    def test_invalid_file_raises(self, tmp_dir) -> None:
        bad = str(tmp_dir / "bad.fcproj")
        with open(bad, "w") as f:
            f.write("not a zip")
        with pytest.raises(ValueError, match="Not a valid"):
            load_project(bad)

    def test_missing_json_raises(self, tmp_dir) -> None:
        """ZIP without project.json should raise ValueError."""
        bad = str(tmp_dir / "nojson.fcproj")
        with zipfile.ZipFile(bad, "w") as zf:
            zf.writestr("random.txt", "hello")
        with pytest.raises(ValueError, match="missing"):
            load_project(bad)

    def test_missing_video_returns_empty_path(self, tmp_dir, full_session) -> None:
        """If the video was not included, video_path should be empty."""
        out = save_project(str(tmp_dir / "novid"), "/nonexistent.avi", full_session)
        result = load_project(out)
        assert result["video_path"] == ""

    def test_missing_bg_preset_returns_none(self, tmp_dir, dummy_video, full_session) -> None:
        """If no bg preset was saved, load should return None."""
        out = save_project(str(tmp_dir / "nobg"), dummy_video, full_session)
        result = load_project(out)
        assert result["bg_preset"] is None

    def test_missing_frame_preset_returns_none(self, tmp_dir, dummy_video, full_session) -> None:
        out = save_project(str(tmp_dir / "nofr"), dummy_video, full_session)
        result = load_project(out)
        assert result["frame_preset"] is None

    def test_generated_narration_roundtrip(self, tmp_dir, dummy_video, full_session) -> None:
        audio_paths = []
        for name in ("generated_01_context.wav", "generated_02_result.wav"):
            audio_path = tmp_dir / name
            with wave.open(str(audio_path), "wb") as wav_file:
                wav_file.setnchannels(1)
                wav_file.setsampwidth(2)
                wav_file.setframerate(16000)
                wav_file.writeframes(b"\x00\x00" * 160)
            audio_paths.append(audio_path)

        seg_one = VoiceoverSegment.create(
            timestamp=0.0,
            text="Context narration",
            voice="nova",
            rate=0.94,
            source="generated",
            script_markdown="## Context\nContext narration",
            script_path="C:/videos/demo_voiceover.md",
        )
        seg_one.audio_path = str(audio_paths[0])
        seg_one.duration_ms = 10.0

        seg_two = VoiceoverSegment.create(
            timestamp=3200.0,
            text="Result narration",
            voice="alloy",
            rate=1.08,
            source="generated",
            script_markdown="## Result\nResult narration",
            script_path="C:/videos/demo_voiceover.md",
        )
        seg_two.audio_path = str(audio_paths[1])
        seg_two.duration_ms = 10.0
        full_session.voiceover_segments = [seg_one, seg_two]

        out = save_project(str(tmp_dir / "generated"), dummy_video, full_session)
        result = load_project(out)
        loaded_segments = result["session"].voiceover_segments
        assert len(loaded_segments) == 2
        assert loaded_segments[0].source == "generated"
        assert loaded_segments[0].script_markdown == "## Context\nContext narration"
        assert loaded_segments[0].script_path == "C:/videos/demo_voiceover.md"
        assert loaded_segments[0].duration_ms == 10.0
        assert loaded_segments[0].rate == 0.94
        assert loaded_segments[0].voice == "nova"
        assert loaded_segments[0].audio_path.endswith(".wav")
        assert os.path.isfile(loaded_segments[0].audio_path)
        assert loaded_segments[1].script_markdown == "## Result\nResult narration"
        assert loaded_segments[1].rate == 1.08
        assert loaded_segments[1].voice == "alloy"
        assert os.path.isfile(loaded_segments[1].audio_path)

    def test_zip_slip_rejected(self, tmp_dir) -> None:
        """A crafted ZIP with path-traversal entries must be rejected."""
        malicious = str(tmp_dir / "evil.fcproj")
        with zipfile.ZipFile(malicious, "w") as zf:
            zf.writestr(_JSON_NAME, '{"id":"x","startTime":0,"duration":1,"mouseTrack":[],"keyframes":[]}')
            # Entry that tries to escape the extraction directory
            zf.writestr("../../escape.txt", "pwned")
        with pytest.raises(ValueError, match="Malicious path"):
            load_project(malicious)


# ── metadata_only save ──────────────────────────────────────────────


class TestMetadataOnlySave:
    def test_metadata_only_preserves_video(self, tmp_dir, dummy_video, full_session) -> None:
        """metadata_only=True should keep the video bytes identical."""
        out = save_project(str(tmp_dir / "meta"), dummy_video, full_session)
        with zipfile.ZipFile(out, "r") as zf:
            original_video = zf.read(_VIDEO_NAME)

        # Modify session metadata and re-save with metadata_only
        full_session.duration = 9999.0
        save_project(out, dummy_video, full_session, metadata_only=True)

        with zipfile.ZipFile(out, "r") as zf:
            assert zf.read(_VIDEO_NAME) == original_video
            data = json.loads(zf.read(_JSON_NAME))
            assert data["duration"] == 9999.0

    def test_metadata_only_updates_json(self, tmp_dir, dummy_video, full_session) -> None:
        """metadata_only save should write updated keyframes."""
        out = save_project(str(tmp_dir / "meta2"), dummy_video, full_session)
        # Add a new keyframe
        full_session.keyframes.append(
            ZoomKeyframe.create(timestamp=2000, zoom=2.0, x=0.6, y=0.7, reason="Pan point")
        )
        save_project(out, dummy_video, full_session, metadata_only=True)
        with zipfile.ZipFile(out, "r") as zf:
            data = json.loads(zf.read(_JSON_NAME))
        assert len(data["keyframes"]) == 3

    def test_metadata_only_on_missing_file_does_full_save(self, tmp_dir, dummy_video, full_session) -> None:
        """If the file doesn't exist yet, metadata_only should do a full save."""
        out_path = str(tmp_dir / "new.fcproj")
        out = save_project(out_path, dummy_video, full_session, metadata_only=True)
        assert os.path.isfile(out)
        with zipfile.ZipFile(out, "r") as zf:
            assert _JSON_NAME in zf.namelist()
            assert _VIDEO_NAME in zf.namelist()
