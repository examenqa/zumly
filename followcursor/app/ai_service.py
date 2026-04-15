"""AI-powered features via Microsoft Foundry (Azure AI).

Provides three AI-enhanced capabilities:

1. **AI Smart Zoom** — Analyzes recording activity (mouse + clicks)
   using a large language model to intelligently determine where and when
   to zoom during playback.  Produces the same ``ZoomKeyframe`` objects as
   the local ``activity_analyzer`` but with AI-driven scene understanding.

2. **Narration + Text-to-Speech Voiceover** — Generates segmented,
   presentation-style narration beats from sampled frames plus timeline
   signals (mouse, clicks, and zooms). The app
   reuses the normal voiceover flow to synthesize and place those
   generated segments, while non-UI callers can still opt into direct
   TTS generation here when needed.

3. **AI Chapters** — Reuses the same shared recording knowledge as
   narration so exported chapter markers stay aligned with the real
   workflow beats.

Users must provide their own Azure AI Foundry API credentials.
"""

import base64
import bisect
import json
import logging
import math
import os
import re
import urllib.error
import urllib.request
from dataclasses import dataclass, replace
from hashlib import sha1
from typing import Any, Callable, List, Optional

from PySide6.QtCore import QThread, Signal

from .models import (
    AnnotationCollection,
    Chapter,
    ClickEvent,
    KeyEvent,
    MousePosition,
    VoiceoverSegment,
    ZoomKeyframe,
)

logger = logging.getLogger(__name__)


# ── Configuration ───────────────────────────────────────────────────


@dataclass
class AISettings:
    """Configuration for Azure AI Foundry API connections.

    A single endpoint and API key are used for chat models and Azure
    Speech Service.  Chat uses the endpoint + deployment name; TTS
    uses the Azure Speech SDK with the same endpoint and key.
    """

    endpoint: str = ""  # Azure AI Foundry / Cognitive Services endpoint URL
    api_key: str = ""  # API key or token
    chat_model: str = ""  # Chat model deployment (e.g. "gpt-4o-mini")
    narration_model: str = "gpt-5.4"  # Narration-specific chat deployment
    tts_voice: str = "en-US-Ava:DragonHDLatestNeural"  # Azure Speech voice name

    @property
    def chat_configured(self) -> bool:
        """True when chat completions can be called."""
        return bool(self.endpoint and self.api_key and self.chat_model)

    @property
    def narration_configured(self) -> bool:
        """True when narration chat completions can be called."""
        return bool(self.endpoint and self.api_key and self.narration_model)

    @property
    def tts_configured(self) -> bool:
        """True when text-to-speech can be called."""
        return bool(self.endpoint and self.api_key)


@dataclass(frozen=True)
class NarrationMoment:
    """A notable recording moment used to steer narration generation."""

    timestamp_ms: float
    label: str
    reason: str
    score: float = 0.0


@dataclass
class ExtractedNarrationFrame:
    """A sampled frame packaged for a multimodal narration prompt."""

    timestamp_ms: float
    label: str
    reason: str
    data_url: str


@dataclass
class GeneratedNarration:
    """Generated narration assets ready for project persistence and TTS."""

    markdown_script: str
    tts_text: str
    script_path: str
    voiceover_segments: List[VoiceoverSegment]
    sampled_timestamps_ms: List[float]
    activity_moments: List[NarrationMoment]

    @property
    def voiceover_segment(self) -> VoiceoverSegment:
        """Backward-compatible access to the first generated narration segment."""
        return self.voiceover_segments[0]


@dataclass(frozen=True)
class RippleDeleteVoiceoverSegmentsResult:
    """Result of ripple-deleting a clip against voiceover segments."""

    segments: List[VoiceoverSegment]
    regenerated_segment_ids: tuple[str, ...]
    removed_generated_count: int
    removed_manual_count: int


@dataclass(frozen=True)
class NarrationScriptSegment:
    """One spoken beat in the generated presentation-style narration."""

    section: str
    start_ms: float
    narration: str


@dataclass(frozen=True)
class NarrationTimingTarget:
    """Timing and pacing guidance for one narration section."""

    section: str
    start_ms: float
    end_ms: float
    target_words: int

    @property
    def duration_ms(self) -> float:
        return max(0.0, self.end_ms - self.start_ms)


@dataclass(frozen=True)
class NarrationBatchNote:
    """A compact batch-level multimodal analysis used for final narration synthesis."""

    batch_index: int
    start_ms: float
    end_ms: float
    frame_count: int
    markdown: str


@dataclass(frozen=True)
class SharedRecordingKnowledge:
    """Shared multimodal evidence that keeps narration and chapters aligned."""

    summary: str
    activity_moments: List[NarrationMoment]
    frames: List[ExtractedNarrationFrame]
    batch_notes: List[NarrationBatchNote]


# ── Prompts ─────────────────────────────────────────────────────────

_ZOOM_SYSTEM_PROMPT = """\
You are a video editing AI that adds cinematic zoom-and-pan effects to screen \
recordings. Your goal is smooth, deliberate camera movements — like a \
professional cameraman gently guiding the viewer's eye to important moments.

You will receive a timestamped activity summary showing mouse position, \
movement speed, and clicks across the recording duration.

**Key principles — smooth and purposeful:**
- FEWER zooms is better. Only zoom on clearly significant activity — \
  deliberate click sequences or important UI interactions
- Each zoom should feel motivated and unhurried. Prefer longer hold times \
  (3-5 seconds) over quick in-and-out zooms
- When activity stays in the same screen area, use ONE sustained zoom \
  rather than multiple separate zooms
- Leave generous breathing room between zoom sections — at least \
  {min_gap_ms}ms gap. The viewer needs time at full view to orient
- Use moderate zoom levels (1.3-1.6). Deep zooms (>2.0) are disorienting \
  unless the activity is very focused in a small area
- All coordinates are normalized (0.0-1.0) relative to the screen
- Do NOT zoom on idle periods, brief mouse movements, or single isolated clicks
- The recording should feel calm and professional, not frenetic

**Panning while zoomed:**
- When activity moves across the screen DURING a zoom section (e.g. the user \
  clicks several buttons in sequence, or the focal area shifts clearly), add \
  pan_points so the camera smoothly follows
- Pan points are intermediate positions the camera visits while staying zoomed
- Only add pan points when the activity genuinely moves — don't pan for \
  small mouse wiggles within the same area
- Each pan point needs a timestamp (ms) when the camera should arrive there

Respond with ONLY a valid JSON array. No markdown, no explanation, no code fences."""

_ZOOM_USER_PROMPT = """\
Analyze this screen recording and generate up to {max_clusters} zoom sections. \
Prefer fewer, longer zooms over many short ones. Use panning when activity \
moves across the screen during a zoom.

{summary}

Return a JSON array where each element has:
- "start_ms": number — when the interesting activity starts (milliseconds)
- "x": number — initial horizontal pan target (0.0 = left, 1.0 = right)
- "y": number — initial vertical pan target (0.0 = top, 1.0 = bottom)
- "zoom": number — zoom level, prefer 1.3-1.6 (max {max_zoom:.1f})
- "hold_ms": number — how long to hold the zoom (3000-5000ms preferred)
- "reason": string — brief description of why this moment is interesting
- "pan_points": array (optional) — if activity moves during the zoom, list \
  intermediate positions: [{{"t_ms": number, "x": number, "y": number}}]
  where t_ms is absolute time in ms. Omit if no panning is needed.

Rules:
- Minimum {min_gap_ms}ms gap between consecutive zoom sections
- Zoom sections MUST NOT overlap — each section must end (start_ms + hold_ms) \
  before the next section's start_ms begins
- Merge nearby activity into one longer zoom rather than multiple short ones
- Use at most {max_clusters} sections total — leave some moments un-zoomed
- pan_points timestamps must be between start_ms and start_ms + hold_ms"""

_NARRATION_SYSTEM_PROMPT = """\
You are a teammate presenting work to peers in a polished demo or pitch.

You will receive:
- a compact activity summary,
- highlighted moments with timestamps,
- and either sampled frames or slice-analysis notes.

Your job is to turn that evidence into spoken narration that sounds like a \
peer presenting what matters and why.

Anti-patterns to avoid:
- Do NOT write closed captions
- Do NOT narrate cursor motion click-by-click
- Do NOT recap the UI line by line
- Do NOT narrate clicks, mouse presses, button mechanics, or camera moves literally
- Do NOT lean on phrases like "you can see", "the cursor moves", "the screen shows", \
  or "then the button appears" unless that detail is genuinely the point
- Do NOT lean on stock presenter filler like "let's start with", "moving on", \
  "at this point", "finally", "what this shows is", or "it's important to note"
- Do NOT say "zooming in on", "the user clicks", "we click", "click here", \
  or similar literal click/zoom mechanics
- Do NOT mention zooms unless they reinforce why the focus changes
- Let clicks and zooms guide emphasis, but turn them into the action, intent, insight, or payoff
- Do NOT include section labels, markdown headings, divider lines, or meta commentary \
  inside the narration text itself

Style requirements:
- Sound confident, persuasive, and presentation-ready
- Sound like someone who actually did the work and is walking peers through the key moves
- Use direct, natural spoken language instead of generic transitions or empty hype
- Explain why each beat matters, what decision lands, and what it unlocks next
- Use visible labels, commands, tools, and values only as supporting evidence
- Assume the audience is technically literate; do not explain basics unless the evidence says it matters
- Keep the writing natural for speech synthesis
- Treat the pacing plan as a real timing constraint: short sections can stay crisp, but longer sections need enough substance to fill the allotted time without filler

Respond with ONLY valid JSON in this shape:
{
  "segments": [
    {
      "section": "Context",
      "start_ms": 0,
      "narration": "..."
    },
    {
      "section": "Background",
      "start_ms": 12000,
      "narration": "..."
    },
    {
      "section": "Prompt / Action",
      "start_ms": 32000,
      "narration": "..."
    },
    {
      "section": "Walkthrough",
      "start_ms": 68000,
      "narration": "..."
    },
    {
      "section": "Result",
      "start_ms": 140000,
      "narration": "..."
    }
  ]
}

Rules:
- Return EXACTLY 5 segments, one per required section, in this order:
  Context, Background, Prompt / Action, Walkthrough, Result
- start_ms values must be non-decreasing and within the recording duration
- Keep each narration close to the requested timing target; thin, trailer-short sections are a failure
- Each narration value should use however many concise sentences the timing plan needs, usually 1-4
- Each narration value must be plain spoken copy only — no section labels, divider lines, bullet markers, or meta framing
- No markdown, no bullet lists, no code fences, no extra keys"""

_NARRATION_USER_PROMPT = """\
Build a segmented presentation-style narration for this recording.

Recording facts:
- Duration: {duration:.1f} seconds
- Target pacing: about {target_words} words total (~{words_per_second:.1f} words/second)
- Required narrative arc: Context → Background → Prompt / Action → Walkthrough → Result
- Output shape: exactly 5 spoken segments, one for each section in order

Section timing plan:
{section_plan}

Activity summary:
{summary}

Important moments:
{moments}

Use the frames as supporting evidence, not as a checklist. Explain why the \
moments matter to peers, what the work unlocks, and how the story moves \
forward. Avoid sounding like a screen reader or recap track. Favor direct, \
authentic spoken copy over generic transitions, obvious signposting, or empty \
hype. Let clicks and zooms influence emphasis, but narrate the action, intent, \
or payoff instead of the mechanics. Keep concrete tools, commands, values, and \
turning points when they matter. Use meaningful spoken content to fill the timing \
plan instead of leaving large dead-air gaps."""

_NARRATION_BATCH_SYSTEM_PROMPT = """\
You are analyzing one chronological slice of a screen-recording walkthrough to \
help a presenter pitch the work later.

Respond in markdown with EXACTLY these section headings and this order:

## Core point
## Proof
## Why it matters

Requirements:
- Keep the notes concise and factual
- Capture the main point of the slice, not a caption-by-caption replay
- Mention tools, commands, labels, or values only when the frames clearly support them
- Explain why the slice matters to the broader story
- Avoid narrating cursor motion or listing every click
- Timestamps are allowed in these internal notes
- No code fences"""

_NARRATION_BATCH_USER_PROMPT = """\
Analyze slice {batch_index} of {total_batches} for this recording.

Slice facts:
- Slice span: {start_label} → {end_label}
- Frames in this slice: {frame_count}

Local highlighted moments:
{moments}

Use the frames to capture the visible workflow, concrete details, and the main \
beats this slice contributes to the overall walkthrough."""

_NARRATION_SYNTHESIS_USER_PROMPT = """\
Build a segmented presentation-style narration for this recording from the \
slice analyses below.

Recording facts:
- Duration: {duration:.1f} seconds
- Target pacing: about {target_words} words total (~{words_per_second:.1f} words/second)
- Required narrative arc: Context → Background → Prompt / Action → Walkthrough → Result
- Output shape: exactly 5 spoken segments, one for each required section in order

Section timing plan:
{section_plan}

Activity summary:
{summary}

Important moments:
{moments}

Slice analyses:
{slice_notes}

Use the slice analyses to explain why the work matters, not to recap each \
visual change. Preserve chronology, but do not mention slice numbers or \
timestamps in the narration. Favor direct, authentic spoken copy over generic \
transitions, obvious signposting, or empty hype. Let clicks and zooms influence \
what gets emphasized, but do not narrate those mechanics literally. Use meaningful \
substance to fill the timing plan without obvious filler."""

_NARRATION_TIMING_POLISH_USER_PROMPT = """\
Tighten this segmented presentation-style narration so the spoken audio tracks \
the recording length more closely.

Recording facts:
- Duration: {duration:.1f} seconds
- Target pacing: about {target_words} words total (~{words_per_second:.1f} words/second)
- Preserve the same five sections and section start times
- Prefer meaningful expansion or compression over filler, repeated phrasing, or silence padding

Section timing plan:
{section_plan}

Activity summary:
{summary}

Important moments:
{moments}

Current draft pacing:
{current_pacing}

Style issues to fix:
{style_issues}

Current draft JSON:
{draft}

Rewrite the narration so each section lands close to its target span when spoken aloud. \
If a section is too short, add concrete why-it-matters detail or outcome. If a section \
is too long, compress repetition. Keep the pitch/presentation tone, keep the \
language natural and direct, avoid AI-sounding signposting, and return ONLY the \
JSON segment structure."""

_CHAPTER_SYSTEM_PROMPT = """\
You are generating chapter markers for a screen-recording walkthrough.

Use the same evidence that powers narration so the chapter markers line up with \
the real beats of the recording instead of guessing from idle gaps alone.

Requirements:
- Focus on major transitions in the story, workflow, or result
- Keep titles short, specific, and navigation-friendly
- Do NOT copy narration sentences or write full spoken prose
- Do NOT narrate cursor motion, clicks, or zooms literally
- Let clicks and zooms influence emphasis behind the scenes
- Prefer outcome- or action-oriented labels over vague names like "Chapter 2"

Respond with ONLY valid JSON in this shape:
{
  "chapters": [
    {"start_ms": 0, "title": "Set up the workflow"},
    {"start_ms": 18000, "title": "Apply the main change"},
    {"start_ms": 54000, "title": "Review the result"}
  ]
}

Rules:
- Return between the requested chapter bounds
- start_ms values must be non-decreasing and within the recording duration
- The first chapter should start at or very near the beginning
- Titles should usually be 2-6 words and readable on a timeline or in export metadata
- No markdown, no bullet lists, no commentary, no extra keys"""

