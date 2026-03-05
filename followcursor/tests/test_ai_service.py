"""Tests for the AI service module (pure-logic layer, no API calls)."""

import json
import pytest

from app.ai_service import (
    AISettings,
    _summarize_activity,
    _parse_zoom_response,
)
from app.models import MousePosition, KeyEvent, ClickEvent


# ── AISettings ──────────────────────────────────────────────────────


class TestAISettings:
    """Test AISettings configuration dataclass."""

    def test_default_not_configured(self):
        s = AISettings()
        assert not s.chat_configured
        assert not s.tts_configured

    def test_chat_configured(self):
        s = AISettings(
            endpoint="https://example.com",
            api_key="key123",
            chat_model="gpt-4o-mini",
        )
        assert s.chat_configured
        assert s.tts_configured  # TTS only needs endpoint + key

    def test_tts_configured(self):
        s = AISettings(
            endpoint="https://example.com",
            api_key="key123",
        )
        assert s.tts_configured

    def test_missing_endpoint_not_configured(self):
        s = AISettings(api_key="key", chat_model="model")
        assert not s.chat_configured

    def test_missing_key_not_configured(self):
        s = AISettings(endpoint="https://x.com", chat_model="model")
        assert not s.chat_configured

    def test_missing_model_not_configured(self):
        s = AISettings(endpoint="https://x.com", api_key="key")
        assert not s.chat_configured

    def test_default_voice(self):
        s = AISettings()
        assert s.tts_voice == "en-US-Ava:DragonHDLatestNeural"


# ── Activity summary ───────────────────────────────────────────────


class TestSummarizeActivity:
    """Test the activity summarization for AI prompts."""

    @pytest.fixture
    def monitor_rect(self):
        return {"left": 0, "top": 0, "width": 1920, "height": 1080}

    def test_basic_summary(self, monitor_rect):
        track = [
            MousePosition(100, 200, 0),
            MousePosition(100, 200, 500),
            MousePosition(100, 200, 1000),
        ]
        result = _summarize_activity(track, None, None, monitor_rect, 1500.0)
        assert "Recording duration: 1.5 seconds" in result
        assert "1920x1080" in result

    def test_includes_keystroke_count(self, monitor_rect):
        track = [MousePosition(100, 200, t) for t in range(0, 2000, 100)]
        keys = [KeyEvent(timestamp=150), KeyEvent(timestamp=250)]
        result = _summarize_activity(track, keys, None, monitor_rect, 2000.0)
        assert "keys=2" in result

    def test_includes_clicks(self, monitor_rect):
        track = [MousePosition(100, 200, t) for t in range(0, 2000, 100)]
        clicks = [ClickEvent(500, 300, 150)]
        result = _summarize_activity(track, None, clicks, monitor_rect, 2000.0)
        assert "clicks=1" in result

    def test_empty_windows_skipped(self, monitor_rect):
        # Only activity at t=0-1s, gap at t=1-2s
        track = [
            MousePosition(100, 200, 0),
            MousePosition(100, 200, 500),
        ]
        result = _summarize_activity(track, None, None, monitor_rect, 3000.0)
        lines = result.strip().split("\n")
        # Should not have an entry for t=2s since there's no data
        assert not any("t=2s" in line for line in lines)

    def test_mouse_speed_classification(self, monitor_rect):
        # Fast mouse movement
        track = [
            MousePosition(0, 0, 0),
            MousePosition(500, 500, 100),
            MousePosition(1000, 1000, 200),
            MousePosition(1500, 1500, 300),
        ]
        result = _summarize_activity(track, None, None, monitor_rect, 1000.0)
        assert "speed=fast" in result

    def test_normalized_positions(self, monitor_rect):
        # Mouse in center of screen
        track = [MousePosition(960, 540, 0), MousePosition(960, 540, 500)]
        result = _summarize_activity(track, None, None, monitor_rect, 1000.0)
        assert "(0.50,0.50)" in result


# ── Zoom response parsing ──────────────────────────────────────────


