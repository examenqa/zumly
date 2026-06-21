"""Tests for the AI service module (pure-logic layer, no API calls)."""

import json
import os
import sys
import types
import wave
import pytest

import app.ai_service as ai_service
from app.ai_service import (
    AISettings,
    ExtractedNarrationFrame,
    NarrationMoment,
    _build_narration_frame_plan,
    _clean_narration_spoken_text,
    _markdown_to_tts_text,
    _normalize_generated_narration_voiceover_segments,
    _select_narration_activity_moments,
    _select_narration_annotation_moments,
    _select_narration_zoom_moments,
    _summarize_activity,
    _parse_zoom_response,
    generate_chapters,
    generate_narration,
    ripple_delete_voiceover_segments,
    replace_generated_narration_segment,
    replace_generated_narration_segments,
)
from app.models import (
    AnnotationCollection,
    ArrowAnnotation,
    Chapter,
    ClickEvent,
    HighlightBox,
    KeyEvent,
    MousePosition,
    TextAnnotation,
    VoiceoverSegment,
    ZoomKeyframe,
)


# ── AISettings ──────────────────────────────────────────────────────


class TestAISettings:
    """Test AISettings configuration dataclass."""

    def test_default_not_configured(self):
        s = AISettings()
        assert not s.chat_configured
        assert not s.narration_configured
        assert not s.tts_configured

    def test_chat_configured(self):
        s = AISettings(
            endpoint="https://example.com",
            api_key="key123",
            chat_model="gpt-4o-mini",
        )
        assert s.chat_configured
        assert s.tts_configured  # TTS only needs endpoint + key

    def test_narration_uses_default_runtime_model(self):
        s = AISettings(
            endpoint="https://example.com",
            api_key="key123",
        )
        assert s.narration_configured
        assert s.narration_model == "gpt-5.4"

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


class TestSynthesizeSpeech:
    def test_plain_text_uses_selected_voice_before_synthesizer_creation(self, tmp_path, monkeypatch):
        captured: dict[str, object] = {}

        class FakeSpeechConfig:
            def __init__(self):
                self.speech_synthesis_voice_name = ""

        class FakeAudioOutputConfig:
            def __init__(self, filename: str):
                captured["output_path"] = filename

        class FakeResult:
            reason = "completed"

        class FakeFuture:
            def get(self):
                return FakeResult()

        class FakeSynthesizer:
            def __init__(self, speech_config, audio_config):
                captured["voice_at_construction"] = speech_config.speech_synthesis_voice_name
                self._speech_config = speech_config

            def speak_text_async(self, text: str):
                captured["text"] = text
                captured["voice_at_speak"] = self._speech_config.speech_synthesis_voice_name
                return FakeFuture()

            def speak_ssml_async(self, ssml: str):
                pytest.fail("Plain-text synthesis should not use SSML when rate and volume are default.")

        fake_speechsdk = types.ModuleType("azure.cognitiveservices.speech")
        fake_speechsdk.audio = types.SimpleNamespace(AudioOutputConfig=FakeAudioOutputConfig)
        fake_speechsdk.SpeechSynthesizer = FakeSynthesizer
        fake_speechsdk.ResultReason = types.SimpleNamespace(
            SynthesizingAudioCompleted="completed",
            Canceled="canceled",
        )
        fake_speechsdk.CancellationReason = types.SimpleNamespace(Error="error")

        monkeypatch.setitem(sys.modules, "azure", types.ModuleType("azure"))
        monkeypatch.setitem(
            sys.modules,
            "azure.cognitiveservices",
            types.ModuleType("azure.cognitiveservices"),
        )
        monkeypatch.setitem(
            sys.modules,
            "azure.cognitiveservices.speech",
            fake_speechsdk,
        )
        monkeypatch.setattr(ai_service, "_build_speech_config", lambda api_key, endpoint: FakeSpeechConfig())

        out_path = tmp_path / "plain.wav"
        result = ai_service.synthesize_speech(
            AISettings(
                endpoint="https://example.com",
                api_key="key123",
                tts_voice="en-US-JennyNeural",
            ),
            "Hello team.",
            str(out_path),
            rate=1.0,
            volume=1.0,
        )

        assert result == str(out_path)
        assert captured["voice_at_construction"] == "en-US-JennyNeural"
        assert captured["voice_at_speak"] == "en-US-JennyNeural"
        assert captured["text"] == "Hello team."


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

    def test_ignores_removed_keystrokes(self, monitor_rect):
        track = [MousePosition(100, 200, t) for t in range(0, 2000, 100)]
        keys = [KeyEvent(timestamp=150), KeyEvent(timestamp=250)]
        result = _summarize_activity(track, keys, None, monitor_rect, 2000.0)
        assert "Total: 20 mouse samples, 0 clicks" in result
        assert "keys=" not in result

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


# ── Narration helpers ───────────────────────────────────────────────