_CHAPTER_USER_PROMPT = """\
Build AI chapter markers for this recording.

Recording facts:
- Duration: {duration:.1f} seconds
- Chapter count target: {min_chapters}-{max_chapters}
- Use the same soft beat guide as narration so both features stay aligned

Narration beat guide:
{section_plan}

Activity summary:
{summary}

Important moments:
{moments}

Use the frames as evidence for the main shifts in the workflow, not as a screenshot checklist. \
Chapter titles should help viewers jump to the important beats quickly."""

_CHAPTER_SYNTHESIS_USER_PROMPT = """\
Build AI chapter markers for this recording from the shared slice analyses below.

Recording facts:
- Duration: {duration:.1f} seconds
- Chapter count target: {min_chapters}-{max_chapters}
- Use the same soft beat guide as narration so both features stay aligned

Narration beat guide:
{section_plan}

Activity summary:
{summary}

Important moments:
{moments}

Shared slice analyses:
{slice_notes}

Use the slice analyses to find the real transitions in the walkthrough. Titles should be \
short navigation labels, not narration copy."""

_NARRATION_WORDS_PER_SECOND = 2.5
_BASE_NARRATION_FRAME_INTERVAL_MS = 5000.0
_NARRATION_ACTIVITY_WINDOW_MS = 1000.0
_NARRATION_ACTIVITY_GAP_MS = 1500.0
_NARRATION_PROVIDER_MAX_IMAGES = 50
_NARRATION_BATCH_IMAGE_BUDGET = 40
_NARRATION_BATCH_PAUSE_SECONDS = 1.0
_NARRATION_RUNTIME_MODEL = "gpt-5.4"
_NARRATION_TIMING_WORD_TOLERANCE_RATIO = 0.18
_NARRATION_TIMING_SECTION_WORD_TOLERANCE_RATIO = 0.35
_NARRATION_TIMING_MIN_SECTION_WORDS = 4
_NARRATION_TTS_RATE_NUDGE_RATIO = 0.12
_NARRATION_TTS_RATE_RETRY_THRESHOLD = 0.03
_NARRATION_TTS_SEGMENT_TOLERANCE_RATIO = 0.08
_NARRATION_TTS_SEGMENT_TOLERANCE_MS = 700.0
_NARRATION_TTS_TOTAL_TOLERANCE_RATIO = 0.015
_NARRATION_TTS_TOTAL_TOLERANCE_MS = 1500.0
_NARRATION_SECTION_ORDER = [
    "Context",
    "Background",
    "Prompt / Action",
    "Walkthrough",
    "Result",
]
_SHARED_RECORDING_KNOWLEDGE_CACHE_SIZE = 4
_SHARED_RECORDING_KNOWLEDGE_CACHE: dict[str, SharedRecordingKnowledge] = {}


# ── Narration helpers ───────────────────────────────────────────────


def _format_time_label(timestamp_ms: float) -> str:
    """Render a millisecond timestamp as a compact mm:ss / hh:mm:ss label."""
    total_seconds = max(0, int(round(timestamp_ms / 1000.0)))
    minutes, seconds = divmod(total_seconds, 60)
    hours, minutes = divmod(minutes, 60)
    if hours > 0:
        return f"{hours:02d}:{minutes:02d}:{seconds:02d}"
    return f"{minutes:02d}:{seconds:02d}"


def _clean_markdown_response(text: str) -> str:
    """Normalize an LLM markdown response before saving it."""
    cleaned = text.strip()
    if cleaned.startswith("```"):
        cleaned = cleaned.split("\n", 1)[1] if "\n" in cleaned else cleaned[3:]
    if cleaned.endswith("```"):
        cleaned = cleaned[:-3]
    return cleaned.strip()


def _clean_json_response(text: str) -> str:
    """Normalize an LLM JSON response before parsing."""
    cleaned = _clean_markdown_response(text)
    if cleaned.lower().startswith("json\n"):
        cleaned = cleaned[5:]
    return cleaned.strip()


def _clone_ai_settings_with_chat_model(settings: AISettings, chat_model: str) -> AISettings:
    """Copy AI settings while overriding the chat model deployment name."""
    return AISettings(
        endpoint=settings.endpoint,
        api_key=settings.api_key,
        chat_model=chat_model,
        narration_model=settings.narration_model,
        tts_voice=settings.tts_voice,
    )


def _narration_chat_settings(settings: AISettings) -> AISettings:
    """Return runtime settings for narration calls."""
    model_name = settings.narration_model or _NARRATION_RUNTIME_MODEL
    return _clone_ai_settings_with_chat_model(settings, model_name)


def _call_narration_chat(
    settings: AISettings,
    system_prompt: str,
    user_prompt: str | List[dict[str, Any]],
) -> str:
    """Call the narration model with the narration-specific runtime deployment."""
    narration_settings = _narration_chat_settings(settings)
    return _call_chat(narration_settings, system_prompt, user_prompt)


def _build_narration_system_prompt(guidance: str = "") -> str:
    """Return the narration system prompt, appending user guidance when provided.

    The guidance block shapes emphasis and focus for the current generation run
    without replacing the baseline tone and anti-pattern rules.
    """
    if not guidance or not guidance.strip():
        return _NARRATION_SYSTEM_PROMPT
    return (
        _NARRATION_SYSTEM_PROMPT
        + "\n\nCreator guidance — use this to shape emphasis, focus, and framing:\n"
        + guidance.strip()
    )


def _shared_recording_knowledge_cache_key(
    *,
    settings: AISettings,
    video_path: str,
    duration_ms: float,
    monitor_rect: dict,
    summary: str,
    moments: List[NarrationMoment],
    frame_plan: List[NarrationMoment],
) -> str:
    """Return a stable cache key for shared narration/chapter analysis."""
    video_stats: dict[str, float | int | str] = {"path": video_path}
    if video_path and os.path.isfile(video_path):
        stat_result = os.stat(video_path)
        video_stats["size"] = stat_result.st_size
        video_stats["mtime_ns"] = stat_result.st_mtime_ns
    payload = {
        "version": 4,
        "narration_model": settings.narration_model or _NARRATION_RUNTIME_MODEL,
        "duration_ms": round(duration_ms, 3),
        "monitor_rect": monitor_rect,
        "summary": summary,
        "moments": [
            {
                "timestamp_ms": round(moment.timestamp_ms, 3),
                "label": moment.label,
                "reason": moment.reason,
                "score": round(moment.score, 4),
            }
            for moment in moments
        ],
        "frame_plan": [
            {
                "timestamp_ms": round(moment.timestamp_ms, 3),
                "label": moment.label,
                "reason": moment.reason,
            }
            for moment in frame_plan
        ],
        "video": video_stats,
    }
    payload_json = json.dumps(payload, ensure_ascii=False, sort_keys=True)
    return sha1(payload_json.encode("utf-8")).hexdigest()


def _get_cached_shared_recording_knowledge(cache_key: str) -> Optional[SharedRecordingKnowledge]:
    """Return cached recording knowledge and refresh its LRU position."""
    knowledge = _SHARED_RECORDING_KNOWLEDGE_CACHE.pop(cache_key, None)
    if knowledge is not None:
        _SHARED_RECORDING_KNOWLEDGE_CACHE[cache_key] = knowledge
    return knowledge


def _store_cached_shared_recording_knowledge(
    cache_key: str,
    knowledge: SharedRecordingKnowledge,
) -> SharedRecordingKnowledge:
    """Store recording knowledge with a tiny LRU cache."""
    _SHARED_RECORDING_KNOWLEDGE_CACHE[cache_key] = knowledge
    while len(_SHARED_RECORDING_KNOWLEDGE_CACHE) > _SHARED_RECORDING_KNOWLEDGE_CACHE_SIZE:
        oldest_key = next(iter(_SHARED_RECORDING_KNOWLEDGE_CACHE))
        _SHARED_RECORDING_KNOWLEDGE_CACHE.pop(oldest_key, None)
    return knowledge


def _slugify_narration_label(label: str) -> str:
    """Turn a narration section label into a filesystem-friendly slug."""
    slug = re.sub(r"[^a-z0-9]+", "-", label.lower()).strip("-")
    return slug or "segment"


def _markdown_to_tts_text(markdown_text: str) -> str:
    """Convert markdown narration into plain text suitable for speech synthesis."""
    paragraphs: list[str] = []
    current: list[str] = []
    in_code_block = False
    section_labels = {label.lower() for label in _NARRATION_SECTION_ORDER} | {"action"}

    for raw_line in markdown_text.splitlines():
        line = raw_line.strip()
        if line.startswith("```"):
            in_code_block = not in_code_block
            continue
        if in_code_block:
            continue
        if not line:
            if current:
                paragraphs.append(" ".join(current).strip())
                current = []
            continue
        if line.startswith("#"):
            continue
        if re.fullmatch(r"[-_*]{3,}", line):
            continue
        for prefix in ("- ", "* ", "+ "):
            if line.startswith(prefix):
                line = line[len(prefix):]
                break
        for token in ("**", "__", "`", "~~", "*", "_"):
            line = line.replace(token, "")
        if not current and line.rstrip(":").strip().lower() in section_labels:
            continue
        if line:
            current.append(line)

    if current:
        paragraphs.append(" ".join(current).strip())

    return "\n\n".join(p for p in paragraphs if p).strip()


def _clean_narration_spoken_text(text: str, section: Optional[str] = None) -> str:
    """Normalize narration content down to plain spoken copy."""
    cleaned = _markdown_to_tts_text(text)
    if not cleaned:
        return ""

    label_options = {item for item in _NARRATION_SECTION_ORDER}
    if section:
        label_options.add(_normalize_narration_section(section))
    label_options.update({"Action"})
    label_pattern = "|".join(
        sorted((re.escape(label) for label in label_options), key=len, reverse=True)
    )

    lines: list[str] = []
    for raw_line in cleaned.splitlines():
        line = raw_line.strip()
        if not line or re.fullmatch(r"[-_*]{3,}", line):
            continue
        if not lines:
            line = re.sub(
                rf"^(?:{label_pattern})\s*[:\-–—|]\s*",
                "",
                line,
                count=1,
                flags=re.IGNORECASE,
            ).strip()
            if not line or line.lower() in {label.lower() for label in label_options}:
                continue
        lines.append(line)

    return "\n\n".join(lines).strip()


def _select_narration_activity_moments(
    mouse_track: List[MousePosition],
    key_events: Optional[List[KeyEvent]],
    click_events: Optional[List[ClickEvent]],
    monitor_rect: dict,
    duration_ms: float,
    window_ms: float = _NARRATION_ACTIVITY_WINDOW_MS,
) -> List[NarrationMoment]:
    """Pick the strongest activity windows to emphasize in narration.

    ``key_events`` is retained for backward-compatible call sites but ignored
    now that keystroke capture has been removed.
    """
    mon_left = monitor_rect.get("left", 0)
    mon_top = monitor_rect.get("top", 0)
    mon_w = max(monitor_rect.get("width", 1), 1)
    mon_h = max(monitor_rect.get("height", 1), 1)

    clicks = click_events or []
    max_moments = max(4, min(10, int(math.ceil(max(duration_ms, 1.0) / 15000.0))))
    windows: list[NarrationMoment] = []
    n_windows = max(1, int(math.ceil(max(duration_ms, 1.0) / window_ms)))

    for wi in range(n_windows):
        t_start = wi * window_ms
        t_end = min(duration_ms, t_start + window_ms)
        window_mouse = [m for m in mouse_track if t_start <= m.timestamp < t_end]
        window_clicks = [c for c in clicks if t_start <= c.timestamp < t_end]

        total_dist = 0.0
        if len(window_mouse) > 1:
            total_dist = sum(
                math.sqrt(
                    (window_mouse[i].x - window_mouse[i - 1].x) ** 2
                    + (window_mouse[i].y - window_mouse[i - 1].y) ** 2
                )
                for i in range(1, len(window_mouse))
            )

        score = 0.0
        reasons: list[str] = []
        label = "timeline sample"

        if window_clicks:
            score += min(8.0, len(window_clicks) * 2.0)
            reasons.append(
                f"{len(window_clicks)} click"
                f"{'s' if len(window_clicks) != 1 else ''}"
            )
            label = "activity cue"
        if total_dist >= 350.0:
            score += min(4.0, total_dist / 250.0)
            reasons.append("fast cursor movement")
            if label == "timeline sample":
                label = "movement cue"
        elif total_dist >= 140.0 and not reasons:
            score += min(2.0, total_dist / 180.0)
            reasons.append("deliberate cursor movement")
            label = "movement cue"

        if not reasons:
            continue

        if window_mouse:
            avg_x = sum(m.x for m in window_mouse) / len(window_mouse)
            avg_y = sum(m.y for m in window_mouse) / len(window_mouse)
        elif window_clicks:
            avg_x = sum(c.x for c in window_clicks) / len(window_clicks)
            avg_y = sum(c.y for c in window_clicks) / len(window_clicks)
        else:
            avg_x = mon_left + (mon_w * 0.5)
            avg_y = mon_top + (mon_h * 0.5)

        nx = max(0.0, min(1.0, (avg_x - mon_left) / mon_w))
        ny = max(0.0, min(1.0, (avg_y - mon_top) / mon_h))
        reasons.append(f"focus near ({nx:.2f}, {ny:.2f})")

        windows.append(
            NarrationMoment(
                timestamp_ms=t_start,
                label=label,
                reason=", ".join(reasons),
                score=score,
            )
        )

    windows.sort(key=lambda moment: (-moment.score, moment.timestamp_ms))
    selected: list[NarrationMoment] = []
    for moment in windows:
        if any(abs(moment.timestamp_ms - existing.timestamp_ms) < window_ms for existing in selected):
            continue
        selected.append(moment)
        if len(selected) >= max_moments:
            break

    selected.sort(key=lambda moment: moment.timestamp_ms)
    return selected

def _select_narration_zoom_moments(
    zoom_keyframes: Optional[List[ZoomKeyframe]],
    duration_ms: float,
    min_gap_ms: float = _NARRATION_ACTIVITY_GAP_MS,
) -> List[NarrationMoment]:
    """Turn editorial zoom keyframes into narration cues."""
    keyframes = sorted(zoom_keyframes or [], key=lambda keyframe: keyframe.timestamp)
    zoom_ins = [
        (idx, keyframe)
        for idx, keyframe in enumerate(keyframes)
        if keyframe.zoom > 1.01
    ]
    if not zoom_ins:
        return []

    max_moments = max(4, min(12, int(math.ceil(max(duration_ms, 1.0) / 20000.0))))
    candidates: list[NarrationMoment] = []

    for idx, keyframe in zoom_ins:
        zoom_out = next(
            (
                candidate for candidate in keyframes[idx + 1:]
                if candidate.zoom <= 1.01
            ),
            None,
        )
        end_ms = zoom_out.timestamp if zoom_out else duration_ms
        end_label = _format_time_label(end_ms)
        reason_parts = [
            f"editor zoom to {keyframe.zoom:.1f}x through {end_label}",
            f"focus near ({keyframe.x:.2f}, {keyframe.y:.2f})",
        ]
        if keyframe.reason:
            reason_parts.append(keyframe.reason.strip())
        candidates.append(
            NarrationMoment(
                timestamp_ms=keyframe.timestamp,
                label="zoom cue",
                reason=", ".join(reason_parts),
                score=max(1.0, float(keyframe.zoom) + 1.5),
            )
        )

    candidates.sort(key=lambda moment: (-moment.score, moment.timestamp_ms))
    selected: list[NarrationMoment] = []
    for moment in candidates:
        if any(abs(moment.timestamp_ms - existing.timestamp_ms) < min_gap_ms for existing in selected):
            continue
        selected.append(moment)
        if len(selected) >= max_moments:
            break

    selected.sort(key=lambda moment: moment.timestamp_ms)
    return selected