class TestParseZoomResponse:
    """Test parsing AI JSON responses into ZoomKeyframe objects."""

    def test_valid_response(self):
        response = json.dumps([
            {
                "start_ms": 2000,
                "x": 0.5,
                "y": 0.3,
                "zoom": 1.5,
                "hold_ms": 2000,
                "reason": "Typing in editor",
            }
        ])
        keyframes = _parse_zoom_response(response, 1.5, 10000.0)
        # Should produce zoom-in + zoom-out pair
        assert len(keyframes) == 2
        zoom_in = keyframes[0]
        zoom_out = keyframes[1]
        assert zoom_in.zoom == 1.5
        assert zoom_out.zoom == 1.0

    def test_multiple_sections(self):
        response = json.dumps([
            {"start_ms": 1000, "x": 0.2, "y": 0.3, "zoom": 1.5, "hold_ms": 1500, "reason": "A"},
            {"start_ms": 6000, "x": 0.8, "y": 0.7, "zoom": 2.0, "hold_ms": 2000, "reason": "B"},
        ])
        keyframes = _parse_zoom_response(response, 1.5, 10000.0)
        assert len(keyframes) == 4  # 2 pairs
        assert all(kf.reason.startswith("AI") for kf in keyframes)

    def test_keyframes_sorted_by_timestamp(self):
        response = json.dumps([
            {"start_ms": 5000, "x": 0.5, "y": 0.5, "zoom": 1.5, "hold_ms": 1000, "reason": "Late"},
            {"start_ms": 1000, "x": 0.5, "y": 0.5, "zoom": 1.5, "hold_ms": 1000, "reason": "Early"},
        ])
        keyframes = _parse_zoom_response(response, 1.5, 10000.0)
        for i in range(1, len(keyframes)):
            assert keyframes[i].timestamp >= keyframes[i - 1].timestamp

    def test_zoom_clamped(self):
        response = json.dumps([
            {"start_ms": 1000, "x": 0.5, "y": 0.5, "zoom": 10.0, "hold_ms": 1000, "reason": "Big"},
        ])
        keyframes = _parse_zoom_response(response, 1.5, 10000.0)
        zoom_in = [kf for kf in keyframes if kf.zoom > 1.0][0]
        assert zoom_in.zoom <= 3.0

    def test_zoom_min_clamped(self):
        response = json.dumps([
            {"start_ms": 1000, "x": 0.5, "y": 0.5, "zoom": 0.5, "hold_ms": 1000, "reason": "Tiny"},
        ])
        keyframes = _parse_zoom_response(response, 1.5, 10000.0)
        zoom_in = [kf for kf in keyframes if kf.zoom > 1.0][0]
        assert zoom_in.zoom >= 1.1

    def test_viewport_clamped_to_bounds(self):
        response = json.dumps([
            {"start_ms": 1000, "x": 0.0, "y": 0.0, "zoom": 2.0, "hold_ms": 1000, "reason": "Edge"},
        ])
        keyframes = _parse_zoom_response(response, 1.5, 10000.0)
        zoom_in = [kf for kf in keyframes if kf.zoom > 1.0][0]
        # At zoom=2.0, half viewport = 0.25, so x must be >= 0.25
        assert zoom_in.x >= 0.25
        assert zoom_in.y >= 0.25

    def test_strips_markdown_fences(self):
        response = "```json\n" + json.dumps([
            {"start_ms": 1000, "x": 0.5, "y": 0.5, "zoom": 1.5, "hold_ms": 1000, "reason": "Test"},
        ]) + "\n```"
        keyframes = _parse_zoom_response(response, 1.5, 10000.0)
        assert len(keyframes) == 2

    def test_invalid_json_raises(self):
        with pytest.raises(ValueError, match="invalid JSON"):
            _parse_zoom_response("not json", 1.5, 10000.0)

    def test_non_array_raises(self):
        with pytest.raises(ValueError, match="not a JSON array"):
            _parse_zoom_response('{"key": "value"}', 1.5, 10000.0)

    def test_empty_array(self):
        keyframes = _parse_zoom_response("[]", 1.5, 10000.0)
        assert keyframes == []

    def test_reason_prefixed_with_ai(self):
        response = json.dumps([
            {"start_ms": 1000, "x": 0.5, "y": 0.5, "zoom": 1.5, "hold_ms": 1000, "reason": "Click on button"},
        ])
        keyframes = _parse_zoom_response(response, 1.5, 10000.0)
        assert keyframes[0].reason.startswith("AI:")

    def test_zoom_out_clamped_to_duration(self):
        response = json.dumps([
            {"start_ms": 9000, "x": 0.5, "y": 0.5, "zoom": 1.5, "hold_ms": 5000, "reason": "Late"},
        ])
        keyframes = _parse_zoom_response(response, 1.5, 10000.0)
        zoom_out = [kf for kf in keyframes if kf.zoom <= 1.0][0]
        assert zoom_out.timestamp <= 10000.0

    def test_zoom_in_time_not_negative(self):
        response = json.dumps([
            {"start_ms": 100, "x": 0.5, "y": 0.5, "zoom": 1.5, "hold_ms": 1000, "reason": "Start"},
        ])
        keyframes = _parse_zoom_response(response, 1.5, 10000.0)
        assert all(kf.timestamp >= 0 for kf in keyframes)