class TestNarrationHelpers:
    def test_markdown_to_tts_text_strips_headings(self):
        markdown = """## Context
Open the terminal.

---

## Result
One clean pass."""
        result = _markdown_to_tts_text(markdown)
        assert "##" not in result
        assert "Context" not in result
        assert "Result" not in result
        assert "---" not in result
        assert "Open the terminal." in result
        assert "One clean pass." in result

    def test_clean_narration_spoken_text_strips_section_prefixes(self):
        raw = "Context: Open the terminal, run the install, and land on the outcome."
        result = _clean_narration_spoken_text(raw, section="Context")
        assert result == "Open the terminal, run the install, and land on the outcome."

    def test_style_issue_formatter_flags_literal_click_and_zoom_wording(self):
        segments = [
            ai_service.NarrationScriptSegment(
                section="Context",
                start_ms=0.0,
                narration="We click Save to lock the fix in.",
            ),
            ai_service.NarrationScriptSegment(
                section="Walkthrough",
                start_ms=1000.0,
                narration="We are zooming in on the output before the handoff.",
            ),
        ]

        issues = ai_service._format_narration_style_issues(segments)

        assert "Context: replace literal click wording" in issues
        assert "Walkthrough: replace literal zoom or camera wording" in issues

    def test_select_activity_moments_ignores_keystrokes_but_keeps_clicks(self, monitor_rect):
        track = [MousePosition(960 + (t / 20.0), 540, t) for t in range(0, 7000, 100)]
        keys = [KeyEvent(timestamp=1200 + i * 40) for i in range(5)]
        clicks = [ClickEvent(1200, 640, 5100), ClickEvent(1210, 645, 5300)]

        moments = _select_narration_activity_moments(
            track,
            keys,
            clicks,
            monitor_rect,
            7000.0,
        )

        assert len(moments) == 1
        assert all("typing burst" not in moment.reason for moment in moments)
        assert any("click" in moment.reason for moment in moments)

    def test_frame_plan_merges_activity_into_five_second_samples(self):
        plan = _build_narration_frame_plan(
            12000.0,
            [NarrationMoment(5000.0, "activity cue", "click cluster", 5.0)],
        )

        assert [moment.timestamp_ms for moment in plan] == [0.0, 5000.0, 10000.0, 12000.0]
        assert plan[1].label == "activity cue"
        assert plan[1].reason == "click cluster"

    def test_build_timing_targets_match_section_windows(self):
        targets = ai_service._build_narration_timing_targets(
            60000.0,
            start_ms_by_section={
                "Context": 0.0,
                "Background": 10000.0,
                "Prompt / Action": 20000.0,
                "Walkthrough": 35000.0,
                "Result": 50000.0,
            },
        )

        assert [target.start_ms for target in targets] == [0.0, 10000.0, 20000.0, 35000.0, 50000.0]
        assert [target.end_ms for target in targets] == [10000.0, 20000.0, 35000.0, 50000.0, 60000.0]
        assert sum(target.target_words for target in targets) == 150
        assert targets[2].target_words > targets[0].target_words

    def test_corrected_retry_rate_stays_within_subtle_bounds(self):
        assert ai_service._corrected_narration_retry_rate(
            chosen_rate=1.0,
            measured_duration_ms=5000.0,
            target_duration_ms=10000.0,
            base_rate=1.0,
        ) == pytest.approx(0.88)
        assert ai_service._corrected_narration_retry_rate(
            chosen_rate=1.0,
            measured_duration_ms=10050.0,
            target_duration_ms=10000.0,
            base_rate=1.0,
        ) is None

    def test_select_zoom_moments_from_keyframes(self):
        zoom_in = ZoomKeyframe.create(
            timestamp=12000.0,
            zoom=1.8,
            x=0.62,
            y=0.28,
            reason="Editor zoom on result pane",
        )
        zoom_out = ZoomKeyframe.create(
            timestamp=18000.0,
            zoom=1.0,
            x=0.5,
            y=0.5,
        )

        moments = _select_narration_zoom_moments([zoom_in, zoom_out], 30000.0)

        assert len(moments) == 1
        assert moments[0].label == "zoom cue"
        assert moments[0].timestamp_ms == 12000.0
        assert "editor zoom to 1.8x" in moments[0].reason
        assert "Editor zoom on result pane" in moments[0].reason

    def test_select_annotation_moments_ignore_removed_annotations(self):
        annotations = AnnotationCollection(
            texts=[
                TextAnnotation.create(
                    start_ms=4000.0,
                    end_ms=9000.0,
                    x=0.25,
                    y=0.20,
                    text="Install Dependencies",
                )
            ],
            arrows=[
                ArrowAnnotation.create(
                    start_ms=12000.0,
                    end_ms=15000.0,
                    x1=0.10,
                    y1=0.15,
                    x2=0.60,
                    y2=0.15,
                )
            ],
            highlights=[
                HighlightBox.create(
                    start_ms=21000.0,
                    end_ms=26000.0,
                    x=0.40,
                    y=0.30,
                    width=0.20,
                    height=0.12,
                )
            ],
        )

        moments = _select_narration_annotation_moments(annotations, 30000.0)

        assert moments == []

    def test_replace_generated_narration_keeps_manual_segments(self):
        manual = VoiceoverSegment.create(timestamp=1500, text="Manual note")
        old_generated = VoiceoverSegment.create(
            timestamp=0,
            text="Old generated",
            source="generated",
            script_markdown="## Context\nOld generated",
        )
        new_generated = VoiceoverSegment.create(
            timestamp=0,
            text="New generated",
            source="generated",
            script_markdown="## Context\nNew generated",
        )

        updated = replace_generated_narration_segment(
            [manual, old_generated],
            new_generated,
        )

        assert updated == [new_generated, manual]
        assert all(seg.id != old_generated.id for seg in updated)

    def test_replace_generated_narration_segments_keeps_manual_segments(self):
        manual = VoiceoverSegment.create(timestamp=2500, text="Manual note")
        old_generated = VoiceoverSegment.create(
            timestamp=0,
            text="Old generated",
            source="generated",
            script_markdown="## Context\nOld generated",
        )
        new_generated = [
            VoiceoverSegment.create(
                timestamp=0,
                text="Context beat",
                source="generated",
                script_markdown="## Context\nContext beat",
            ),
            VoiceoverSegment.create(
                timestamp=4000,
                text="Result beat",
                source="generated",
                script_markdown="## Result\nResult beat",
            ),
        ]

        updated = replace_generated_narration_segments(
            [manual, old_generated],
            new_generated,
        )

        assert updated[0] == new_generated[0]
        assert updated[1] == manual
        assert updated[2] == new_generated[1]

    def test_normalize_generated_narration_voiceover_segments_shifts_later_beats(self):
        context = VoiceoverSegment.create(
            timestamp=0,
            text="Context beat",
            source="generated",
            script_markdown="## Context\nContext beat",
        )
        context.duration_ms = 1600.0
        background = VoiceoverSegment.create(
            timestamp=1000,
            text="Background beat",
            source="generated",
            script_markdown="## Background\nBackground beat",
        )
        result = VoiceoverSegment.create(
            timestamp=2500,
            text="Result beat",
            source="generated",
            script_markdown="## Result\nResult beat",
        )
        manual = VoiceoverSegment.create(timestamp=500, text="Manual note")

        updated = _normalize_generated_narration_voiceover_segments(
            [result, manual, background, context],
            video_duration_ms=5000.0,
        )

        generated = [segment for segment in updated if segment.is_generated_narration]
        assert [segment.timestamp for segment in generated] == [0.0, 1600.0, 3100.0]
        assert manual.timestamp == 500

    def test_ripple_delete_voiceovers_trims_generated_segments(self):
        generated = VoiceoverSegment.create(
            timestamp=0,
            text=(
                "Set up the problem clearly. "
                "Show the middle details that get cut. "
                "Land on the outcome after the cut."
            ),
            voice="en-US-JennyNeural",
            source="generated",
            script_markdown=(
                "## Context\n"
                "Set up the problem clearly. "
                "Show the middle details that get cut. "
                "Land on the outcome after the cut."
            ),
        )
        generated.duration_ms = 9000.0
        generated.audio_path = "context.wav"
        later_generated = VoiceoverSegment.create(
            timestamp=12000,
            text="Wrap up the result.",
            voice="en-US-JennyNeural",
            source="generated",
            script_markdown="## Result\nWrap up the result.",
        )
        later_generated.duration_ms = 2500.0
        later_generated.audio_path = "result.wav"
        manual = VoiceoverSegment.create(timestamp=1000, text="Manual note")
        manual.duration_ms = 400.0

        result = ripple_delete_voiceover_segments(
            [manual, generated, later_generated],
            3000.0,
            6000.0,
            video_duration_ms=18000.0,
        )

        assert result.removed_generated_count == 0
        assert result.removed_manual_count == 0
        assert result.regenerated_segment_ids == (generated.id,)
        updated_generated = next(seg for seg in result.segments if seg.id == generated.id)
        assert updated_generated.timestamp == 0.0
        assert updated_generated.audio_path == ""
        assert updated_generated.duration_ms == 0.0
        assert updated_generated.voice == "en-US-JennyNeural"
        assert "Set up the problem clearly." in updated_generated.text
        assert "Land on the outcome after the cut." in updated_generated.text
        assert "Show the middle details that get cut." not in updated_generated.text
        assert updated_generated.script_markdown.startswith("## Context\n")
        shifted_later = next(seg for seg in result.segments if seg.id == later_generated.id)
        assert shifted_later.timestamp == 9000.0
        assert shifted_later.audio_path == "result.wav"
        assert shifted_later.duration_ms == 2500.0

    def test_ripple_delete_voiceovers_removes_manual_and_fully_deleted_generated(self):
        manual = VoiceoverSegment.create(timestamp=3200, text="Manual overlap")
        manual.duration_ms = 800.0
        deleted_generated = VoiceoverSegment.create(
            timestamp=3500,
            text="This beat is entirely deleted.",
            voice="en-US-JennyNeural",
            source="generated",
            script_markdown="## Walkthrough\nThis beat is entirely deleted.",
        )
        deleted_generated.duration_ms = 900.0
        later_generated = VoiceoverSegment.create(
            timestamp=7000,
            text="Keep this result beat.",
            voice="en-US-JennyNeural",
            source="generated",
            script_markdown="## Result\nKeep this result beat.",
        )
        later_generated.duration_ms = 1500.0
        later_generated.audio_path = "result.wav"

        result = ripple_delete_voiceover_segments(
            [manual, deleted_generated, later_generated],
            3000.0,
            6000.0,
            video_duration_ms=12000.0,
        )

        assert result.removed_manual_count == 1
        assert result.removed_generated_count == 1
        assert result.regenerated_segment_ids == ()
        assert [seg.id for seg in result.segments] == [later_generated.id]
        assert result.segments[0].timestamp == 4000.0
        assert result.segments[0].audio_path == "result.wav"

    def test_ripple_delete_voiceovers_infers_unsynthesized_generated_duration(self):
        context = VoiceoverSegment.create(
            timestamp=0,
            text="Open the context. Explain the setup. Close on the takeaway.",
            source="generated",
            script_markdown=(
                "## Context\nOpen the context. Explain the setup. Close on the takeaway."
            ),
        )
        walkthrough = VoiceoverSegment.create(
            timestamp=5000,
            text="Walk through the flow.",
            source="generated",
            script_markdown="## Walkthrough\nWalk through the flow.",
        )

        result = ripple_delete_voiceover_segments(
            [context, walkthrough],
            2000.0,
            3000.0,
            video_duration_ms=10000.0,
        )

        assert result.regenerated_segment_ids == (context.id,)
        updated_context = next(seg for seg in result.segments if seg.id == context.id)
        assert updated_context.timestamp == 0.0
        assert updated_context.audio_path == ""
        shifted_walkthrough = next(seg for seg in result.segments if seg.id == walkthrough.id)
        assert shifted_walkthrough.timestamp == 4000.0