def _select_narration_annotation_moments(
    annotations: Optional[AnnotationCollection],
    duration_ms: float,
    min_gap_ms: float = _NARRATION_ACTIVITY_GAP_MS,
) -> List[NarrationMoment]:
    """Return no annotation cues now that annotations are a removed feature."""
    _ = (annotations, duration_ms, min_gap_ms)
    return []

def _summarize_zoom_context(zoom_keyframes: Optional[List[ZoomKeyframe]]) -> str:
    """Summarize the authoritative zoom plan for narration prompts."""
    moments = _select_narration_zoom_moments(zoom_keyframes, duration_ms=1.0e9)
    if not moments:
        return "- No existing zoom sections."
    lines = [
        f"- {_format_time_label(moment.timestamp_ms)} — {moment.reason}"
        for moment in moments[:12]
    ]
    if len(moments) > 12:
        lines.append(f"- {len(moments) - 12} more zoom cue(s) omitted for brevity.")
    return "\n".join(lines)


def _format_annotation_context(
    annotations: Optional[AnnotationCollection],
    start_ms: Optional[float] = None,
    end_ms: Optional[float] = None,
    max_items: int = 12,
) -> str:
    """Return empty compatibility text for removed annotations."""
    _ = (annotations, start_ms, end_ms, max_items)
    return ""

def _build_narration_context_summary(
    mouse_track: List[MousePosition],
    key_events: Optional[List[KeyEvent]],
    click_events: Optional[List[ClickEvent]],
    monitor_rect: dict,
    duration_ms: float,
    zoom_keyframes: Optional[List[ZoomKeyframe]] = None,
    annotations: Optional[AnnotationCollection] = None,
) -> str:
    """Build the full structured narration context summary."""
    activity_summary = _summarize_activity(
        mouse_track,
        key_events,
        click_events,
        monitor_rect,
        duration_ms,
    )
    return "\n\n".join(
        [
            activity_summary,
            "Existing zoom sections:",
            _summarize_zoom_context(zoom_keyframes),
        ]
    )

def _merge_narration_moments(*moment_lists: List[NarrationMoment]) -> List[NarrationMoment]:
    """Merge multiple cue sources into one chronological narration moment list."""
    flattened: list[NarrationMoment] = []
    for moments in moment_lists:
        flattened.extend(moments)

    merged: list[NarrationMoment] = []
    for moment in sorted(flattened, key=lambda value: float(value.timestamp_ms)):
        timestamp_key = int(round(float(moment.timestamp_ms)))
        if merged and int(round(float(merged[-1].timestamp_ms))) == timestamp_key:
            previous = merged[-1]
            previous_priority = (_narration_item_priority(previous.label), float(previous.score))
            current_priority = (_narration_item_priority(moment.label), float(moment.score))
            dominant = moment if current_priority > previous_priority else previous
            reasons = [previous.reason]
            if moment.reason and moment.reason not in reasons:
                reasons.append(moment.reason)
            merged[-1] = NarrationMoment(
                timestamp_ms=previous.timestamp_ms,
                label=dominant.label,
                reason=", ".join(reason for reason in reasons if reason),
                score=max(float(previous.score), float(moment.score)),
            )
            continue
        merged.append(moment)
    return merged


def _build_narration_frame_plan(
    duration_ms: float,
    activity_moments: List[NarrationMoment],
    base_interval_ms: float = _BASE_NARRATION_FRAME_INTERVAL_MS,
    min_gap_ms: float = _NARRATION_ACTIVITY_GAP_MS,
) -> List[NarrationMoment]:
    """Combine 5-second samples with extra narration cues."""
    plan: list[NarrationMoment] = []

    t_ms = 0.0
    while t_ms < max(duration_ms, 0.0):
        plan.append(
            NarrationMoment(
                timestamp_ms=t_ms,
                label="timeline sample",
                reason="regular 5-second timeline sample",
            )
        )
        t_ms += base_interval_ms

    if not plan or abs(plan[-1].timestamp_ms - duration_ms) >= min_gap_ms:
        plan.append(
            NarrationMoment(
                timestamp_ms=max(0.0, duration_ms),
                label="timeline sample",
                reason="final recording state",
            )
        )

    for moment in activity_moments:
        for idx, existing in enumerate(plan):
            if abs(existing.timestamp_ms - moment.timestamp_ms) < min_gap_ms:
                if existing.label == "timeline sample":
                    plan[idx] = NarrationMoment(
                        timestamp_ms=existing.timestamp_ms,
                        label=moment.label,
                        reason=moment.reason,
                        score=moment.score,
                    )
                break
        else:
            plan.append(moment)

    plan.sort(key=lambda moment: moment.timestamp_ms)
    return plan


def _narration_item_priority(label: str) -> int:
    """Rank narration items so stronger cues win when timestamps collide."""
    priorities = {
        "timeline sample": 0,
        "movement cue": 1,
        "activity cue": 2,
        "zoom cue": 3,
    }
    return priorities.get(label, 1)


def _dedupe_narration_items(
    items: List[Any],
    score_getter: Optional[Callable[[Any], float]] = None,
) -> List[Any]:
    """Collapse identical timestamps while keeping the richest narration cue."""
    deduped: list[Any] = []
    for item in sorted(items, key=lambda value: float(value.timestamp_ms)):
        timestamp_key = int(round(float(item.timestamp_ms)))
        if deduped and int(round(float(deduped[-1].timestamp_ms))) == timestamp_key:
            current = deduped[-1]
            current_priority = (
                _narration_item_priority(current.label),
                float(score_getter(current) if score_getter else 0.0),
            )
            candidate_priority = (
                _narration_item_priority(item.label),
                float(score_getter(item) if score_getter else 0.0),
            )
            if candidate_priority > current_priority:
                deduped[-1] = item
            continue
        deduped.append(item)
    return deduped


def _limit_narration_items(
    items: List[Any],
    max_items: int,
    score_getter: Optional[Callable[[Any], float]] = None,
) -> List[Any]:
    """Trim narration items to a provider-safe budget without losing key context."""
    if max_items <= 0 or not items:
        return []

    deduped = _dedupe_narration_items(items, score_getter=score_getter)
    if len(deduped) <= max_items:
        return deduped

    if max_items == 1:
        return [deduped[-1]]

    selected: set[int] = {0, len(deduped) - 1}
    activity_indexes = [
        idx for idx, item in enumerate(deduped)
        if idx not in selected and _narration_item_priority(item.label) > 0
    ]
    if activity_indexes:
        activity_indexes.sort(
            key=lambda idx: (
                -float(score_getter(deduped[idx]) if score_getter else 0.0),
                float(deduped[idx].timestamp_ms),
            )
        )
        remaining_activity_slots = max(0, max_items - len(selected))
        selected.update(activity_indexes[:remaining_activity_slots])

    while len(selected) < max_items:
        remaining = [idx for idx in range(len(deduped)) if idx not in selected]
        if not remaining:
            break

        selected_timestamps = sorted(deduped[idx].timestamp_ms for idx in selected)

        def coverage_key(idx: int) -> tuple[float, float, float, float]:
            timestamp = float(deduped[idx].timestamp_ms)
            insert_at = bisect.bisect_left(selected_timestamps, timestamp)
            prev_timestamp = selected_timestamps[insert_at - 1] if insert_at > 0 else timestamp
            next_timestamp = (
                selected_timestamps[insert_at]
                if insert_at < len(selected_timestamps)
                else timestamp
            )
            nearest_gap = min(timestamp - prev_timestamp, next_timestamp - timestamp)
            span = next_timestamp - prev_timestamp
            score = float(score_getter(deduped[idx]) if score_getter else 0.0)
            return (
                nearest_gap,
                span,
                score,
                -timestamp,
            )

        best_idx = max(remaining, key=coverage_key)
        selected.add(best_idx)

    limited = [deduped[idx] for idx in sorted(selected)]
    logger.info(
        "Trimmed narration items from %d to %d to respect the %d-image provider cap",
        len(deduped),
        len(limited),
        max_items,
    )
    return limited

def _limit_extracted_narration_frames(
    frames: List[ExtractedNarrationFrame],
    max_images: int = _NARRATION_PROVIDER_MAX_IMAGES,
) -> List[ExtractedNarrationFrame]:
    """Defensively cap the final prompt frame pack before building payload content."""
    return _limit_narration_items(frames, max_images)


def _resolve_frame_index(
    target_ms: float,
    total_frames: int,
    duration_ms: float,
    frame_timestamps: Optional[List[float]],
    fps: float,
) -> int:
    """Map a recording timestamp to the closest video frame index."""
    if total_frames <= 1:
        return 0

    clamped_ms = max(0.0, min(duration_ms, target_ms)) if duration_ms > 0 else max(0.0, target_ms)

    if frame_timestamps:
        timestamps = [float(t) for t in frame_timestamps[:total_frames]]
        if timestamps:
            idx = bisect.bisect_right(timestamps, clamped_ms) - 1
            idx = max(0, min(idx, len(timestamps) - 1))
            if idx + 1 < len(timestamps):
                prev_delta = abs(clamped_ms - timestamps[idx])
                next_delta = abs(timestamps[idx + 1] - clamped_ms)
                if next_delta < prev_delta:
                    idx += 1
            return max(0, min(idx, total_frames - 1))

    if duration_ms > 0:
        return max(0, min(total_frames - 1, int(round((clamped_ms / duration_ms) * (total_frames - 1)))))

    if fps > 0:
        return max(0, min(total_frames - 1, int(round((clamped_ms / 1000.0) * fps))))

    return 0


def _extract_narration_frames(
    video_path: str,
    frame_plan: List[NarrationMoment],
    duration_ms: float,
    frame_timestamps: Optional[List[float]] = None,
) -> List[ExtractedNarrationFrame]:
    """Extract and encode narration prompt frames as JPEG data URLs."""
    if not video_path or not os.path.isfile(video_path):
        raise ValueError("Narration generation requires a readable video file.")

    try:
        import cv2
    except ImportError as exc:
        raise RuntimeError(
            "opencv-python-headless is required for narration frame extraction."
        ) from exc

    cap = cv2.VideoCapture(video_path)
    if not cap.isOpened():
        raise RuntimeError(f"Could not open video for narration sampling: {video_path}")

    try:
        total_frames = int(max(0, cap.get(cv2.CAP_PROP_FRAME_COUNT)))
        fps = float(cap.get(cv2.CAP_PROP_FPS) or 0.0)
        if total_frames <= 0:
            raise RuntimeError("Video has no readable frames for narration generation.")

        indexed_plan: list[tuple[int, NarrationMoment]] = []
        for moment in frame_plan:
            frame_idx = _resolve_frame_index(
                moment.timestamp_ms,
                total_frames,
                duration_ms,
                frame_timestamps,
                fps,
            )
            if indexed_plan and indexed_plan[-1][0] == frame_idx:
                prev_idx, prev_moment = indexed_plan[-1]
                if prev_moment.label == "timeline sample" and moment.label != "timeline sample":
                    indexed_plan[-1] = (prev_idx, moment)
                continue
            indexed_plan.append((frame_idx, moment))

        extracted: list[ExtractedNarrationFrame] = []
        for frame_idx, moment in indexed_plan:
            cap.set(cv2.CAP_PROP_POS_FRAMES, frame_idx)
            ok, frame = cap.read()
            if not ok or frame is None:
                logger.warning(
                    "Skipping narration frame at %s (frame %d)",
                    _format_time_label(moment.timestamp_ms),
                    frame_idx,
                )
                continue

            height, width = frame.shape[:2]
            max_dim = max(height, width)
            if max_dim > 960:
                scale = 960.0 / max_dim
                frame = cv2.resize(
                    frame,
                    (max(1, int(round(width * scale))), max(1, int(round(height * scale)))),
                    interpolation=cv2.INTER_AREA,
                )

            ok, encoded = cv2.imencode(
                ".jpg",
                frame,
                [int(cv2.IMWRITE_JPEG_QUALITY), 82],
            )
            if not ok:
                raise RuntimeError(
                    f"Failed to encode narration frame at {moment.timestamp_ms:.0f}ms."
                )

            data_url = (
                "data:image/jpeg;base64,"
                + base64.b64encode(encoded.tobytes()).decode("ascii")
            )
            extracted.append(
                ExtractedNarrationFrame(
                    timestamp_ms=moment.timestamp_ms,
                    label=moment.label,
                    reason=moment.reason,
                    data_url=data_url,
                )
            )

        if not extracted:
            raise RuntimeError("Narration frame extraction produced no usable frames.")

        return extracted
    finally:
        cap.release()


def _build_narration_user_content(
    summary: str,
    duration_ms: float,
    activity_moments: List[NarrationMoment],
    frames: List[ExtractedNarrationFrame],
    timing_targets: Optional[List[NarrationTimingTarget]] = None,
) -> List[dict[str, Any]]:
    """Build multimodal user content for narration generation."""
    frames = _limit_extracted_narration_frames(frames)
    target_words = max(25, int(round((duration_ms / 1000.0) * _NARRATION_WORDS_PER_SECOND)))
    moments_text = _format_narration_moments_text(activity_moments)
    timing_targets = timing_targets or _build_narration_timing_targets(duration_ms)

    intro = _NARRATION_USER_PROMPT.format(
        duration=duration_ms / 1000.0,
        target_words=target_words,
        words_per_second=_NARRATION_WORDS_PER_SECOND,
        section_plan=_format_narration_section_plan(timing_targets),
        summary=summary,
        moments=moments_text,
    )

    content: List[dict[str, Any]] = [{"type": "text", "text": intro}]
    content.append({
        "type": "text",
        "text": (
            "Frame pack follows in chronological order. Treat the screenshots as "
            "evidence for the story, stakes, tools, labels, values, and outcomes "
            "that matter — not as a caption checklist."
        ),
    })

    for idx, frame in enumerate(frames, start=1):
        content.append({
            "type": "text",
            "text": (
                f"Frame {idx} — {_format_time_label(frame.timestamp_ms)} — "
                f"{frame.label}. {frame.reason}."
            ),
        })
        content.append({
            "type": "image_url",
            "image_url": {"url": frame.data_url, "detail": "low"},
        })

    return content


def _format_narration_moments_text(moments: List[NarrationMoment]) -> str:
    """Render narration moments for prompts."""
    return "\n".join(
        f"- {_format_time_label(moment.timestamp_ms)} — {moment.reason}"
        for moment in moments
    ) or "- No strong narrative cues beyond the regular timeline samples."


def _count_spoken_words(text: str) -> int:
    """Count spoken words in narration text."""
    return len(re.findall(r"[A-Za-z0-9]+(?:['’-][A-Za-z0-9]+)?", text))


_LITERAL_CLICK_NARRATION_PATTERN = re.compile(
    r"\b(?:double[\s-]?click(?:s|ed|ing)?|right[\s-]?click(?:s|ed|ing)?|click(?:s|ed|ing)?)\b",
    re.IGNORECASE,
)
_LITERAL_ZOOM_NARRATION_PATTERN = re.compile(
    r"\b(?:zoom(?:s|ed|ing)?(?:\s+(?:in|into|out|on))?|camera\s+(?:zooms|zoomed|zooming)|"
    r"pan(?:s|ned|ning)?|camera\s+(?:moves|moved|moving)|camera\s+(?:pans|panned|panning))\b",
    re.IGNORECASE,
)


def _collect_narration_style_issues(segments: List[NarrationScriptSegment]) -> list[str]:
    """Return any narration beats that still describe clicks/zooms too literally."""
    issues: list[str] = []
    for segment in segments:
        text = segment.narration
        if _LITERAL_CLICK_NARRATION_PATTERN.search(text):
            issues.append(
                f"{segment.section}: replace literal click wording with the action, intent, or outcome."
            )
        if _LITERAL_ZOOM_NARRATION_PATTERN.search(text):
            issues.append(
                f"{segment.section}: replace literal zoom or camera wording with the idea or payoff being emphasized."
            )
    return issues


def _format_narration_style_issues(segments: List[NarrationScriptSegment]) -> str:
    """Render style cleanup notes for the polish prompt."""
    issues = _collect_narration_style_issues(segments)
    if not issues:
        return "- None. Keep the same direct, natural style."
    return "\n".join(f"- {issue}" for issue in issues)


def _narration_word_bounds(
    target_words: int,
    tolerance_ratio: float = _NARRATION_TIMING_WORD_TOLERANCE_RATIO,
) -> tuple[int, int]:
    """Return an acceptable word-count range around a target."""
    if target_words <= 0:
        return 0, 0
    tolerance = max(2, int(math.ceil(target_words * tolerance_ratio)))
    return max(1, target_words - tolerance), target_words + tolerance