class TestSharedRecordingKnowledge:
    def test_generate_chapters_uses_shared_recording_context(
        self, tmp_path, monitor_rect, monkeypatch
    ):
        ai_service._SHARED_RECORDING_KNOWLEDGE_CACHE.clear()
        video_path = tmp_path / "demo.mp4"
        video_path.write_bytes(b"video")
        prompts: dict[str, object] = {}

        def fake_extract(video, frame_plan, duration_ms, frame_timestamps=None):
            assert video == str(video_path)
            assert duration_ms == 45000.0
            assert frame_timestamps is None
            return [
                ExtractedNarrationFrame(
                    timestamp_ms=2000.0,
                    label="timeline sample",
                    reason="Settings screen",
                    data_url="data:image/jpeg;base64,AAAA",
                ),
                ExtractedNarrationFrame(
                    timestamp_ms=9000.0,
                    label="result cue",
                    reason="Preview result",
                    data_url="data:image/jpeg;base64,BBBB",
                ),
            ]

        def fake_call(settings, system_prompt, user_prompt):
            prompts["system_prompt"] = system_prompt
            prompts["user_prompt"] = user_prompt
            assert settings.chat_model == "gpt-5.4"
            return json.dumps(
                {
                    "chapters": [
                        {"title": "Open project", "timestamp_ms": 0},
                        {"title": "Adjust settings", "timestamp_ms": 4200},
                        {"title": "Preview result", "timestamp_ms": 9800},
                    ]
                }
            )

        monkeypatch.setattr(ai_service, "_extract_narration_frames", fake_extract)
        monkeypatch.setattr(ai_service, "_call_chat", fake_call)

        chapters = generate_chapters(
            AISettings(
                api_key="test-key",
                endpoint="https://example.invalid",
                chat_model="gpt-4o-mini",
            ),
            video_path=str(video_path),
            mouse_track=[
                MousePosition(x=0.1, y=0.2, timestamp=0.0),
                MousePosition(x=0.55, y=0.3, timestamp=4000.0),
                MousePosition(x=0.82, y=0.65, timestamp=9000.0),
            ],
            monitor_rect=monitor_rect,
            duration_ms=45000.0,
            click_events=[ClickEvent(x=0.56, y=0.31, timestamp=4100.0)],
            key_events=[KeyEvent(timestamp=4300.0)],
            zoom_keyframes=[
                ZoomKeyframe(
                    id="zoom-1",
                    timestamp=4000.0,
                    zoom=1.8,
                    x=0.56,
                    y=0.32,
                    duration=600.0,
                )
            ],
            annotations=AnnotationCollection(
                texts=[
                    TextAnnotation.create(
                        start_ms=3900.0,
                        end_ms=7000.0,
                        x=0.55,
                        y=0.25,
                        text="Publish settings",
                    )
                ]
            ),
        )

        assert chapters == [
            Chapter(timestamp_ms=0, name="Open project", auto_detected=True),
            Chapter(timestamp_ms=4200, name="Adjust settings", auto_detected=True),
            Chapter(timestamp_ms=9800, name="Preview result", auto_detected=True),
        ]
        prompt_text = prompts["user_prompt"]
        assert isinstance(prompt_text, list)
        combined_prompt_text = "\n".join(
            item["text"] for item in prompt_text if item.get("type") == "text"
        )
        assert "Narration beat guide" in combined_prompt_text
        assert "Existing zoom sections" in combined_prompt_text
        assert "Preview result" in combined_prompt_text
        assert "Structured annotations" not in combined_prompt_text
        assert "Publish settings" not in combined_prompt_text
        assert "keys=" not in combined_prompt_text

    def test_generate_chapters_and_narration_reuse_shared_batch_analysis(
        self, tmp_path, monitor_rect, monkeypatch
    ):
        ai_service._SHARED_RECORDING_KNOWLEDGE_CACHE.clear()
        video_path = tmp_path / "long-demo.mp4"
        video_path.write_bytes(b"video")
        calls = {"extract": 0, "batch": 0, "chapters": 0, "narration": 0}

        def fake_extract(video, frame_plan, duration_ms, frame_timestamps=None):
            calls["extract"] += 1
            assert video == str(video_path)
            assert duration_ms == 300000.0
            assert frame_timestamps is None
            return [
                ExtractedNarrationFrame(
                    timestamp_ms=moment.timestamp_ms,
                    label=f"frame {index}",
                    reason=f"Frame {index}",
                    data_url=f"data:image/jpeg;base64,{index:04d}",
                )
                for index, moment in enumerate(frame_plan, start=1)
            ]

        def fake_call(settings, system_prompt, user_prompt):
            assert settings.chat_model == "gpt-5.4"
            if isinstance(user_prompt, list):
                calls["batch"] += 1
                return "### Slice summary\n- Setup\n- Action\n- Result"
            if system_prompt == ai_service._CHAPTER_SYSTEM_PROMPT:
                calls["chapters"] += 1
                return json.dumps(
                    {
                        "chapters": [
                            {"title": "Setup", "timestamp_ms": 0},
                            {"title": "Action", "timestamp_ms": 120000},
                            {"title": "Result", "timestamp_ms": 240000},
                        ]
                    }
                )
            calls["narration"] += 1
            return json.dumps(
                {
                    "segments": [
                        {"section": "Context", "start_ms": 0, "narration": "Setup context."},
                        {"section": "Background", "start_ms": 60000, "narration": "Background detail."},
                        {"section": "Prompt / Action", "start_ms": 120000, "narration": "Action beat."},
                        {"section": "Walkthrough", "start_ms": 180000, "narration": "Walkthrough detail."},
                        {"section": "Result", "start_ms": 240000, "narration": "Result summary."},
                    ]
                }
            )

        monkeypatch.setattr(ai_service, "_extract_narration_frames", fake_extract)
        monkeypatch.setattr(ai_service, "_call_chat", fake_call)
        monkeypatch.setattr(ai_service, "synthesize_speech", lambda *args, **kwargs: None)

        settings = AISettings(
            api_key="test-key",
            endpoint="https://example.invalid",
            chat_model="gpt-4o-mini",
        )
        mouse_track = [
            MousePosition(
                x=0.2 + ((ts // 5000) % 10) * 0.05,
                y=0.4,
                timestamp=float(ts),
            )
            for ts in range(0, 300001, 5000)
        ]

        chapters = generate_chapters(
            settings,
            video_path=str(video_path),
            mouse_track=mouse_track,
            monitor_rect=monitor_rect,
            duration_ms=300000.0,
        )
        narration = generate_narration(
            settings,
            video_path=str(video_path),
            mouse_track=mouse_track,
            monitor_rect=monitor_rect,
            duration_ms=300000.0,
            voice="en-US-JennyNeural",
            synthesize_audio=False,
        )

        assert [chapter.name for chapter in chapters] == ["Setup", "Action", "Result"]
        assert [segment.generated_narration_label for segment in narration.voiceover_segments] == [
            "Context",
            "Background",
            "Prompt / Action",
            "Walkthrough",
            "Result",
        ]
        assert calls["extract"] == 1
        assert calls["chapters"] == 1
        assert calls["narration"] >= 1
        assert calls["batch"] > 0


class TestNarrationGuidancePrompt:
    """Tests for optional user guidance injected into the narration system prompt."""

    def test_build_narration_system_prompt_without_guidance(self):
        """Empty guidance returns the unmodified baseline system prompt."""
        result = ai_service._build_narration_system_prompt("")
        assert result == ai_service._NARRATION_SYSTEM_PROMPT

    def test_build_narration_system_prompt_with_whitespace_only(self):
        """Whitespace-only guidance is treated as absent."""
        result = ai_service._build_narration_system_prompt("   \n  ")
        assert result == ai_service._NARRATION_SYSTEM_PROMPT

    def test_build_narration_system_prompt_appends_guidance_block(self):
        """Non-empty guidance is appended after the baseline prompt."""
        guidance = "Focus on end-user benefit and ease-of-use."
        result = ai_service._build_narration_system_prompt(guidance)
        assert result.startswith(ai_service._NARRATION_SYSTEM_PROMPT)
        assert "Creator guidance" in result
        assert guidance in result

    def test_build_narration_system_prompt_strips_guidance_whitespace(self):
        """Leading/trailing whitespace in guidance is stripped."""
        guidance = "  Focus on the payoff.  "
        result = ai_service._build_narration_system_prompt(guidance)
        assert "Focus on the payoff." in result
        assert result.endswith("Focus on the payoff.")

    def test_generate_narration_passes_guidance_to_system_prompt(
        self, tmp_path, monitor_rect, monkeypatch
    ):
        """Guidance text appears in the system prompt forwarded to the AI call."""
        video_path = tmp_path / "guided-demo.mp4"
        video_path.write_bytes(b"video")
        captured: dict[str, list] = {"system_prompts": []}
        guidance_text = "Emphasize how easy this is for non-technical users."

        def fake_extract(video_path_arg, frame_plan, duration_ms, frame_timestamps=None):
            return [
                ExtractedNarrationFrame(
                    timestamp_ms=0.0,
                    label="sample",
                    reason="opener",
                    data_url="data:image/jpeg;base64,AAAA",
                )
            ]

        def fake_call(settings, system_prompt, user_prompt):
            captured["system_prompts"].append(system_prompt)
            return json.dumps({
                "segments": [
                    {"section": "Context", "start_ms": 0, "narration": "This is easy to set up."},
                    {"section": "Background", "start_ms": 6000, "narration": "The background is lightweight."},
                    {"section": "Prompt / Action", "start_ms": 12000, "narration": "One click starts it."},
                    {"section": "Walkthrough", "start_ms": 24000, "narration": "The walkthrough is quick."},
                    {"section": "Result", "start_ms": 38000, "narration": "The result speaks for itself."},
                ]
            })

        monkeypatch.setattr(ai_service, "_extract_narration_frames", fake_extract)
        monkeypatch.setattr(ai_service, "_call_chat", fake_call)

        generate_narration(
            AISettings(
                endpoint="https://example.com",
                api_key="key123",
                chat_model="gpt-4o-mini",
            ),
            str(video_path),
            [MousePosition(960, 540, 0)],
            monitor_rect,
            45000.0,
            synthesize_audio=False,
            guidance_prompt=guidance_text,
        )

        assert captured["system_prompts"], "Expected at least one AI call"
        # Every narration call should carry the guidance
        for prompt in captured["system_prompts"]:
            assert guidance_text in prompt, (
                f"Guidance missing from system prompt: {prompt[:200]}"
            )

    def test_generate_narration_without_guidance_uses_baseline_prompt(
        self, tmp_path, monitor_rect, monkeypatch
    ):
        """When guidance_prompt is None the baseline system prompt is used unchanged."""
        video_path = tmp_path / "no-guidance-demo.mp4"
        video_path.write_bytes(b"video")
        captured: dict[str, list] = {"system_prompts": []}

        def fake_extract(video_path_arg, frame_plan, duration_ms, frame_timestamps=None):
            return [
                ExtractedNarrationFrame(
                    timestamp_ms=0.0,
                    label="sample",
                    reason="opener",
                    data_url="data:image/jpeg;base64,AAAA",
                )
            ]

        def fake_call(settings, system_prompt, user_prompt):
            captured["system_prompts"].append(system_prompt)
            return json.dumps({
                "segments": [
                    {"section": "Context", "start_ms": 0, "narration": "Context beat."},
                    {"section": "Background", "start_ms": 6000, "narration": "Background beat."},
                    {"section": "Prompt / Action", "start_ms": 12000, "narration": "Action beat."},
                    {"section": "Walkthrough", "start_ms": 24000, "narration": "Walkthrough beat."},
                    {"section": "Result", "start_ms": 38000, "narration": "Result beat."},
                ]
            })

        monkeypatch.setattr(ai_service, "_extract_narration_frames", fake_extract)
        monkeypatch.setattr(ai_service, "_call_chat", fake_call)

        generate_narration(
            AISettings(
                endpoint="https://example.com",
                api_key="key123",
                chat_model="gpt-4o-mini",
            ),
            str(video_path),
            [MousePosition(960, 540, 0)],
            monitor_rect,
            45000.0,
            synthesize_audio=False,
            guidance_prompt=None,
        )

        for prompt in captured["system_prompts"]:
            assert prompt == ai_service._NARRATION_SYSTEM_PROMPT, (
                "No-guidance call should use the unmodified baseline prompt"
            )


class TestGenerateNarration:
    def test_generate_narration_polishes_short_draft_to_match_timing(self, tmp_path, monitor_rect, monkeypatch):
        video_path = tmp_path / "timed-demo.mp4"
        video_path.write_bytes(b"video")
        captured: dict[str, object] = {"chat_calls": []}

        def make_words(prefix: str, count: int) -> str:
            return " ".join(f"{prefix}{index}" for index in range(count)) + "."

        initial_starts = [0.0, 8000.0, 18000.0, 32000.0, 50000.0]

        def fake_extract(video_path_arg, frame_plan, duration_ms, frame_timestamps=None):
            return [
                ExtractedNarrationFrame(
                    timestamp_ms=0.0,
                    label="timeline sample",
                    reason="opening frame",
                    data_url="data:image/jpeg;base64,AAAA",
                ),
                ExtractedNarrationFrame(
                    timestamp_ms=30000.0,
                    label="activity cue",
                    reason="midpoint frame",
                    data_url="data:image/jpeg;base64,BBBB",
                ),
            ]

        def fake_call(settings, system_prompt, user_prompt):
            captured["chat_calls"].append(user_prompt)
            if isinstance(user_prompt, list):
                return json.dumps({
                    "segments": [
                        {"section": "Context", "start_ms": initial_starts[0], "narration": "Quick context."},
                        {"section": "Background", "start_ms": initial_starts[1], "narration": "Quick setup."},
                        {"section": "Prompt / Action", "start_ms": initial_starts[2], "narration": "Quick action."},
                        {"section": "Walkthrough", "start_ms": initial_starts[3], "narration": "Quick walkthrough."},
                        {"section": "Result", "start_ms": initial_starts[4], "narration": "Quick result."},
                    ]
                })
            return json.dumps({
                "segments": [
                    {"section": "Context", "start_ms": 9999, "narration": make_words("context", 22)},
                    {"section": "Background", "start_ms": 19999, "narration": make_words("background", 24)},
                    {"section": "Prompt / Action", "start_ms": 29999, "narration": make_words("action", 28)},
                    {"section": "Walkthrough", "start_ms": 39999, "narration": make_words("walkthrough", 34)},
                    {"section": "Result", "start_ms": 59999, "narration": make_words("result", 24)},
                ]
            })

        monkeypatch.setattr(ai_service, "_extract_narration_frames", fake_extract)
        monkeypatch.setattr(ai_service, "_call_chat", fake_call)

        result = generate_narration(
            AISettings(
                endpoint="https://example.com",
                api_key="key123",
                chat_model="gpt-4o-mini",
            ),
            str(video_path),
            [MousePosition(960, 540, 0), MousePosition(980, 560, 30000)],
            monitor_rect,
            60000.0,
            synthesize_audio=False,
        )

        assert len(captured["chat_calls"]) == 2
        assert isinstance(captured["chat_calls"][1], str)
        assert "Current draft pacing:" in captured["chat_calls"][1]
        assert "Quick context." in captured["chat_calls"][1]
        assert [seg.timestamp for seg in result.voiceover_segments] == initial_starts
        assert result.voiceover_segments[0].text.startswith("context0")
        assert ai_service._count_spoken_words(result.tts_text) > 100

    def test_generate_narration_polishes_literal_click_and_zoom_language(self, tmp_path, monitor_rect, monkeypatch):
        video_path = tmp_path / "styled-demo.mp4"
        video_path.write_bytes(b"video")
        captured: dict[str, object] = {"chat_calls": []}

        def fake_extract(video_path_arg, frame_plan, duration_ms, frame_timestamps=None):
            return [
                ExtractedNarrationFrame(
                    timestamp_ms=0.0,
                    label="timeline sample",
                    reason="opening frame",
                    data_url="data:image/jpeg;base64,AAAA",
                ),
                ExtractedNarrationFrame(
                    timestamp_ms=30000.0,
                    label="activity cue",
                    reason="midpoint frame",
                    data_url="data:image/jpeg;base64,BBBB",
                ),
            ]

        def fake_call(settings, system_prompt, user_prompt):
            captured["chat_calls"].append(user_prompt)
            if isinstance(user_prompt, list):
                return json.dumps({
                    "segments": [
                        {"section": "Context", "start_ms": 0.0, "narration": "We click into the setup so the room sees where the fix starts."},
                        {"section": "Background", "start_ms": 8000.0, "narration": "The foundation is already there, so the value comes from what changes next for the team."},
                        {"section": "Prompt / Action", "start_ms": 18000.0, "narration": "The main move commits the change and makes the intent explicit for the next handoff."},
                        {"section": "Walkthrough", "start_ms": 32000.0, "narration": "We are zooming in on the output before the result lands for everyone watching."},
                        {"section": "Result", "start_ms": 50000.0, "narration": "The result closes the loop with a concrete outcome the team can trust."},
                    ]
                })
            return json.dumps({
                "segments": [
                    {"section": "Context", "start_ms": 0.0, "narration": "We open on the setup so the room understands exactly where the fix begins."},
                    {"section": "Background", "start_ms": 8000.0, "narration": "The foundation is already there, so the value comes from what changes next for the team."},
                    {"section": "Prompt / Action", "start_ms": 18000.0, "narration": "The main move commits the change and makes the intent explicit for the next handoff."},
                    {"section": "Walkthrough", "start_ms": 32000.0, "narration": "The walkthrough keeps the emphasis on the output that proves the change is working."},
                    {"section": "Result", "start_ms": 50000.0, "narration": "The result closes the loop with a concrete outcome the team can trust."},
                ]
            })

        monkeypatch.setattr(ai_service, "_extract_narration_frames", fake_extract)
        monkeypatch.setattr(ai_service, "_call_chat", fake_call)

        result = generate_narration(
            AISettings(
                endpoint="https://example.com",
                api_key="key123",
                chat_model="gpt-4o-mini",
            ),
            str(video_path),
            [MousePosition(960, 540, 0), MousePosition(980, 560, 30000)],
            monitor_rect,
            60000.0,
            synthesize_audio=False,
        )

        assert len(captured["chat_calls"]) == 2
        assert isinstance(captured["chat_calls"][1], str)
        assert "Style issues to fix:" in captured["chat_calls"][1]
        assert "literal click wording" in captured["chat_calls"][1]
        assert "literal zoom or camera wording" in captured["chat_calls"][1]
        assert "click into the setup" in captured["chat_calls"][1]
        assert "zooming in on the output" in captured["chat_calls"][1]
        assert "click" not in result.tts_text.lower()
        assert "zooming in on" not in result.tts_text.lower()
        assert "zoom" not in result.voiceover_segments[3].text.lower()

    def test_generate_narration_adjusts_tts_rate_to_match_video_duration(self, tmp_path, monitor_rect, monkeypatch):
        video_path = tmp_path / "paced-demo.mp4"
        video_path.write_bytes(b"video")
        captured: dict[str, object] = {"tts_calls": []}

        def make_words(prefix: str, count: int) -> str:
            return " ".join(f"{prefix}{index}" for index in range(count)) + "."

        starts = [0.0, 10000.0, 20000.0, 35000.0, 50000.0]
        counts = [21, 21, 32, 32, 21]

        def fake_extract(video_path_arg, frame_plan, duration_ms, frame_timestamps=None):
            return [
                ExtractedNarrationFrame(
                    timestamp_ms=0.0,
                    label="timeline sample",
                    reason="opening frame",
                    data_url="data:image/jpeg;base64,AAAA",
                ),
                ExtractedNarrationFrame(
                    timestamp_ms=30000.0,
                    label="activity cue",
                    reason="midpoint frame",
                    data_url="data:image/jpeg;base64,BBBB",
                ),
            ]

        def fake_call(settings, system_prompt, user_prompt):
            return json.dumps({
                "segments": [
                    {"section": "Context", "start_ms": starts[0], "narration": make_words("context", counts[0])},
                    {"section": "Background", "start_ms": starts[1], "narration": make_words("background", counts[1])},
                    {"section": "Prompt / Action", "start_ms": starts[2], "narration": make_words("action", counts[2])},
                    {"section": "Walkthrough", "start_ms": starts[3], "narration": make_words("walkthrough", counts[3])},
                    {"section": "Result", "start_ms": starts[4], "narration": make_words("result", counts[4])},
                ]
            })

        def fake_speech(settings, text, output_path, rate=1.0, volume=1.0):
            word_count = ai_service._count_spoken_words(text)
            duration_ms = (word_count * 500.0) / max(rate, 0.01)
            captured["tts_calls"].append((output_path, rate, duration_ms))
            frame_count = int(round((duration_ms / 1000.0) * 16000))
            with wave.open(output_path, "wb") as wav_file:
                wav_file.setnchannels(1)
                wav_file.setsampwidth(2)
                wav_file.setframerate(16000)
                wav_file.writeframes(b"\x00\x00" * frame_count)
            return output_path

        monkeypatch.setattr(ai_service, "_extract_narration_frames", fake_extract)
        monkeypatch.setattr(ai_service, "_call_chat", fake_call)
        monkeypatch.setattr(ai_service, "synthesize_speech", fake_speech)

        result = generate_narration(
            AISettings(
                endpoint="https://example.com",
                api_key="key123",
                chat_model="gpt-4o-mini",
            ),
            str(video_path),
            [MousePosition(960, 540, 0), MousePosition(980, 560, 30000)],
            monitor_rect,
            60000.0,
            synthesize_audio=True,
        )

        assert len(captured["tts_calls"]) == 10
        assert any(call[1] != 1.0 for call in captured["tts_calls"])
        assert all(0.88 <= seg.rate <= 1.12 for seg in result.voiceover_segments)
        total_end_ms = max(seg.timestamp + seg.duration_ms for seg in result.voiceover_segments)
        assert abs(total_end_ms - 60000.0) <= ai_service._narration_total_audio_tolerance_ms(60000.0)

    def test_generate_narration_retimes_synthesized_segments_to_avoid_overlap(
        self,
        tmp_path,
        monitor_rect,
        monkeypatch,
    ):
        video_path = tmp_path / "overlap-demo.mp4"
        video_path.write_bytes(b"video")
        starts = [0.0, 1000.0, 2000.0, 3000.0, 4000.0]

        def fake_extract(video_path_arg, frame_plan, duration_ms, frame_timestamps=None):
            return [
                ExtractedNarrationFrame(
                    timestamp_ms=0.0,
                    label="timeline sample",
                    reason="opening frame",
                    data_url="data:image/jpeg;base64,AAAA",
                ),
                ExtractedNarrationFrame(
                    timestamp_ms=4500.0,
                    label="activity cue",
                    reason="midpoint frame",
                    data_url="data:image/jpeg;base64,BBBB",
                ),
            ]

        def fake_call(settings, system_prompt, user_prompt):
            return json.dumps({
                "segments": [
                    {"section": "Context", "start_ms": starts[0], "narration": "Context line."},
                    {"section": "Background", "start_ms": starts[1], "narration": "Background line."},
                    {"section": "Prompt / Action", "start_ms": starts[2], "narration": "Action line."},
                    {"section": "Walkthrough", "start_ms": starts[3], "narration": "Walkthrough line."},
                    {"section": "Result", "start_ms": starts[4], "narration": "Result line."},
                ]
            })

        def fake_speech(settings, text, output_path, rate=1.0, volume=1.0):
            duration_ms = 1500.0
            frame_count = int(round((duration_ms / 1000.0) * 16000))
            with wave.open(output_path, "wb") as wav_file:
                wav_file.setnchannels(1)
                wav_file.setsampwidth(2)
                wav_file.setframerate(16000)
                wav_file.writeframes(b"\x00\x00" * frame_count)
            return output_path

        monkeypatch.setattr(ai_service, "_extract_narration_frames", fake_extract)
        monkeypatch.setattr(ai_service, "_call_chat", fake_call)
        monkeypatch.setattr(ai_service, "synthesize_speech", fake_speech)

        result = generate_narration(
            AISettings(
                endpoint="https://example.com",
                api_key="key123",
                chat_model="gpt-4o-mini",
            ),
            str(video_path),
            [MousePosition(960, 540, 0), MousePosition(980, 560, 4500)],
            monitor_rect,
            9000.0,
            synthesize_audio=True,
        )

        assert [segment.timestamp for segment in result.voiceover_segments] == [
            0.0,
            1500.0,
            3000.0,
            4500.0,
            6000.0,
        ]
        assert all(
            later.timestamp >= earlier.timestamp + earlier.duration_ms
            for earlier, later in zip(
                result.voiceover_segments,
                result.voiceover_segments[1:],
            )
        )

    def test_generate_narration_payload_respects_image_cap(self, tmp_path, monitor_rect, monkeypatch):
        video_path = tmp_path / "long-demo.mp4"
        video_path.write_bytes(b"video")
        captured: dict[str, object] = {"chat_calls": []}
        annotations = AnnotationCollection(
            texts=[
                TextAnnotation.create(
                    start_ms=176500.0,
                    end_ms=186000.0,
                    x=0.68,
                    y=0.34,
                    text="Result banner",
                )
            ],
            highlights=[
                HighlightBox.create(
                    start_ms=64000.0,
                    end_ms=72000.0,
                    x=0.52,
                    y=0.26,
                    width=0.18,
                    height=0.12,
                )
            ],
        )

        def fake_extract(video_path_arg, frame_plan, duration_ms, frame_timestamps=None):
            captured["frame_plan"] = frame_plan
            return [
                ExtractedNarrationFrame(
                    timestamp_ms=moment.timestamp_ms,
                    label=moment.label,
                    reason=moment.reason,
                    data_url=f"data:image/jpeg;base64,{idx:04d}",
                )
                for idx, moment in enumerate(frame_plan, start=1)
            ]

        def fake_call(settings, system_prompt, user_prompt):
            assert settings.chat_model == "gpt-5.4"
            captured["chat_calls"].append(user_prompt)
            if isinstance(user_prompt, list):
                batch_images = [item for item in user_prompt if item.get("type") == "image_url"]
                return f"""## Core point
This batch proves the workflow is still advancing.

## Proof
There are {len(batch_images)} supporting frames in this slice.

## Why it matters
The slice moves the presentation toward a stronger result."""
            return json.dumps({
                "segments": [
                    {
                        "section": "Context",
                        "start_ms": 0,
                        "narration": "We frame the long run as a credible end-to-end story, not a passive replay of interface changes."
                    },
                    {
                        "section": "Background",
                        "start_ms": 28000,
                        "narration": "The setup is already established, so the team can focus on why the next beats move the work forward."
                    },
                    {
                        "section": "Prompt / Action",
                        "start_ms": 76000,
                        "narration": "This is where the operator makes the consequential move. The presentation calls out the decision and the intent behind it."
                    },
                    {
                        "section": "Walkthrough",
                        "start_ms": 148000,
                        "narration": "The walkthrough stays centered on momentum, proof, and consequence. The details support the story instead of overwhelming it."
                    },
                    {
                        "section": "Result",
                        "start_ms": 252000,
                        "narration": "We close by landing the payoff cleanly. The outcome is ready for the next handoff and easy to defend in the room."
                    }
                ]
            })

        monkeypatch.setattr(ai_service, "_extract_narration_frames", fake_extract)
        monkeypatch.setattr(ai_service, "_call_chat", fake_call)
        monkeypatch.setattr(ai_service, "_NARRATION_BATCH_PAUSE_SECONDS", 0.0)

        zoom_in = ZoomKeyframe.create(
            timestamp=182500.0,
            zoom=1.7,
            x=0.72,
            y=0.38,
            reason="Zoom in on the final result",
        )
        zoom_out = ZoomKeyframe.create(
            timestamp=188000.0,
            zoom=1.0,
            x=0.5,
            y=0.5,
        )

        result = generate_narration(
            AISettings(
                endpoint="https://example.com",
                api_key="key123",
                chat_model="gpt-4o-mini",
            ),
            str(video_path),
            [MousePosition(960, 540, t) for t in range(0, 300001, 1000)],
            monitor_rect,
            300000.0,
            key_events=(
                [KeyEvent(timestamp=12500.0 + i * 40) for i in range(5)]
                + [KeyEvent(timestamp=177500.0 + i * 40) for i in range(4)]
            ),
            click_events=[
                ClickEvent(1200, 640, 67500.0),
                ClickEvent(1210, 645, 177500.0),
            ],
            zoom_keyframes=[zoom_in, zoom_out],
            annotations=annotations,
            synthesize_audio=False,
        )

        frame_plan = captured["frame_plan"]
        chat_calls = captured["chat_calls"]
        batch_calls = [prompt for prompt in chat_calls if isinstance(prompt, list)]
        final_call = next(prompt for prompt in chat_calls if isinstance(prompt, str))

        assert len(frame_plan) > ai_service._NARRATION_PROVIDER_MAX_IMAGES
        assert len(batch_calls) == 2
        assert all(
            len([item for item in prompt if item.get("type") == "image_url"])
            <= ai_service._NARRATION_PROVIDER_MAX_IMAGES
            for prompt in batch_calls
        )
        assert not any(
            "Result banner" in item.get("text", "")
            for prompt in batch_calls
            for item in prompt
            if item.get("type") == "text"
        )
        assert any(moment.label != "timeline sample" for moment in frame_plan)
        assert frame_plan[0].timestamp_ms == 0.0
        assert frame_plan[-1].timestamp_ms == 300000.0
        assert "editor zoom to 1.7x" in final_call
        assert "Result banner" not in final_call
        assert "keys=" not in final_call
        assert "not to recap each visual change" in final_call
        assert any(moment.label == "zoom cue" for moment in result.activity_moments)
        assert not any(moment.label == "annotation cue" for moment in result.activity_moments)
        assert len(result.voiceover_segments) == 5
        assert [seg.timestamp for seg in result.voiceover_segments] == [0.0, 28000.0, 76000.0, 148000.0, 252000.0]
        assert result.sampled_timestamps_ms == [moment.timestamp_ms for moment in frame_plan]


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
        """Even with a very early start_ms the zoom-in time must clamp to 0."""
        response = json.dumps([
            {"start_ms": 100, "x": 0.5, "y": 0.5, "zoom": 1.5, "hold_ms": 1000, "reason": "Start"},
        ])
        keyframes = _parse_zoom_response(response, 1.5, 10000.0)
        # Must produce a zoom-in and a zoom-out keyframe
        assert len(keyframes) == 2
        zoom_in = keyframes[0]
        zoom_out = keyframes[1]
        # Zoom-in time: max(0, start_ms - transition - anticipation) = max(0, 100-600-100) = 0
        assert zoom_in.timestamp == 0.0
        assert zoom_in.zoom == 1.5
        # Zoom-out should start at start_ms + hold_ms = 1100
        assert zoom_out.timestamp == 1100.0
        assert zoom_out.zoom == 1.0

    def test_excessive_sections_capped(self):
        """AI returning more than 50 sections should be truncated."""
        sections = [
            {"start_ms": i * 500, "x": 0.5, "y": 0.5, "zoom": 1.5,
             "hold_ms": 200, "reason": f"s{i}"}
            for i in range(100)
        ]
        response = json.dumps(sections)
        keyframes = _parse_zoom_response(response, 1.5, 100000.0)
        # Each section produces 2 keyframes (in + out); capped at 50 sections → 100 kfs
        assert len(keyframes) <= 50 * 2