def _build_narration_timing_targets(
    duration_ms: float,
    start_ms_by_section: Optional[dict[str, float]] = None,
) -> List[NarrationTimingTarget]:
    """Build section timing targets from either defaults or parsed segment starts."""
    draft_segments = [
        NarrationScriptSegment(
            section=section,
            start_ms=(
                start_ms_by_section.get(section, _default_narration_segment_start_ms(section, duration_ms))
                if start_ms_by_section else
                _default_narration_segment_start_ms(section, duration_ms)
            ),
            narration="Placeholder narration.",
        )
        for section in _NARRATION_SECTION_ORDER
    ]
    normalized = _normalize_narration_segments(draft_segments, duration_ms)
    total_target_words = max(25, int(round((duration_ms / 1000.0) * _NARRATION_WORDS_PER_SECOND)))
    end_points: list[float] = []
    weights: list[float] = []
    for index, segment in enumerate(normalized):
        end_ms = normalized[index + 1].start_ms if index + 1 < len(normalized) else max(segment.start_ms, duration_ms)
        end_points.append(end_ms)
        weights.append(max(1.0, end_ms - segment.start_ms))

    base_words = min(
        _NARRATION_TIMING_MIN_SECTION_WORDS,
        max(1, total_target_words // max(1, len(normalized))),
    )
    remaining_words = max(0, total_target_words - (base_words * len(normalized)))
    total_weight = sum(weights) or float(len(weights))
    raw_extras = [remaining_words * (weight / total_weight) for weight in weights]
    extra_words = [int(math.floor(extra)) for extra in raw_extras]
    remainder = remaining_words - sum(extra_words)
    remainders = sorted(
        range(len(raw_extras)),
        key=lambda index: (raw_extras[index] - extra_words[index], weights[index]),
        reverse=True,
    )
    for index in remainders[:remainder]:
        extra_words[index] += 1

    return [
        NarrationTimingTarget(
            section=segment.section,
            start_ms=segment.start_ms,
            end_ms=end_points[index],
            target_words=base_words + extra_words[index],
        )
        for index, segment in enumerate(normalized)
    ]


def _format_narration_section_plan(targets: List[NarrationTimingTarget]) -> str:
    """Render section timing targets for prompts."""
    lines: list[str] = []
    for target in targets:
        lower_words, upper_words = _narration_word_bounds(target.target_words)
        lines.append(
            f"- {target.section}: {_format_time_label(target.start_ms)} → "
            f"{_format_time_label(target.end_ms)} "
            f"({target.duration_ms / 1000.0:.1f}s), aim for about {target.target_words} "
            f"spoken words ({lower_words}-{upper_words} is acceptable)"
        )
    return "\n".join(lines)


def _build_segment_timing_targets_from_segments(
    segments: List[NarrationScriptSegment],
    duration_ms: float,
) -> List[NarrationTimingTarget]:
    """Build timing targets from the current parsed segment layout."""
    return _build_narration_timing_targets(
        duration_ms,
        start_ms_by_section={segment.section: segment.start_ms for segment in segments},
    )


def _format_narration_draft_pacing(
    segments: List[NarrationScriptSegment],
    timing_targets: List[NarrationTimingTarget],
) -> str:
    """Render the current draft pacing against the requested targets."""
    lines: list[str] = []
    for segment, target in zip(segments, timing_targets):
        lower_words, upper_words = _narration_word_bounds(
            target.target_words,
            tolerance_ratio=_NARRATION_TIMING_SECTION_WORD_TOLERANCE_RATIO,
        )
        actual_words = _count_spoken_words(segment.narration)
        lines.append(
            f"- {target.section}: {actual_words} words now; target about {target.target_words} "
            f"words ({lower_words}-{upper_words}) across {target.duration_ms / 1000.0:.1f}s"
        )
    return "\n".join(lines)


def _serialize_narration_segments(segments: List[NarrationScriptSegment]) -> str:
    """Render narration segments as JSON for revision prompts."""
    payload = {
        "segments": [
            {
                "section": segment.section,
                "start_ms": int(round(segment.start_ms)),
                "narration": segment.narration.strip(),
            }
            for segment in segments
        ]
    }
    return json.dumps(payload, indent=2, ensure_ascii=False)


def _build_narration_timing_polish_prompt(
    summary: str,
    duration_ms: float,
    moments: List[NarrationMoment],
    segments: List[NarrationScriptSegment],
) -> str:
    """Build the text-only pacing-polish prompt for an existing narration draft."""
    timing_targets = _build_segment_timing_targets_from_segments(segments, duration_ms)
    total_target_words = sum(target.target_words for target in timing_targets)
    return _NARRATION_TIMING_POLISH_USER_PROMPT.format(
        duration=duration_ms / 1000.0,
        target_words=total_target_words,
        words_per_second=_NARRATION_WORDS_PER_SECOND,
        section_plan=_format_narration_section_plan(timing_targets),
        summary=summary,
        moments=_format_narration_moments_text(moments),
        current_pacing=_format_narration_draft_pacing(segments, timing_targets),
        style_issues=_format_narration_style_issues(segments),
        draft=_serialize_narration_segments(segments),
    )


def _needs_narration_timing_polish(
    segments: List[NarrationScriptSegment],
    timing_targets: List[NarrationTimingTarget],
) -> bool:
    """Decide whether a draft needs a rewrite pass for pacing or narration style."""
    actual_total_words = sum(_count_spoken_words(segment.narration) for segment in segments)
    target_total_words = sum(target.target_words for target in timing_targets)
    total_lower, total_upper = _narration_word_bounds(target_total_words)
    if actual_total_words < total_lower or actual_total_words > total_upper:
        return True

    for segment, target in zip(segments, timing_targets):
        lower_words, upper_words = _narration_word_bounds(
            target.target_words,
            tolerance_ratio=_NARRATION_TIMING_SECTION_WORD_TOLERANCE_RATIO,
        )
        actual_words = _count_spoken_words(segment.narration)
        if actual_words < lower_words or actual_words > upper_words:
            return True
    return bool(_collect_narration_style_issues(segments))


def _preserve_narration_segment_starts(
    segments: List[NarrationScriptSegment],
    start_ms_by_section: dict[str, float],
) -> List[NarrationScriptSegment]:
    """Keep narration section timestamps stable across text-only revision passes."""
    return [
        NarrationScriptSegment(
            section=segment.section,
            start_ms=start_ms_by_section.get(segment.section, segment.start_ms),
            narration=segment.narration,
        )
        for segment in segments
    ]


def _polish_narration_segments_for_timing(
    settings: AISettings,
    summary: str,
    duration_ms: float,
    moments: List[NarrationMoment],
    segments: List[NarrationScriptSegment],
    guidance: str = "",
    status_callback: Optional[Callable[[str], None]] = None,
) -> List[NarrationScriptSegment]:
    """Rewrite a draft narration when pacing or literal mechanics need cleanup."""
    timing_targets = _build_segment_timing_targets_from_segments(segments, duration_ms)
    if not _needs_narration_timing_polish(segments, timing_targets):
        return segments

    if status_callback:
        status_callback("Refining narration wording and pacing…")
    logger.info("Narration draft needs polish for pacing/style; requesting rewrite pass")

    try:
        response = _call_narration_chat(
            settings,
            _build_narration_system_prompt(guidance),
            _build_narration_timing_polish_prompt(
                summary,
                duration_ms,
                moments,
                segments,
            ),
        )
        polished = _parse_narration_segments_response(response, duration_ms)
    except Exception as exc:
        logger.warning("Narration polish failed; using original draft: %s", exc)
        return segments

    polished = _preserve_narration_segment_starts(
        polished,
        {segment.section: segment.start_ms for segment in segments},
    )

    polished_targets = _build_segment_timing_targets_from_segments(polished, duration_ms)
    actual_total_words = sum(_count_spoken_words(segment.narration) for segment in polished)
    target_total_words = sum(target.target_words for target in polished_targets)
    total_lower, total_upper = _narration_word_bounds(target_total_words)
    if actual_total_words < total_lower or actual_total_words > total_upper:
        logger.warning(
            "Narration pacing still drifted after polish: target=%d words, actual=%d words",
            target_total_words,
            actual_total_words,
        )
    remaining_style_issues = _collect_narration_style_issues(polished)
    if remaining_style_issues:
        logger.warning(
            "Narration still includes literal click/zoom wording after polish: %s",
            "; ".join(remaining_style_issues),
        )
    return polished


def _chunk_narration_frames(
    frames: List[ExtractedNarrationFrame],
    batch_size: int = _NARRATION_BATCH_IMAGE_BUDGET,
) -> List[List[ExtractedNarrationFrame]]:
    """Split extracted frames into provider-safe chronological batches."""
    if batch_size <= 0:
        raise ValueError("Narration batch size must be positive.")
    effective_batch_size = min(batch_size, _NARRATION_PROVIDER_MAX_IMAGES)
    return [
        frames[start:start + effective_batch_size]
        for start in range(0, len(frames), effective_batch_size)
    ]


def _slice_narration_moments(
    moments: List[NarrationMoment],
    start_ms: float,
    end_ms: float,
    padding_ms: float = _NARRATION_ACTIVITY_GAP_MS,
) -> List[NarrationMoment]:
    """Collect moments relevant to a specific frame slice."""
    return [
        moment
        for moment in moments
        if (start_ms - padding_ms) <= moment.timestamp_ms <= (end_ms + padding_ms)
    ]


def _build_narration_batch_user_content(
    batch_index: int,
    total_batches: int,
    frames: List[ExtractedNarrationFrame],
    moments: List[NarrationMoment],
) -> List[dict[str, Any]]:
    """Build one multimodal slice-analysis payload."""
    batch_frames = _limit_extracted_narration_frames(frames)
    start_ms = batch_frames[0].timestamp_ms
    end_ms = batch_frames[-1].timestamp_ms
    intro = _NARRATION_BATCH_USER_PROMPT.format(
        batch_index=batch_index,
        total_batches=total_batches,
        start_label=_format_time_label(start_ms),
        end_label=_format_time_label(end_ms),
        frame_count=len(batch_frames),
        moments=_format_narration_moments_text(moments),
    )

    content: List[dict[str, Any]] = [{"type": "text", "text": intro}]
    for idx, frame in enumerate(batch_frames, start=1):
        content.append({
            "type": "text",
            "text": (
                f"Slice frame {idx} — {_format_time_label(frame.timestamp_ms)} — "
                f"{frame.label}. {frame.reason}."
            ),
        })
        content.append({
            "type": "image_url",
            "image_url": {"url": frame.data_url, "detail": "low"},
        })
    return content


def _build_narration_synthesis_user_prompt(
    summary: str,
    duration_ms: float,
    moments: List[NarrationMoment],
    batch_notes: List[NarrationBatchNote],
    timing_targets: Optional[List[NarrationTimingTarget]] = None,
) -> str:
    """Build the final text-only synthesis prompt after batch analysis."""
    target_words = max(25, int(round((duration_ms / 1000.0) * _NARRATION_WORDS_PER_SECOND)))
    timing_targets = timing_targets or _build_narration_timing_targets(duration_ms)
    slice_notes = "\n\n".join(
        (
            f"### Slice {note.batch_index} "
            f"({_format_time_label(note.start_ms)} → {_format_time_label(note.end_ms)}, "
            f"{note.frame_count} frames)\n"
            f"{note.markdown}"
        )
        for note in batch_notes
    )
    return _NARRATION_SYNTHESIS_USER_PROMPT.format(
        duration=duration_ms / 1000.0,
        target_words=target_words,
        words_per_second=_NARRATION_WORDS_PER_SECOND,
        section_plan=_format_narration_section_plan(timing_targets),
        summary=summary,
        moments=_format_narration_moments_text(moments),
        slice_notes=slice_notes,
    )


def _build_shared_recording_batch_notes(
    settings: AISettings,
    moments: List[NarrationMoment],
    frames: List[ExtractedNarrationFrame],
    status_callback: Optional[Callable[[str], None]] = None,
) -> List[NarrationBatchNote]:
    """Analyze oversized frame packs once so narration and chapters can reuse the notes."""
    import time as _time

    batches = _chunk_narration_frames(frames)
    batch_notes: list[NarrationBatchNote] = []

    logger.info(
        "Shared recording knowledge uses %d frames; analyzing in %d batches of up to %d images",
        len(frames),
        len(batches),
        _NARRATION_BATCH_IMAGE_BUDGET,
    )

    for batch_offset, batch_frames in enumerate(batches, start=1):
        if batch_offset > 1:
            _time.sleep(_NARRATION_BATCH_PAUSE_SECONDS)
        if status_callback:
            status_callback(
                f"Analyzing shared recording context ({batch_offset}/{len(batches)})…"
            )
        batch_start_ms = batch_frames[0].timestamp_ms
        batch_end_ms = batch_frames[-1].timestamp_ms
        batch_moments = _slice_narration_moments(moments, batch_start_ms, batch_end_ms)
        batch_content = _build_narration_batch_user_content(
            batch_offset,
            len(batches),
            batch_frames,
            batch_moments,
        )
        batch_markdown = _clean_markdown_response(
            _call_narration_chat(settings, _NARRATION_BATCH_SYSTEM_PROMPT, batch_content)
        )
        batch_notes.append(
            NarrationBatchNote(
                batch_index=batch_offset,
                start_ms=batch_start_ms,
                end_ms=batch_end_ms,
                frame_count=len(batch_frames),
                markdown=batch_markdown,
            )
        )
    return batch_notes


def _build_shared_recording_knowledge(
    settings: AISettings,
    video_path: str,
    mouse_track: List[MousePosition],
    monitor_rect: dict,
    duration_ms: float,
    key_events: Optional[List[KeyEvent]] = None,
    click_events: Optional[List[ClickEvent]] = None,
    zoom_keyframes: Optional[List[ZoomKeyframe]] = None,
    annotations: Optional[AnnotationCollection] = None,
    frame_timestamps: Optional[List[float]] = None,
    status_callback: Optional[Callable[[str], None]] = None,
) -> SharedRecordingKnowledge:
    """Build or reuse the shared multimodal evidence behind narration and chapters."""
    if key_events:
        logger.info(
            "Ignoring %d removed keystroke event(s) while building shared AI context",
            len(key_events),
        )
    if annotations:
        logger.info("Ignoring removed annotations while building shared AI context")
    summary = _build_narration_context_summary(
        mouse_track,
        key_events,
        click_events,
        monitor_rect,
        duration_ms,
        zoom_keyframes=zoom_keyframes,
        annotations=annotations,
    )
    activity_moments = _select_narration_activity_moments(
        mouse_track,
        key_events,
        click_events,
        monitor_rect,
        duration_ms,
    )
    zoom_moments = _select_narration_zoom_moments(zoom_keyframes, duration_ms)
    narration_moments = _merge_narration_moments(
        activity_moments,
        zoom_moments,
    )
    frame_plan = _build_narration_frame_plan(duration_ms, narration_moments)
    cache_key = _shared_recording_knowledge_cache_key(
        settings=settings,
        video_path=video_path,
        duration_ms=duration_ms,
        monitor_rect=monitor_rect,
        summary=summary,
        moments=narration_moments,
        frame_plan=frame_plan,
    )
    cached = _get_cached_shared_recording_knowledge(cache_key)
    if cached is not None:
        logger.info("Reusing shared recording knowledge cache entry %s", cache_key[:8])
        return cached

    if status_callback:
        status_callback("Sampling shared recording context…")
    frames = _extract_narration_frames(
        video_path,
        frame_plan,
        duration_ms,
        frame_timestamps=frame_timestamps,
    )
    batch_notes: list[NarrationBatchNote] = []
    if len(frames) > _NARRATION_PROVIDER_MAX_IMAGES:
        batch_notes = _build_shared_recording_batch_notes(
            settings,
            narration_moments,
            frames,
            status_callback=status_callback,
        )

    return _store_cached_shared_recording_knowledge(
        cache_key,
        SharedRecordingKnowledge(
            summary=summary,
            activity_moments=narration_moments,
            frames=frames,
            batch_notes=batch_notes,
        ),
    )


def _normalize_narration_section(section: str) -> str:
    """Map section labels to the canonical narration arc."""
    normalized = " ".join(section.replace("/", " / ").split()).strip()
    lowered = normalized.lower()
    mapping = {
        "context": "Context",
        "background": "Background",
        "prompt / action": "Prompt / Action",
        "prompt/action": "Prompt / Action",
        "action": "Prompt / Action",
        "walkthrough": "Walkthrough",
        "result": "Result",
    }
    return mapping.get(lowered, normalized)


def _default_narration_segment_start_ms(section: str, duration_ms: float) -> float:
    """Return a durable fallback timestamp for a narration section."""
    ratios = {
        "Context": 0.00,
        "Background": 0.12,
        "Prompt / Action": 0.28,
        "Walkthrough": 0.50,
        "Result": 0.82,
    }
    return max(0.0, duration_ms * ratios.get(section, 0.0))


def _normalize_narration_segments(
    segments: List[NarrationScriptSegment],
    duration_ms: float,
) -> List[NarrationScriptSegment]:
    """Clamp section timestamps and enforce canonical ordering."""
    by_section = {segment.section: segment for segment in segments}
    normalized: list[NarrationScriptSegment] = []
    min_gap_ms = 500.0

    for index, section in enumerate(_NARRATION_SECTION_ORDER):
        segment = by_section.get(section)
        if segment is None:
            raise ValueError(f"Narration response missing required section: {section}")

        remaining = len(_NARRATION_SECTION_ORDER) - index - 1
        raw_start_ms = float(segment.start_ms)
        if not math.isfinite(raw_start_ms):
            raw_start_ms = _default_narration_segment_start_ms(section, duration_ms)
        lower_bound = normalized[-1].start_ms + min_gap_ms if normalized else 0.0
        upper_bound = max(lower_bound, duration_ms - (remaining * min_gap_ms))
        clamped_start_ms = max(lower_bound, min(raw_start_ms, upper_bound))
        spoken_text = _clean_narration_spoken_text(segment.narration, section=section)
        if not spoken_text:
            raise ValueError(f"Narration response produced no spoken text for section: {section}")
        normalized.append(
            NarrationScriptSegment(
                section=section,
                start_ms=clamped_start_ms,
                narration=spoken_text,
            )
        )

    return normalized


def _parse_narration_segments_from_markdown(
    markdown_text: str,
    duration_ms: float,
) -> List[NarrationScriptSegment]:
    """Fallback parser for legacy markdown narration responses."""
    cleaned = _clean_markdown_response(markdown_text)
    sections: dict[str, list[str]] = {}
    current_section: Optional[str] = None
    current_lines: list[str] = []

    def _flush() -> None:
        if current_section is None:
            return
        content = "\n".join(current_lines).strip()
        if content:
            sections[current_section] = [content]

    for raw_line in cleaned.splitlines():
        stripped = raw_line.strip()
        if stripped.startswith("#"):
            heading = _normalize_narration_section(stripped.lstrip("#").strip())
            if heading in _NARRATION_SECTION_ORDER:
                _flush()
                current_section = heading
                current_lines = []
                continue
        if current_section is not None:
            current_lines.append(raw_line)

    _flush()

    parsed = [
        NarrationScriptSegment(
            section=section,
            start_ms=float("nan"),
            narration=sections[section][0],
        )
        for section in _NARRATION_SECTION_ORDER
        if section in sections
    ]
    return _normalize_narration_segments(parsed, duration_ms)


def _parse_narration_segments_response(
    response_text: str,
    duration_ms: float,
) -> List[NarrationScriptSegment]:
    """Parse narration JSON into the canonical five spoken sections."""
    cleaned = _clean_json_response(response_text)
    try:
        data = json.loads(cleaned)
    except json.JSONDecodeError:
        logger.warning("Narration response was not JSON; falling back to markdown parsing")
        return _parse_narration_segments_from_markdown(response_text, duration_ms)

    raw_segments = data.get("segments") if isinstance(data, dict) else data
    if not isinstance(raw_segments, list):
        raise ValueError("Narration response is not a JSON segment array")

    parsed: list[NarrationScriptSegment] = []
    seen_sections: set[str] = set()
    for item in raw_segments:
        if not isinstance(item, dict):
            continue
        section = _normalize_narration_section(str(item.get("section", "")).strip())
        if section not in _NARRATION_SECTION_ORDER or section in seen_sections:
            continue
        narration = str(item.get("narration", item.get("text", ""))).strip()
        if not narration:
            continue
        try:
            start_ms = float(item.get("start_ms", float("nan")))
        except (TypeError, ValueError):
            start_ms = float("nan")
        parsed.append(
            NarrationScriptSegment(
                section=section,
                start_ms=start_ms,
                narration=narration,
            )
        )
        seen_sections.add(section)

    return _normalize_narration_segments(parsed, duration_ms)


def _build_segment_markdown(segment: NarrationScriptSegment) -> str:
    """Render one narration segment as markdown."""
    return _build_generated_narration_markdown(segment.section, segment.narration)


def _build_narration_markdown_script(segments: List[NarrationScriptSegment]) -> str:
    """Combine narration segments into one markdown sidecar document."""
    return "\n\n".join(_build_segment_markdown(segment) for segment in segments).strip()


def _combine_narration_tts_text(segments: List[NarrationScriptSegment]) -> str:
    """Combine narration segments into one plain spoken transcript."""
    spoken_sections = [
        _clean_narration_spoken_text(segment.narration, section=segment.section)
        for segment in segments
    ]
    return "\n\n".join(section for section in spoken_sections if section)


def _build_narration_segment_audio_path(
    script_path: str,
    segment_index: int,
    section: str,
) -> str:
    """Derive a clear WAV filename for one generated narration segment."""
    root, _ = os.path.splitext(script_path)
    return f"{root}_{segment_index:02d}_{_slugify_narration_label(section)}.wav"


def _extract_narration_section_label(script_markdown: str) -> str:
    """Extract the canonical section label from a segment markdown block."""
    for raw_line in script_markdown.splitlines():
        stripped = raw_line.strip()
        if not stripped:
            continue
        if stripped.startswith("#"):
            heading = _normalize_narration_section(stripped.lstrip("#").strip())
            if heading in _NARRATION_SECTION_ORDER:
                return heading
            break
    return "Segment"


def _generated_narration_duration_map(
    segments: Optional[List[VoiceoverSegment]],
    video_duration_ms: float,
) -> dict[str, float]:
    """Infer usable durations for generated narration segments without audio yet."""
    generated_segments = sorted(
        [segment for segment in (segments or []) if segment.is_generated_narration],
        key=lambda segment: segment.timestamp,
    )
    durations: dict[str, float] = {}
    for index, segment in enumerate(generated_segments):
        if segment.duration_ms > 0:
            durations[segment.id] = segment.duration_ms
            continue
        next_start = (
            generated_segments[index + 1].timestamp
            if index + 1 < len(generated_segments)
            else video_duration_ms
        )
        durations[segment.id] = max(0.0, next_start - segment.timestamp)
    return durations


def _normalize_generated_narration_voiceover_segments(
    segments: Optional[List[VoiceoverSegment]],
    *,
    video_duration_ms: float,
) -> List[VoiceoverSegment]:
    """Shift generated narration segments forward so measured audio never overlaps."""
    ordered_segments = sorted(list(segments or []), key=lambda segment: segment.timestamp)
    if not ordered_segments:
        return []

    effective_durations = _generated_narration_duration_map(ordered_segments, video_duration_ms)
    next_generated_start = 0.0
    for segment in ordered_segments:
        if not segment.is_generated_narration:
            continue
        raw_start = float(segment.timestamp)
        if not math.isfinite(raw_start):
            raw_start = next_generated_start
        segment.timestamp = max(0.0, raw_start, next_generated_start)
        next_generated_start = (
            segment.timestamp + max(0.0, effective_durations.get(segment.id, 0.0))
        )

    ordered_segments.sort(key=lambda segment: segment.timestamp)
    return ordered_segments


def _split_narration_sentences(text: str) -> list[str]:
    """Split narration text into sentence-like chunks."""
    import re

    normalized = " ".join(text.split()).strip()
    if not normalized:
        return []
    return [
        chunk.strip()
        for chunk in re.split(r"(?<=[.!?])\s+", normalized)
        if chunk.strip()
    ]


def _take_sentence_span(
    sentences: list[str],
    target_words: int,
    *,
    from_end: bool = False,
) -> list[str]:
    """Take enough whole sentences to satisfy the requested word budget."""
    if not sentences or target_words <= 0:
        return []
    running = 0
    selected: list[str] = []
    source = reversed(sentences) if from_end else sentences
    for sentence in source:
        selected.append(sentence)
        running += max(1, _count_spoken_words(sentence))
        if running >= target_words:
            break
    if from_end:
        selected.reverse()
    return selected


def _trim_generated_narration_text_for_ripple_delete(
    text: str,
    *,
    lead_ms: float,
    trail_ms: float,
    total_duration_ms: float,
) -> str:
    """Keep the narration portions that survive a ripple delete."""
    normalized = " ".join(text.split()).strip()
    if not normalized or total_duration_ms <= 0:
        return normalized

    lead_ms = max(0.0, lead_ms)
    trail_ms = max(0.0, trail_ms)
    keep_ms = lead_ms + trail_ms
    if keep_ms <= 0:
        return ""

    keep_ratio = max(0.0, min(1.0, keep_ms / total_duration_ms))
    if keep_ratio >= 0.97:
        return normalized

    sentences = _split_narration_sentences(normalized)
    if len(sentences) >= 2:
        total_words = max(
            1,
            sum(max(1, _count_spoken_words(sentence)) for sentence in sentences),
        )
        if len(sentences) >= 3:
            if lead_ms > 0 and trail_ms > 0:
                desired_count = max(
                    2,
                    min(len(sentences), int(round(len(sentences) * keep_ratio))),
                )
                lead_count = max(1, int(round(desired_count * (lead_ms / keep_ms))))
                trail_count = max(1, desired_count - lead_count)
                while lead_count + trail_count > desired_count:
                    if lead_count >= trail_count and lead_count > 1:
                        lead_count -= 1
                    elif trail_count > 1:
                        trail_count -= 1
                    else:
                        break
                selected = sentences[:lead_count] + sentences[len(sentences) - trail_count:]
            elif lead_ms > 0:
                desired_count = max(1, min(len(sentences), int(round(len(sentences) * keep_ratio))))
                selected = sentences[:desired_count]
            else:
                desired_count = max(1, min(len(sentences), int(round(len(sentences) * keep_ratio))))
                selected = sentences[len(sentences) - desired_count:]
        elif lead_ms > 0 and trail_ms > 0:
            lead_target = max(1, int(round(total_words * (lead_ms / total_duration_ms))))
            trail_target = max(1, int(round(total_words * (trail_ms / total_duration_ms))))
            prefix = _take_sentence_span(sentences, lead_target)
            suffix = _take_sentence_span(sentences, trail_target, from_end=True)
            prefix_count = len(prefix)
            suffix_count = len(suffix)
            while prefix_count + suffix_count > len(sentences):
                if prefix_count >= suffix_count and prefix_count > 1:
                    prefix_count -= 1
                elif suffix_count > 1:
                    suffix_count -= 1
                else:
                    break
            selected = sentences[:prefix_count] + sentences[len(sentences) - suffix_count:]
        elif lead_ms > 0:
            target_words = max(1, int(round(total_words * keep_ratio)))
            selected = _take_sentence_span(sentences, target_words)
        else:
            target_words = max(1, int(round(total_words * keep_ratio)))
            selected = _take_sentence_span(sentences, target_words, from_end=True)
        trimmed = " ".join(part for part in selected if part).strip()
        if trimmed:
            return trimmed

    words = normalized.split()
    if len(words) <= 1:
        return normalized
    if lead_ms > 0 and trail_ms > 0:
        lead_count = max(1, int(round(len(words) * (lead_ms / total_duration_ms))))
        trail_count = max(1, int(round(len(words) * (trail_ms / total_duration_ms))))
        if lead_count + trail_count >= len(words):
            return normalized
        selected_words = words[:lead_count] + words[-trail_count:]
    elif lead_ms > 0:
        keep_words = max(1, int(round(len(words) * keep_ratio)))
        selected_words = words[:keep_words]
    else:
        keep_words = max(1, int(round(len(words) * keep_ratio)))
        selected_words = words[-keep_words:]
    return " ".join(selected_words).strip()


def _build_generated_narration_markdown(section: str, narration_text: str) -> str:
    """Rebuild the markdown block for a generated narration segment."""
    spoken_text = _clean_narration_spoken_text(narration_text, section=section)
    return f"## {section}\n{spoken_text}".strip()


def _ripple_delete_time(timestamp_ms: float, delete_start_ms: float, delete_end_ms: float) -> float:
    """Map a timestamp through a ripple delete."""
    gap = delete_end_ms - delete_start_ms
    if gap <= 0:
        return timestamp_ms
    if timestamp_ms <= delete_start_ms:
        return timestamp_ms
    if timestamp_ms >= delete_end_ms:
        return timestamp_ms - gap
    return delete_start_ms


def ripple_delete_voiceover_segments(
    segments: Optional[List[VoiceoverSegment]],
    delete_start_ms: float,
    delete_end_ms: float,
    *,
    video_duration_ms: float,
) -> RippleDeleteVoiceoverSegmentsResult:
    """Ripple-delete a clip range while preserving generated narration when possible."""
    if delete_end_ms <= delete_start_ms:
        return RippleDeleteVoiceoverSegmentsResult(
            segments=list(segments or []),
            regenerated_segment_ids=(),
            removed_generated_count=0,
            removed_manual_count=0,
        )

    generated_durations = _generated_narration_duration_map(segments, video_duration_ms)
    updated_segments: list[VoiceoverSegment] = []
    regenerated_segment_ids: list[str] = []
    removed_generated_count = 0
    removed_manual_count = 0

    for segment in sorted(segments or [], key=lambda item: item.timestamp):
        effective_duration_ms = (
            generated_durations.get(segment.id, 0.0)
            if segment.is_generated_narration
            else max(segment.duration_ms, 1.0 if segment.duration_ms == 0 else segment.duration_ms)
        )
        end_ms = segment.timestamp + max(0.0, effective_duration_ms)
        overlaps_deleted_clip = (
            segment.timestamp < delete_end_ms and end_ms > delete_start_ms
        )
        if not overlaps_deleted_clip:
            shifted_timestamp = segment.timestamp
            if segment.timestamp >= delete_end_ms:
                shifted_timestamp -= delete_end_ms - delete_start_ms
            updated_segments.append(
                replace(segment, timestamp=max(0.0, shifted_timestamp))
            )
            continue

        if not segment.is_generated_narration:
            removed_manual_count += 1
            continue

        new_start_ms = _ripple_delete_time(segment.timestamp, delete_start_ms, delete_end_ms)
        new_end_ms = _ripple_delete_time(end_ms, delete_start_ms, delete_end_ms)
        if new_end_ms <= new_start_ms:
            removed_generated_count += 1
            continue

        lead_ms = max(0.0, min(end_ms, delete_start_ms) - segment.timestamp)
        trail_ms = max(0.0, end_ms - max(segment.timestamp, delete_end_ms))
        trimmed_text = _trim_generated_narration_text_for_ripple_delete(
            segment.text,
            lead_ms=lead_ms,
            trail_ms=trail_ms,
            total_duration_ms=max(1.0, end_ms - segment.timestamp),
        ) or segment.text.strip()
        section_label = _extract_narration_section_label(segment.script_markdown)
        updated_segments.append(
            replace(
                segment,
                timestamp=max(0.0, new_start_ms),
                text=trimmed_text,
                audio_path="",
                duration_ms=0.0,
                script_markdown=_build_generated_narration_markdown(section_label, trimmed_text),
            )
        )
        regenerated_segment_ids.append(segment.id)

    updated_segments = _normalize_generated_narration_voiceover_segments(
        updated_segments,
        video_duration_ms=video_duration_ms,
    )
    return RippleDeleteVoiceoverSegmentsResult(
        segments=updated_segments,
        regenerated_segment_ids=tuple(regenerated_segment_ids),
        removed_generated_count=removed_generated_count,
        removed_manual_count=removed_manual_count,
    )


def _generate_narration_segments(
    settings: AISettings,
    knowledge: SharedRecordingKnowledge,
    duration_ms: float,
    guidance: str = "",
    status_callback: Optional[Callable[[str], None]] = None,
) -> List[NarrationScriptSegment]:
    """Generate narration segments from shared recording knowledge."""
    system_prompt = _build_narration_system_prompt(guidance)
    default_timing_targets = _build_narration_timing_targets(duration_ms)
    if not knowledge.batch_notes:
        user_content = _build_narration_user_content(
            knowledge.summary,
            duration_ms,
            knowledge.activity_moments,
            knowledge.frames,
            timing_targets=default_timing_targets,
        )
        logger.info(
            "Calling AI for narration generation (%d frames, shared single pass)",
            len(knowledge.frames),
        )
        response = _call_narration_chat(settings, system_prompt, user_content)
        return _polish_narration_segments_for_timing(
            settings,
            knowledge.summary,
            duration_ms,
            knowledge.activity_moments,
            _parse_narration_segments_response(response, duration_ms),
            guidance=guidance,
            status_callback=status_callback,
        )

    if status_callback:
        status_callback("Synthesizing narration from shared recording context…")
    synthesis_prompt = _build_narration_synthesis_user_prompt(
        knowledge.summary,
        duration_ms,
        knowledge.activity_moments,
        knowledge.batch_notes,
        timing_targets=default_timing_targets,
    )
    logger.info(
        "Calling AI for final narration synthesis after reusing %d shared batch analyses",
        len(knowledge.batch_notes),
    )
    response = _call_narration_chat(settings, system_prompt, synthesis_prompt)
    return _polish_narration_segments_for_timing(
        settings,
        knowledge.summary,
        duration_ms,
        knowledge.activity_moments,
        _parse_narration_segments_response(response, duration_ms),
        guidance=guidance,
        status_callback=status_callback,
    )


def _chapter_count_bounds(duration_ms: float) -> tuple[int, int]:
    """Return sensible AI chapter-count bounds for a recording length."""
    if duration_ms <= 15000:
        return (1, 2)
    if duration_ms <= 45000:
        return (2, 4)
    if duration_ms <= 120000:
        return (3, 6)
    if duration_ms <= 300000:
        return (4, 8)
    return (5, 8)


def _chapter_min_gap_ms(duration_ms: float) -> float:
    """Return the minimum distance between AI chapter starts."""
    return max(2500.0, min(12000.0, duration_ms * 0.06))


def _normalize_chapter_title(title: str, index: int) -> str:
    """Collapse whitespace and provide a stable fallback chapter title."""
    normalized = " ".join((title or "").split()).strip(" -–—")
    return normalized or f"Chapter {index}"


def _parse_chapters_response(response: str, duration_ms: float) -> List[Chapter]:
    """Parse and normalize an AI chapter response."""
    try:
        payload = json.loads(_clean_json_response(response))
    except json.JSONDecodeError as exc:
        raise ValueError("AI chapter response was not valid JSON.") from exc

    if isinstance(payload, dict):
        raw_chapters = payload.get("chapters", [])
    elif isinstance(payload, list):
        raw_chapters = payload
    else:
        raise ValueError("AI chapter response used an unexpected shape.")

    normalized: list[Chapter] = []
    for index, item in enumerate(raw_chapters, start=1):
        if not isinstance(item, dict):
            continue
        raw_start = item.get("start_ms", item.get("timestamp_ms", item.get("timestampMs", 0)))
        raw_title = item.get("title", item.get("name", item.get("label", "")))
        try:
            start_ms = float(raw_start)
        except (TypeError, ValueError):
            start_ms = 0.0
        if not math.isfinite(start_ms):
            start_ms = 0.0
        start_ms = max(0.0, min(start_ms, duration_ms))
        normalized.append(
            Chapter(
                timestamp_ms=int(round(start_ms)),
                name=_normalize_chapter_title(str(raw_title), index),
                auto_detected=True,
            )
        )

    if not normalized:
        return [Chapter(timestamp_ms=0, name="Overview", auto_detected=True)]

    normalized.sort(key=lambda chapter: chapter.timestamp_ms)
    normalized[0] = replace(normalized[0], timestamp_ms=0)
    min_gap_ms = _chapter_min_gap_ms(duration_ms)
    _, max_chapters = _chapter_count_bounds(duration_ms)
    filtered: list[Chapter] = [normalized[0]]
    for chapter in normalized[1:]:
        if len(filtered) >= max_chapters:
            break
        if chapter.timestamp_ms - filtered[-1].timestamp_ms < min_gap_ms:
            continue
        filtered.append(chapter)
    return filtered


def _build_chapter_user_content(
    knowledge: SharedRecordingKnowledge,
    duration_ms: float,
) -> List[dict[str, Any]]:
    """Build a single-pass multimodal chapter-generation payload."""
    min_chapters, max_chapters = _chapter_count_bounds(duration_ms)
    section_plan = _format_narration_section_plan(_build_narration_timing_targets(duration_ms))
    frames = _limit_extracted_narration_frames(knowledge.frames)
    intro = _CHAPTER_USER_PROMPT.format(
        duration=duration_ms / 1000.0,
        min_chapters=min_chapters,
        max_chapters=max_chapters,
        section_plan=section_plan,
        summary=knowledge.summary,
        moments=_format_narration_moments_text(knowledge.activity_moments),
    )
    content: List[dict[str, Any]] = [{"type": "text", "text": intro}]
    content.append({
        "type": "text",
        "text": (
            "Frame pack follows in chronological order. Use the screenshots as shared evidence for "
            "the transitions that matter, not as a chapter-per-frame checklist."
        ),
    })
    for idx, frame in enumerate(frames, start=1):
        content.append({
            "type": "text",
            "text": (
                f"Frame {idx} — {_format_time_label(frame.timestamp_ms)} — "
                f"{frame.label}. {frame.reason}."
            ),
        })
        content.append({
            "type": "image_url",
            "image_url": {"url": frame.data_url, "detail": "low"},
        })
    return content


def _build_chapter_synthesis_user_prompt(
    knowledge: SharedRecordingKnowledge,
    duration_ms: float,
) -> str:
    """Build a text-only chapter prompt from shared slice analyses."""
    min_chapters, max_chapters = _chapter_count_bounds(duration_ms)
    section_plan = _format_narration_section_plan(_build_narration_timing_targets(duration_ms))
    slice_notes = "\n\n".join(
        (
            f"### Slice {note.batch_index} "
            f"({_format_time_label(note.start_ms)} → {_format_time_label(note.end_ms)}, "
            f"{note.frame_count} frames)\n"
            f"{note.markdown}"
        )
        for note in knowledge.batch_notes
    )
    return _CHAPTER_SYNTHESIS_USER_PROMPT.format(
        duration=duration_ms / 1000.0,
        min_chapters=min_chapters,
        max_chapters=max_chapters,
        section_plan=section_plan,
        summary=knowledge.summary,
        moments=_format_narration_moments_text(knowledge.activity_moments),
        slice_notes=slice_notes,
    )


def _generate_chapters_from_knowledge(
    settings: AISettings,
    knowledge: SharedRecordingKnowledge,
    duration_ms: float,
    status_callback: Optional[Callable[[str], None]] = None,
) -> List[Chapter]:
    """Generate AI chapters from the shared narration/chapter evidence."""
    if knowledge.batch_notes:
        if status_callback:
            status_callback("Synthesizing AI chapters from shared recording context…")
        prompt = _build_chapter_synthesis_user_prompt(knowledge, duration_ms)
        logger.info(
            "Calling AI for chapter synthesis after reusing %d shared batch analyses",
            len(knowledge.batch_notes),
        )
        response = _call_narration_chat(settings, _CHAPTER_SYSTEM_PROMPT, prompt)
        return _parse_chapters_response(response, duration_ms)

    prompt = _build_chapter_user_content(knowledge, duration_ms)
    logger.info(
        "Calling AI for chapter generation (%d frames, shared single pass)",
        len(knowledge.frames),
    )
    response = _call_narration_chat(settings, _CHAPTER_SYSTEM_PROMPT, prompt)
    return _parse_chapters_response(response, duration_ms)


def _default_narration_script_path(video_path: str) -> str:
    """Derive the narration markdown path from the current video path."""
    if not video_path:
        raise ValueError("Narration generation requires a video path.")
    root, _ = os.path.splitext(video_path)
    return f"{root}_voiceover.md"


def _write_narration_script(markdown_script: str, script_path: str) -> str:
    """Persist the generated narration markdown to disk."""
    directory = os.path.dirname(script_path)
    if directory:
        os.makedirs(directory, exist_ok=True)
    with open(script_path, "w", encoding="utf-8") as handle:
        handle.write(markdown_script)
        if not markdown_script.endswith("\n"):
            handle.write("\n")
    logger.info("Narration script saved: %s", script_path)
    return script_path


def _probe_wav_duration_ms(path: str) -> float:
    """Measure a synthesized WAV file with the standard library."""
    import wave

    try:
        with wave.open(path, "rb") as wav_file:
            frame_count = wav_file.getnframes()
            frame_rate = wav_file.getframerate()
            if frame_rate <= 0:
                return 0.0
            return (frame_count / frame_rate) * 1000.0
    except (OSError, wave.Error):
        logger.warning("Could not probe WAV duration for %s", path)
        return 0.0


def _narration_segment_audio_tolerance_ms(target_duration_ms: float) -> float:
    """Return an acceptable timing mismatch for one spoken narration segment."""
    return max(
        _NARRATION_TTS_SEGMENT_TOLERANCE_MS,
        target_duration_ms * _NARRATION_TTS_SEGMENT_TOLERANCE_RATIO,
    )


def _narration_total_audio_tolerance_ms(duration_ms: float) -> float:
    """Return an acceptable total narration-vs-video duration mismatch."""
    return max(
        _NARRATION_TTS_TOTAL_TOLERANCE_MS,
        duration_ms * _NARRATION_TTS_TOTAL_TOLERANCE_RATIO,
    )


def _narration_rate_bounds(base_rate: float) -> tuple[float, float]:
    """Return the subtle TTS-rate adjustment bounds around the caller's base rate."""
    base_rate = max(0.1, base_rate)
    return (
        max(0.5, base_rate * (1.0 - _NARRATION_TTS_RATE_NUDGE_RATIO)),
        min(3.0, base_rate * (1.0 + _NARRATION_TTS_RATE_NUDGE_RATIO)),
    )


def _suggest_narration_segment_rate(
    text: str,
    target_duration_ms: float,
    base_rate: float = 1.0,
) -> float:
    """Choose a small TTS-rate nudge that better fits the section timing window."""
    if target_duration_ms <= 0:
        return max(0.1, base_rate)

    word_count = _count_spoken_words(text)
    if word_count <= 0:
        return max(0.1, base_rate)

    estimated_duration_ms = (word_count / _NARRATION_WORDS_PER_SECOND) * 1000.0
    suggested_rate = base_rate * (estimated_duration_ms / max(target_duration_ms, 1.0))
    min_rate, max_rate = _narration_rate_bounds(base_rate)
    return max(min_rate, min(suggested_rate, max_rate))


def _corrected_narration_retry_rate(
    chosen_rate: float,
    measured_duration_ms: float,
    target_duration_ms: float,
    base_rate: float = 1.0,
) -> Optional[float]:
    """Return a bounded one-shot retry rate when measured TTS misses its target window."""
    if target_duration_ms <= 0 or measured_duration_ms <= 0:
        return None

    tolerance_ms = _narration_segment_audio_tolerance_ms(target_duration_ms)
    if abs(measured_duration_ms - target_duration_ms) <= tolerance_ms:
        return None

    min_rate, max_rate = _narration_rate_bounds(base_rate)
    retry_rate = chosen_rate * (measured_duration_ms / max(target_duration_ms, 1.0))
    retry_rate = max(min_rate, min(retry_rate, max_rate))
    if abs(retry_rate - chosen_rate) < _NARRATION_TTS_RATE_RETRY_THRESHOLD:
        return None
    return retry_rate


def _synthesize_narration_segment_audio(
    settings: AISettings,
    text: str,
    output_path: str,
    target_duration_ms: float,
    base_rate: float = 1.0,
    preferred_rate: Optional[float] = None,
    volume: float = 1.0,
) -> tuple[str, float, float]:
    """Synthesize one narration segment and retry once with a corrected rate if needed."""
    min_rate, max_rate = _narration_rate_bounds(base_rate)
    chosen_rate = preferred_rate if preferred_rate is not None else _suggest_narration_segment_rate(
        text,
        target_duration_ms,
        base_rate=base_rate,
    )
    chosen_rate = max(min_rate, min(chosen_rate, max_rate))
    audio_path = synthesize_speech(
        settings,
        text,
        output_path,
        rate=chosen_rate,
        volume=volume,
    )
    measured_duration_ms = _probe_wav_duration_ms(audio_path)
    if target_duration_ms <= 0 or measured_duration_ms <= 0:
        return audio_path, measured_duration_ms, chosen_rate

    retry_rate = _corrected_narration_retry_rate(
        chosen_rate,
        measured_duration_ms,
        target_duration_ms,
        base_rate=base_rate,
    )
    if retry_rate is None:
        return audio_path, measured_duration_ms, chosen_rate

    audio_path = synthesize_speech(
        settings,
        text,
        output_path,
        rate=retry_rate,
        volume=volume,
    )
    return audio_path, _probe_wav_duration_ms(audio_path), retry_rate


def replace_generated_narration_segments(
    segments: Optional[List[VoiceoverSegment]],
    generated_segments: List[VoiceoverSegment],
) -> List[VoiceoverSegment]:
    """Replace any existing generated narration while keeping manual voiceovers."""
    if any(not segment.is_generated_narration for segment in generated_segments):
        raise ValueError("Expected generated narration segments.")

    updated = [seg for seg in (segments or []) if not seg.is_generated_narration]
    updated.extend(generated_segments)
    updated.sort(key=lambda seg: seg.timestamp)
    return updated


def replace_generated_narration_segment(
    segments: Optional[List[VoiceoverSegment]],
    generated_segment: VoiceoverSegment,
) -> List[VoiceoverSegment]:
    """Backward-compatible wrapper for single-segment generated narration."""
    return replace_generated_narration_segments(segments, [generated_segment])


def generate_chapters(
    settings: AISettings,
    video_path: str,
    mouse_track: List[MousePosition],
    monitor_rect: dict,
    duration_ms: float,
    key_events: Optional[List[KeyEvent]] = None,
    click_events: Optional[List[ClickEvent]] = None,
    zoom_keyframes: Optional[List[ZoomKeyframe]] = None,
    annotations: Optional[AnnotationCollection] = None,
    frame_timestamps: Optional[List[float]] = None,
    status_callback: Optional[Callable[[str], None]] = None,
) -> List[Chapter]:
    """Generate AI chapter markers from the shared narration/chapter knowledge.

    ``key_events`` and ``annotations`` are accepted for backward-compatible
    call sites but ignored.
    """
    if not settings.narration_configured:
        raise ValueError("AI chat model is not configured.")

    knowledge = _build_shared_recording_knowledge(
        settings,
        video_path,
        mouse_track,
        monitor_rect,
        duration_ms,
        key_events=key_events,
        click_events=click_events,
        zoom_keyframes=zoom_keyframes,
        annotations=annotations,
        frame_timestamps=frame_timestamps,
        status_callback=status_callback,
    )
    chapters = _generate_chapters_from_knowledge(
        settings,
        knowledge,
        duration_ms,
        status_callback=status_callback,
    )
    logger.info(
        "AI chapters generated: %d markers from %d shared cues",
        len(chapters),
        len(knowledge.activity_moments),
    )
    return chapters


# ── Activity summarizer ─────────────────────────────────────────────


def _summarize_activity(
    mouse_track: List[MousePosition],
    key_events: Optional[List[KeyEvent]],
    click_events: Optional[List[ClickEvent]],
    monitor_rect: dict,
    duration_ms: float,
    window_ms: float = 1000.0,
) -> str:
    """Create a compact text summary of recording activity for the AI prompt.

    Breaks the recording into time windows and summarizes mouse position,
    speed, and click positions for each window. ``key_events`` is retained for
    backward-compatible call sites but ignored now that the keystroke feature
    has been removed. Empty windows are skipped to keep the prompt compact.
    """
    mon_left = monitor_rect.get("left", 0)
    mon_top = monitor_rect.get("top", 0)
    mon_w = max(monitor_rect.get("width", 1), 1)
    mon_h = max(monitor_rect.get("height", 1), 1)

    ignored_key_count = len(key_events or [])
    if ignored_key_count:
        logger.debug("Ignoring removed keystroke activity in AI summary")
    clicks = click_events or []
    n_windows = max(1, int(duration_ms / window_ms))

    lines: list[str] = [
        f"Recording duration: {duration_ms / 1000:.1f} seconds",
        f"Screen area: {mon_w}x{mon_h} pixels",
        f"Total: {len(mouse_track)} mouse samples, {len(clicks)} clicks",
        "",
        "Activity timeline (per-second windows):",
    ]

    for wi in range(n_windows):
        t_start = wi * window_ms
        t_end = t_start + window_ms

        window_mouse = [m for m in mouse_track if t_start <= m.timestamp < t_end]
        window_clicks = [c for c in clicks if t_start <= c.timestamp < t_end]

        if not window_mouse and not window_clicks:
            continue

        if window_mouse:
            avg_x = sum(m.x for m in window_mouse) / len(window_mouse)
            avg_y = sum(m.y for m in window_mouse) / len(window_mouse)
            nx = max(0.0, min(1.0, (avg_x - mon_left) / mon_w))
            ny = max(0.0, min(1.0, (avg_y - mon_top) / mon_h))

            if len(window_mouse) > 1:
                total_dist = sum(
                    math.sqrt(
                        (window_mouse[i].x - window_mouse[i - 1].x) ** 2
                        + (window_mouse[i].y - window_mouse[i - 1].y) ** 2
                    )
                    for i in range(1, len(window_mouse))
                )
                speed = "fast" if total_dist > 200 else ("medium" if total_dist > 50 else "slow")
            else:
                speed = "still"
        elif window_clicks:
            avg_x = sum(c.x for c in window_clicks) / len(window_clicks)
            avg_y = sum(c.y for c in window_clicks) / len(window_clicks)
            nx = max(0.0, min(1.0, (avg_x - mon_left) / mon_w))
            ny = max(0.0, min(1.0, (avg_y - mon_top) / mon_h))
            speed = "no data"
        else:
            nx, ny = 0.5, 0.5
            speed = "no data"

        parts = [f"t={wi}s: mouse=({nx:.2f},{ny:.2f}) speed={speed}"]
        if window_clicks:
            click_positions = ", ".join(
                f"({max(0.0, min(1.0, (c.x - mon_left) / mon_w)):.2f},"
                f"{max(0.0, min(1.0, (c.y - mon_top) / mon_h)):.2f})"
                for c in window_clicks
            )
            parts.append(f"clicks={len(window_clicks)} at [{click_positions}]")

        lines.append(" | ".join(parts))

    return "\n".join(lines)

# ── Chat API ────────────────────────────────────────────────────────

# Azure OpenAI (Cognitive Services) endpoints contain these hosts.
_AZURE_OPENAI_HOSTS = ("cognitiveservices.azure.com", "openai.azure.com")
_AZURE_OPENAI_API_VERSION = "2024-05-01-preview"


def _build_chat_url(endpoint: str, model: str) -> str:
    """Build the chat completions URL for the given endpoint type.

    Handles three input styles:

    1. **Full deployment URL** — user pasted an Azure OpenAI URL that
       contains ``/openai/deployments/``.  The base URL is extracted
       (everything before ``/openai/deployments/``) and the *model*
       parameter is used as the deployment name — the deployment in
       the pasted URL is ignored so that the Chat Model field controls
       which model is actually called.
    2. **Azure OpenAI base URL** — hostname contains
       ``cognitiveservices.azure.com`` or ``openai.azure.com``.
       Constructs ``{base}/openai/deployments/{model}/chat/completions``.
    3. **Generic inference endpoint** — GitHub Models, Azure AI Foundry.
       Constructs ``{base}/chat/completions``.
    """
    base = endpoint.rstrip("/")

    # Case 1: user pasted a full deployment URL — extract the base
    if "/openai/deployments/" in base:
        base = base[: base.index("/openai/deployments/")]

    # Case 2: Azure OpenAI base URL (original or extracted from Case 1)
    if any(host in base.lower() for host in _AZURE_OPENAI_HOSTS):
        return (
            f"{base}/openai/deployments/{model}"
            f"/chat/completions?api-version={_AZURE_OPENAI_API_VERSION}"
        )

    # Case 3: generic endpoint
    return f"{base}/chat/completions"


def _call_chat(
    settings: AISettings,
    system_prompt: str,
    user_prompt: str | List[dict[str, Any]],
) -> str:
    """Call chat completions via REST with retry for transient errors.

    Validates the response structure before accessing nested keys.
    Retries up to 3 times with exponential backoff for 429/500/503.
    """
    import time as _time

    # Validate endpoint uses HTTPS to prevent sending API key over plaintext
    if not settings.endpoint.lower().startswith("https://"):
        raise RuntimeError(
            "AI endpoint must use HTTPS to protect your API key. "
            f"Got: {settings.endpoint[:50]}"
        )

    url = _build_chat_url(settings.endpoint, settings.chat_model)
    logger.info("Chat API URL: %s", url)

    body = json.dumps({
        "model": settings.chat_model,
        "messages": [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt},
        ],
    }).encode("utf-8")

    req_headers = {
        "Content-Type": "application/json",
        "api-key": settings.api_key,
    }

    _MAX_RETRIES = 3
    _RETRY_CODES = {429, 500, 503}

    last_exc: Exception | None = None
    for attempt in range(_MAX_RETRIES):
        req = urllib.request.Request(
            url, data=body, headers=req_headers, method="POST",
        )
        try:
            with urllib.request.urlopen(req, timeout=120) as resp:
                data = json.loads(resp.read().decode("utf-8"))
        except urllib.error.HTTPError as exc:
            error_body = exc.read().decode(errors="replace")[:500]
            if exc.code in _RETRY_CODES and attempt < _MAX_RETRIES - 1:
                wait = 2 ** attempt
                logger.warning(
                    "Chat API %s (attempt %d/%d), retrying in %ds: %s",
                    exc.code, attempt + 1, _MAX_RETRIES, wait, error_body[:200],
                )
                _time.sleep(wait)
                last_exc = exc
                continue
            logger.error("Chat API error %s: %s", exc.code, error_body)
            raise RuntimeError(f"Chat API error ({exc.code}): {error_body}") from exc
        except urllib.error.URLError as exc:
            if attempt < _MAX_RETRIES - 1:
                wait = 2 ** attempt
                logger.warning(
                    "Chat API connection error (attempt %d/%d), retrying in %ds: %s",
                    attempt + 1, _MAX_RETRIES, wait, exc.reason,
                )
                _time.sleep(wait)
                last_exc = exc
                continue
            raise RuntimeError(f"Chat API connection error: {exc.reason}") from exc
        else:
            # Validate response structure
            if not isinstance(data, dict):
                raise RuntimeError("Chat API returned unexpected response format")
            choices = data.get("choices")
            if not choices or not isinstance(choices, list):
                raise RuntimeError("Chat API returned no choices")
            message = choices[0].get("message") if isinstance(choices[0], dict) else None
            if not message or not isinstance(message, dict):
                raise RuntimeError("Chat API response missing message")
            content = message.get("content", "")
            if not content:
                raise RuntimeError("Chat API returned empty content")
            return content

    raise RuntimeError(f"Chat API failed after {_MAX_RETRIES} retries") from last_exc


# ── Zoom analysis ──────────────────────────────────────────────────


def _parse_zoom_response(
    response_text: str,
    zoom_level: float,
    duration_ms: float,
) -> List[ZoomKeyframe]:
    """Parse AI response JSON into zoom-in / zoom-out keyframe pairs."""
    text = response_text.strip()
    if text.startswith("```"):
        text = text.split("\n", 1)[1] if "\n" in text else text[3:]
    if text.endswith("```"):
        text = text[:-3]
    text = text.strip()

    try:
        sections = json.loads(text)
    except json.JSONDecodeError as exc:
        logger.error("Failed to parse AI zoom response: %s\nRaw: %s", exc, text[:500])
        raise ValueError(f"AI returned invalid JSON: {exc}") from exc

    if not isinstance(sections, list):
        raise ValueError("AI response is not a JSON array")

    # Cap the number of sections to prevent a malicious/confused LLM
    # from returning thousands of keyframes that cause memory pressure.
    _MAX_AI_SECTIONS = 50
    if len(sections) > _MAX_AI_SECTIONS:
        logger.warning(
            "AI returned %d sections, capping to %d", len(sections), _MAX_AI_SECTIONS
        )
        sections = sections[:_MAX_AI_SECTIONS]

    transition_ms = 600.0
    keyframes: List[ZoomKeyframe] = []

    for section in sections:
        start_ms = float(section.get("start_ms", 0))
        x = max(0.0, min(1.0, float(section.get("x", 0.5))))
        y = max(0.0, min(1.0, float(section.get("y", 0.5))))
        zoom = max(1.1, min(3.0, float(section.get("zoom", zoom_level))))
        hold_ms = float(section.get("hold_ms", 2000))
        reason = str(section.get("reason", "AI-detected activity"))
        pan_points = section.get("pan_points", [])

        # Cap pan points per section to prevent memory exhaustion
        _MAX_PAN_POINTS = 20
        if isinstance(pan_points, list) and len(pan_points) > _MAX_PAN_POINTS:
            logger.warning(
                "Section has %d pan points, capping to %d",
                len(pan_points), _MAX_PAN_POINTS,
            )
            pan_points = pan_points[:_MAX_PAN_POINTS]

        # Clamp viewport to stay within source bounds
        half_vw = 0.5 / zoom
        half_vh = 0.5 / zoom
        x = max(half_vw, min(1.0 - half_vw, x))
        y = max(half_vh, min(1.0 - half_vh, y))

        # Zoom-in keyframe (arrive before the action)
        zoom_in_time = max(0.0, start_ms - transition_ms - 100.0)
        kf_in = ZoomKeyframe.create(
            timestamp=zoom_in_time,
            zoom=zoom,
            x=x,
            y=y,
            duration=transition_ms,
            reason=f"AI: {reason}",
        )
        keyframes.append(kf_in)

        # Pan-point keyframes (same zoom, different position)
        if isinstance(pan_points, list):
            prev_x, prev_y = x, y
            for pp in pan_points:
                if not isinstance(pp, dict):
                    continue
                pp_t = float(pp.get("t_ms", 0))
                pp_x = max(0.0, min(1.0, float(pp.get("x", 0.5))))
                pp_y = max(0.0, min(1.0, float(pp.get("y", 0.5))))
                # Clamp to viewport bounds
                pp_x = max(half_vw, min(1.0 - half_vw, pp_x))
                pp_y = max(half_vh, min(1.0 - half_vh, pp_y))
                # Compute pan duration proportional to distance
                dx = pp_x - prev_x
                dy = pp_y - prev_y
                dist = (dx * dx + dy * dy) ** 0.5
                pan_dur = min(700.0, max(400.0, dist * 1200.0))
                kf_pan = ZoomKeyframe.create(
                    timestamp=max(zoom_in_time + transition_ms, pp_t - pan_dur),
                    zoom=zoom,
                    x=pp_x,
                    y=pp_y,
                    duration=pan_dur,
                    reason=f"AI pan: {reason}",
                )
                keyframes.append(kf_pan)
                prev_x, prev_y = pp_x, pp_y

        # Zoom-out keyframe — ensure the transition completes before
        # the video ends so the zoom-out animation isn't truncated.
        zoom_out_dur = transition_ms * 2
        zoom_out_time = start_ms + hold_ms
        # Clamp so zoom-out start + transition doesn't exceed duration
        max_zoom_out_start = duration_ms - zoom_out_dur
        zoom_out_time = max(0.0, min(zoom_out_time, max_zoom_out_start))
        kf_out = ZoomKeyframe.create(
            timestamp=zoom_out_time,
            zoom=1.0,
            x=0.5,
            y=0.5,
            duration=zoom_out_dur,
            reason=f"AI zoom-out: {reason}",
        )
        keyframes.append(kf_out)

    keyframes.sort(key=lambda k: k.timestamp)

    # ── Prevent overlapping zoom sections ───────────────────────────
    # Find zoom-in/zoom-out pairs and ensure each zoom-out completes
    # before the next zoom-in starts.
    segments: list[tuple[int, int]] = []
    seg_start: int | None = None
    for idx, kf in enumerate(keyframes):
        if kf.zoom > 1.01 and seg_start is None:
            seg_start = idx
        elif kf.zoom <= 1.01 and seg_start is not None:
            segments.append((seg_start, idx))
            seg_start = None

    for s_idx in range(len(segments) - 1):
        _, out_idx = segments[s_idx]
        next_in_idx, _ = segments[s_idx + 1]
        out_kf = keyframes[out_idx]
        in_kf = keyframes[next_in_idx]
        out_end = out_kf.timestamp + out_kf.duration
        if out_end > in_kf.timestamp:
            available = in_kf.timestamp - out_kf.timestamp
            if available > 100:
                keyframes[out_idx] = ZoomKeyframe.create(
                    timestamp=out_kf.timestamp,
                    zoom=out_kf.zoom,
                    x=out_kf.x,
                    y=out_kf.y,
                    duration=max(100, int(available) - 50),
                    reason=out_kf.reason,
                )
            else:
                keyframes[next_in_idx] = ZoomKeyframe.create(
                    timestamp=out_end + 50,
                    zoom=in_kf.zoom,
                    x=in_kf.x,
                    y=in_kf.y,
                    duration=in_kf.duration,
                    reason=in_kf.reason,
                )

    return keyframes


def analyze_activity_with_ai(
    settings: AISettings,
    mouse_track: List[MousePosition],
    monitor_rect: dict,
    duration_ms: float,
    key_events: Optional[List[KeyEvent]] = None,
    click_events: Optional[List[ClickEvent]] = None,
    max_clusters: int = 6,
    zoom_level: float = 1.5,
    min_gap_ms: int = 4000,
) -> List[ZoomKeyframe]:
    """Analyze recording activity with AI to generate zoom keyframes.

    Same return type as ``activity_analyzer.analyze_activity()`` so results
    can be used interchangeably by the zoom engine.
    """
    if not settings.chat_configured:
        raise ValueError(
            "AI chat model is not configured.\n"
            "Set endpoint, API key, and model name in AI Settings."
        )

    summary = _summarize_activity(
        mouse_track, key_events, click_events, monitor_rect, duration_ms,
    )

    user_prompt = _ZOOM_USER_PROMPT.format(
        max_clusters=max_clusters,
        summary=summary,
        max_zoom=min(zoom_level + 0.5, 3.0),
        min_gap_ms=min_gap_ms,
    )

    logger.info("Calling AI for zoom analysis (max_clusters=%d)", max_clusters)
    system_prompt = _ZOOM_SYSTEM_PROMPT.format(min_gap_ms=min_gap_ms)
    response = _call_chat(settings, system_prompt, user_prompt)
    logger.info("AI zoom response length: %d chars", len(response))

    keyframes = _parse_zoom_response(response, zoom_level, duration_ms)
    logger.info("AI generated %d keyframes", len(keyframes))
    return keyframes


# ── Narration ───────────────────────────────────────────────────────


def generate_narration(
    settings: AISettings,
    video_path: str,
    mouse_track: List[MousePosition],
    monitor_rect: dict,
    duration_ms: float,
    key_events: Optional[List[KeyEvent]] = None,
    click_events: Optional[List[ClickEvent]] = None,
    zoom_keyframes: Optional[List[ZoomKeyframe]] = None,
    annotations: Optional[AnnotationCollection] = None,
    frame_timestamps: Optional[List[float]] = None,
    voice: Optional[str] = None,
    rate: float = 1.0,
    volume: float = 1.0,
    script_output_path: Optional[str] = None,
    audio_output_path: Optional[str] = None,
    synthesize_audio: bool = False,
    guidance_prompt: Optional[str] = None,
    status_callback: Optional[Callable[[str], None]] = None,
) -> GeneratedNarration:
    """Generate timestamped narration segments and optional direct TTS audio.

    ``key_events`` and ``annotations`` are accepted for backward-compatible
    call sites but ignored.
    """
    if not settings.narration_configured:
        raise ValueError("AI chat model is not configured.")
    if synthesize_audio and not settings.tts_configured:
        raise ValueError(
            "TTS is not configured.\n"
            "Set endpoint and API key in AI Settings."
        )

    knowledge = _build_shared_recording_knowledge(
        settings,
        video_path,
        mouse_track,
        monitor_rect,
        duration_ms,
        key_events=key_events,
        click_events=click_events,
        zoom_keyframes=zoom_keyframes,
        annotations=annotations,
        frame_timestamps=frame_timestamps,
        status_callback=status_callback,
    )
    narration_segments = _generate_narration_segments(
        settings,
        knowledge,
        duration_ms,
        guidance=guidance_prompt or "",
        status_callback=status_callback,
    )
    markdown_script = _build_narration_markdown_script(narration_segments)
    tts_text = _combine_narration_tts_text(narration_segments)
    if not markdown_script:
        raise RuntimeError("AI narration response was empty.")
    if not tts_text:
        raise RuntimeError("AI narration response produced no spoken text.")

    timing_targets = _build_segment_timing_targets_from_segments(narration_segments, duration_ms)
    target_words = sum(target.target_words for target in timing_targets)
    actual_words = _count_spoken_words(tts_text)
    total_lower, total_upper = _narration_word_bounds(target_words)
    if actual_words < total_lower or actual_words > total_upper:
        logger.warning(
            "Narration pacing drifted from target: target=%d words, actual=%d words",
            target_words,
            actual_words,
        )

    script_path = _write_narration_script(
        markdown_script,
        script_output_path or _default_narration_script_path(video_path),
    )

    selected_voice = voice or settings.tts_voice
    voiceover_segments: list[VoiceoverSegment] = []
    for index, narration_segment in enumerate(narration_segments, start=1):
        timing_target = timing_targets[index - 1]
        suggested_rate = _suggest_narration_segment_rate(
            narration_segment.narration,
            timing_target.duration_ms,
            base_rate=rate,
        )
        voiceover_segments.append(
            VoiceoverSegment.create(
                timestamp=narration_segment.start_ms,
                text=narration_segment.narration.strip(),
                voice=selected_voice,
                rate=suggested_rate,
                volume=volume,
                source="generated",
                script_markdown=_build_segment_markdown(narration_segment),
                script_path=script_path,
            )
        )

    if synthesize_audio:
        tts_settings = AISettings(
            endpoint=settings.endpoint,
            api_key=settings.api_key,
            chat_model=settings.chat_model,
            narration_model=settings.narration_model,
            tts_voice=selected_voice,
        )
        base_script_path = script_path
        if audio_output_path:
            audio_root, audio_ext = os.path.splitext(audio_output_path)
            base_script_path = f"{audio_root}.md" if audio_ext.lower() == ".wav" else audio_output_path
        for index, narration_segment in enumerate(narration_segments, start=1):
            segment = voiceover_segments[index - 1]
            current_target_duration_ms = _generated_narration_duration_map(
                voiceover_segments,
                duration_ms,
            ).get(segment.id, 0.0)
            segment.audio_path, segment.duration_ms, segment.rate = _synthesize_narration_segment_audio(
                tts_settings,
                segment.text,
                _build_narration_segment_audio_path(
                    base_script_path,
                    index,
                    narration_segment.section,
                ),
                target_duration_ms=current_target_duration_ms,
                base_rate=rate,
                preferred_rate=segment.rate,
                volume=segment.volume,
            )
            voiceover_segments = _normalize_generated_narration_voiceover_segments(
                voiceover_segments,
                video_duration_ms=duration_ms,
            )
        spoken_ends = [
            segment.timestamp + segment.duration_ms
            for segment in voiceover_segments
            if segment.duration_ms > 0
        ]
        spoken_end_ms = max(spoken_ends) if spoken_ends else 0.0
        total_tolerance_ms = _narration_total_audio_tolerance_ms(duration_ms)
        drift_ms = duration_ms - spoken_end_ms
        if abs(drift_ms) > total_tolerance_ms:
            logger.warning(
                "Narration audio still ends %.1fs %s the video (tolerance %.1fs)",
                abs(drift_ms) / 1000.0,
                "before" if drift_ms > 0 else "after",
                total_tolerance_ms / 1000.0,
            )

    logger.info(
        "AI narration generated: %d chars markdown, %d spoken words across %d segments",
        len(markdown_script),
        actual_words,
        len(voiceover_segments),
    )
    return GeneratedNarration(
        markdown_script=markdown_script,
        tts_text=tts_text,
        script_path=script_path,
        voiceover_segments=voiceover_segments,
        sampled_timestamps_ms=[frame.timestamp_ms for frame in knowledge.frames],
        activity_moments=knowledge.activity_moments,
    )


# ── Text-to-Speech ──────────────────────────────────────────────────


def _extract_region(endpoint: str) -> str:
    """Extract the Azure region from a Cognitive Services endpoint URL.

    ``https://eastus.api.cognitive.microsoft.com`` → ``eastus``
    ``https://myresource.cognitiveservices.azure.com`` → extracts via REST
    Returns empty string if region cannot be determined.
    """
    import re
    # Regional endpoint: https://<region>.api.cognitive.microsoft.com
    m = re.match(r"https?://([^.]+)\.api\.cognitive\.microsoft\.com", endpoint)
    if m:
        return m.group(1)
    # Custom domain: https://<name>.cognitiveservices.azure.com
    # Try to get region from the resource — use a known region mapping
    # For now, try extracting from a HEAD request's headers
    try:
        req = urllib.request.Request(endpoint, method="HEAD")
        with urllib.request.urlopen(req, timeout=5) as resp:
            region = resp.headers.get("x-ms-region", "")
            if region:
                return region.lower()
    except Exception:
        pass
    return ""


def _build_speech_config(api_key: str, endpoint: str):
    """Create a SpeechConfig that works with both regional and custom-domain endpoints."""
    import azure.cognitiveservices.speech as speechsdk

    # Try to use region-based config for better compatibility with HD voices
    region = _extract_region(endpoint)
    if region:
        return speechsdk.SpeechConfig(subscription=api_key, region=region)

    # Fallback to endpoint-based config
    return speechsdk.SpeechConfig(subscription=api_key, endpoint=endpoint)


def synthesize_speech(
    settings: AISettings,
    text: str,
    output_path: str,
    rate: float = 1.0,
    volume: float = 1.0,
) -> str:
    """Convert text to speech via Azure Speech SDK.

    Uses SSML ``<prosody>`` to control speech rate and volume.
    Returns the path to the generated WAV file.
    """
    if not settings.tts_configured:
        raise ValueError(
            "TTS is not configured.\n"
            "Set endpoint and API key in AI Settings."
        )

    try:
        import azure.cognitiveservices.speech as speechsdk
    except ImportError:
        raise RuntimeError(
            "azure-cognitiveservices-speech package required for TTS.\n"
            "Install with: pip install azure-cognitiveservices-speech"
        )

    endpoint = settings.endpoint.rstrip("/")
    speech_config = _build_speech_config(settings.api_key, endpoint)
    speech_config.speech_synthesis_voice_name = settings.tts_voice

    if not output_path.lower().endswith(".wav"):
        output_path = output_path.rsplit(".", 1)[0] + ".wav" if "." in output_path else output_path + ".wav"

    audio_config = speechsdk.audio.AudioOutputConfig(filename=output_path)
    synthesizer = speechsdk.SpeechSynthesizer(
        speech_config=speech_config,
        audio_config=audio_config,
    )

    # Use plain text when rate and volume are at defaults
    use_ssml = abs(rate - 1.0) > 0.05 or abs(volume - 1.0) > 0.05

    if use_ssml:
        # Build SSML with prosody
        import html as _html
        # Rate: Azure SSML uses relative percentage (+0% = normal, -50% = half, +100% = double)
        rate_rel = int((rate - 1.0) * 100)
        rate_str = f"{rate_rel:+d}%"
        # Volume: relative percentage (+0% = normal)
        vol_rel = int((volume - 1.0) * 100)
        vol_str = f"{vol_rel:+d}%"
        ssml = (
            '<speak version="1.0" xmlns="http://www.w3.org/2001/10/synthesis" xml:lang="en-US">'
            f'<voice name="{_html.escape(settings.tts_voice)}">'
            f'<prosody rate="{rate_str}" volume="{vol_str}">'
            f'{_html.escape(text)}'
            '</prosody></voice></speak>'
        )
        logger.info("TTS SSML: rate=%s volume=%s", rate_str, vol_str)
        result = synthesizer.speak_ssml_async(ssml).get()
    else:
        result = synthesizer.speak_text_async(text).get()

    if result.reason == speechsdk.ResultReason.SynthesizingAudioCompleted:
        logger.info("TTS audio saved: %s", output_path)
        return output_path
    elif result.reason == speechsdk.ResultReason.Canceled:
        details = result.cancellation_details
        error_msg = f"Speech synthesis canceled: {details.reason}"
        if details.reason == speechsdk.CancellationReason.Error:
            error_msg += f" — {details.error_details}"
        logger.error(error_msg)
        raise RuntimeError(error_msg)
    else:
        raise RuntimeError(f"Unexpected TTS result: {result.reason}")


# ── Background worker ──────────────────────────────────────────────


class AIWorker(QThread):
    """Background thread for AI operations.

    Runs a single AI task (zoom analysis, chapters, narration, or TTS) and emits
    the result via the appropriate signal.  Keeps the GUI responsive
    during API calls.
    """

    zoom_result = Signal(list)  # List[ZoomKeyframe]
    chapters_result = Signal(list)  # List[Chapter]
    narration_result = Signal(object)  # GeneratedNarration
    tts_result = Signal(str, str)  # (segment_id, audio_file_path)
    error = Signal(str, str)  # (task "zoom"|"chapters"|"narration"|"tts", error message)
    status = Signal(str)  # progress text

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self._task: str = ""
        self._settings: Optional[AISettings] = None
        self._kwargs: dict = {}

    def run_zoom_analysis(self, settings: AISettings, **kwargs) -> None:
        """Start AI zoom analysis in background."""
        self._task = "zoom"
        self._settings = settings
        self._kwargs = kwargs
        self.start()

    def run_narration(self, settings: AISettings, **kwargs) -> None:
        """Start automated narration generation in background."""
        self._task = "narration"
        self._settings = settings
        self._kwargs = kwargs
        self.start()

    def run_chapters(self, settings: AISettings, **kwargs) -> None:
        """Start AI chapter generation in background."""
        self._task = "chapters"
        self._settings = settings
        self._kwargs = kwargs
        self.start()

    def run_tts(self, settings: AISettings, segment_id: str, text: str,
                output_path: str, rate: float = 1.0, volume: float = 1.0) -> None:
        """Start TTS synthesis in background for a specific voiceover segment."""
        self._task = "tts"
        self._settings = settings
        self._kwargs = {
            "segment_id": segment_id, "text": text,
            "output_path": output_path, "rate": rate, "volume": volume,
        }
        self.start()

    def run(self) -> None:  # noqa: D401
        try:
            if self._task == "zoom":
                self.status.emit("Analyzing activity with AI\u2026")
                result = analyze_activity_with_ai(self._settings, **self._kwargs)
                self.zoom_result.emit(result)
            elif self._task == "narration":
                self.status.emit("Generating GPT-5.4 voiceover segments\u2026")
                result = generate_narration(
                    self._settings,
                    status_callback=self.status.emit,
                    **self._kwargs,
                )
                self.narration_result.emit(result)
            elif self._task == "chapters":
                self.status.emit("Generating AI chapter markers\u2026")
                result = generate_chapters(
                    self._settings,
                    status_callback=self.status.emit,
                    **self._kwargs,
                )
                self.chapters_result.emit(result)
            elif self._task == "tts":
                seg_id = self._kwargs.pop("segment_id")
                self.status.emit("Synthesizing speech\u2026")
                result = synthesize_speech(self._settings, **self._kwargs)
                self.tts_result.emit(seg_id, result)
        except Exception as exc:
            logger.exception("AI operation failed: %s", self._task)
            self.error.emit(self._task, str(exc))
