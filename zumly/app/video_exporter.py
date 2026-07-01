import logging
import os
import subprocess
import threading
import time
import tempfile
import re
import bisect
from dataclasses import dataclass, replace
from typing import Any, List, Optional, Callable

from PIL import Image, ImageDraw, ImageFont

from .models import ZoomKeyframe, MousePosition, ClickEvent, HighlightBox, TimelineFrame, VideoSegment, VoiceoverSegment, ClickEffectPreset, DEFAULT_CLICK_EFFECT, Chapter
from .backgrounds import BackgroundPreset, DEFAULT_PRESET
from .frames import FramePreset, DEFAULT_FRAME
from .utils import ffmpeg_exe as _ffmpeg_exe, subprocess_kwargs as _subprocess_kwargs

logger = logging.getLogger(__name__)


@dataclass
class VideoProbeResult:
    src_fps: float
    total_frames: int
    src_w: int
    src_h: int
    out_w: int
    out_h: int
    fps: float
    is_gif: bool


@dataclass
class GeometryResult:
    scr_x: int
    scr_y: int
    scr_w: int
    scr_h: int
    base_canvas: Any
    screen_mask: Any
    device_mask_u8: Any
    bg: Any


class GeometryComputer:
    """Pure geometry helper shared by tests and the FFmpeg export graph."""

    def __init__(
        self,
        canvas_w: int,
        canvas_h: int,
        src_w: int,
        src_h: int,
        frame_preset: Optional[FramePreset] = None,
    ) -> None:
        self.canvas_w = int(canvas_w)
        self.canvas_h = int(canvas_h)
        self.src_w = max(int(src_w), 1)
        self.src_h = max(int(src_h), 1)
        self.frame_preset = frame_preset or DEFAULT_FRAME

    def compute(self) -> dict:
        W = max(self.canvas_w, 1)
        H = max(self.canvas_h, 1)
        video_aspect = self.src_w / self.src_h
        fp = self.frame_preset

        if fp.is_none:
            if W / H > video_aspect:
                scr_h = H
                scr_w = int(H * video_aspect)
            else:
                scr_w = W
                scr_h = int(W / video_aspect)
            return {
                "scr_x": (W - scr_w) // 2,
                "scr_y": (H - scr_h) // 2,
                "scr_w": max(scr_w, 1),
                "scr_h": max(scr_h, 1),
            }

        preliminary_scale = max((W - 2 * W * fp.padding) / 900.0, 0.01)
        bw_est = fp.bezel_width * preliminary_scale
        pad_x = W * fp.padding
        pad_y = H * fp.padding
        avail_w = max(W - 2 * pad_x, 1.0)
        avail_h = max(H - 2 * pad_y, 1.0)

        dev_h = avail_h
        scr_h_try = max(dev_h - 2 * bw_est, 1.0)
        scr_w_try = scr_h_try * video_aspect
        dev_w = scr_w_try + 2 * bw_est
        if dev_w > avail_w:
            dev_w = avail_w
            scr_w_try = max(dev_w - 2 * bw_est, 1.0)
            scr_h_try = scr_w_try / video_aspect
            dev_h = scr_h_try + 2 * bw_est

        dev_x = (W - dev_w) / 2
        dev_y = (H - dev_h) / 2
        scale = max(dev_w / 900.0, 0.01)
        bw = fp.bezel_width * scale

        scr_x = dev_x + bw
        scr_y = dev_y + bw
        scr_w = max(dev_w - 2 * bw, 1.0)
        scr_h = max(dev_h - 2 * bw, 1.0)

        return {
            "scr_x": int(scr_x),
            "scr_y": int(scr_y),
            "scr_w": max(int(scr_w), 1),
            "scr_h": max(int(scr_h), 1),
            "dev_x": int(dev_x),
            "dev_y": int(dev_y),
            "dev_w": max(int(dev_w), 1),
            "dev_h": max(int(dev_h), 1),
            "bw": int(round(bw)),
            "outer_r": int(round(fp.outer_radius * scale)),
            "inner_r": int(round(fp.inner_radius * scale)),
            "edge_thickness": max(int(round(fp.edge_width * scale)), 0),
        }


def generate_device_frame_png(preset: FramePreset, w: int, h: int, geom: dict) -> str:
    """Generate a device frame PNG and return the path."""
    img = Image.new("RGBA", (w, h), (0, 0, 0, 0))
    draw = ImageDraw.Draw(img)

    if preset and not preset.is_none and "dev_x" in geom:
        dev_box = [
            geom["dev_x"],
            geom["dev_y"],
            geom["dev_x"] + geom["dev_w"],
            geom["dev_y"] + geom["dev_h"],
        ]
        scr_box = [
            geom["scr_x"],
            geom["scr_y"],
            geom["scr_x"] + geom["scr_w"],
            geom["scr_y"] + geom["scr_h"],
        ]
        if preset.shadow_layers > 0:
            for layer in range(preset.shadow_layers, 0, -1):
                spread = layer * 4
                alpha = max(8, 34 - layer * 5)
                draw.rounded_rectangle(
                    [
                        dev_box[0] - spread,
                        dev_box[1] - spread,
                        dev_box[2] + spread,
                        dev_box[3] + spread,
                    ],
                    radius=geom["outer_r"] + spread,
                    fill=(0, 0, 0, alpha),
                )
        if geom["bw"] > 0:
            draw.rounded_rectangle(
                dev_box,
                radius=geom["outer_r"],
                fill=preset.bezel_color + (255,),
                outline=preset.edge_color + (255,),
                width=max(geom["edge_thickness"], 1),
            )
            draw.rounded_rectangle(
                scr_box,
                radius=geom["inner_r"],
                fill=(0, 0, 0, 0),
            )
        elif preset.shadow_layers > 0:
            draw.rounded_rectangle(
                scr_box,
                radius=geom["inner_r"],
                outline=preset.edge_color + (80,),
                width=max(geom["edge_thickness"], 1),
            )
    
    f = tempfile.NamedTemporaryFile(suffix=".png", delete=False)
    path = f.name
    f.close()
    img.save(path)
    return path

def generate_click_png(preset: ClickEffectPreset) -> str:
    """Generate a visible click marker PNG for FFmpeg overlay."""
    r = max(int(preset.radius), 12)
    d = max(1, r * 2)
    img = Image.new("RGBA", (d, d), (0, 0, 0, 0))
    draw = ImageDraw.Draw(img)
    color = preset.color
    style = preset.style if preset.style in ("ripple", "burst", "highlight") else "ripple"

    if style == "highlight":
        fill = (color[0], color[1], color[2], min(color[3], 120))
        draw.ellipse([1, 1, d - 2, d - 2], fill=fill, outline=color, width=max(2, r // 8))
    elif style == "burst":
        import math
        cx = cy = r
        for i in range(8):
            angle = 2.0 * math.pi * i / 8
            x1 = cx + math.cos(angle) * r * 0.35
            y1 = cy + math.sin(angle) * r * 0.35
            x2 = cx + math.cos(angle) * r * 0.95
            y2 = cy + math.sin(angle) * r * 0.95
            draw.line([x1, y1, x2, y2], fill=color, width=max(2, r // 8))
        draw.ellipse([r - 4, r - 4, r + 4, r + 4], fill=color)
    else:
        draw.ellipse([2, 2, d - 3, d - 3], outline=color, width=max(3, r // 6))
        draw.ellipse([r - 5, r - 5, r + 5, r + 5], fill=color)

    f = tempfile.NamedTemporaryFile(suffix=".png", delete=False)
    path = f.name
    f.close()
    img.save(path)
    return path


def generate_cursor_png() -> str:
    """Generate a high-contrast cursor marker PNG for click evidence overlays."""
    scale = 1.55
    pts = [
        (0, 0), (0, 17), (4, 13), (7, 20), (9, 19), (6, 12), (12, 12)
    ]
    pts = [(int(x * scale) + 4, int(y * scale) + 4) for x, y in pts]
    img = Image.new("RGBA", (28, 38), (0, 0, 0, 0))
    draw = ImageDraw.Draw(img)
    shadow = [(x + 2, y + 2) for x, y in pts]
    draw.polygon(shadow, fill=(0, 0, 0, 120))
    draw.line(shadow + [shadow[0]], fill=(0, 0, 0, 160), width=3)
    draw.polygon(pts, fill=(255, 255, 255, 255))
    draw.line(pts + [pts[0]], fill=(20, 20, 20, 255), width=2)

    f = tempfile.NamedTemporaryFile(suffix=".png", delete=False)
    path = f.name
    f.close()
    img.save(path)
    return path


def generate_highlight_png(highlight: HighlightBox, w: int, h: int, geom: dict) -> str:
    """Generate a timed spotlight overlay PNG for one highlight."""
    img = Image.new("RGBA", (w, h), (0, 0, 0, 0))
    draw = ImageDraw.Draw(img)

    scr_x = int(geom["scr_x"])
    scr_y = int(geom["scr_y"])
    scr_w = int(geom["scr_w"])
    scr_h = int(geom["scr_h"])
    dim_alpha = int(max(0.0, min(0.9, float(highlight.dim_opacity))) * 255)
    draw.rectangle(
        [scr_x, scr_y, scr_x + scr_w, scr_y + scr_h],
        fill=(0, 0, 0, dim_alpha),
    )

    hx = int(scr_x + max(0.0, min(1.0, float(highlight.x))) * scr_w)
    hy = int(scr_y + max(0.0, min(1.0, float(highlight.y))) * scr_h)
    hx = max(scr_x, min(scr_x + scr_w - 1, hx))
    hy = max(scr_y, min(scr_y + scr_h - 1, hy))
    hw = max(1, int(max(0.0, min(1.0, float(highlight.width))) * scr_w))
    hh = max(1, int(max(0.0, min(1.0, float(highlight.height))) * scr_h))
    box = [hx, hy, max(hx + 1, min(scr_x + scr_w, hx + hw)), max(hy + 1, min(scr_y + scr_h, hy + hh))]

    if getattr(highlight, "shape", "rect") == "circle":
        draw.ellipse(box, fill=(0, 0, 0, 0))
        draw.ellipse(
            box,
            outline=tuple(highlight.color[:3]) + (230,),
            width=max(2, int(highlight.border_width)),
        )
    else:
        radius = max(6, min(hw, hh) // 12)
        draw.rounded_rectangle(box, radius=radius, fill=(0, 0, 0, 0))
        draw.rounded_rectangle(
            box,
            radius=radius,
            outline=tuple(highlight.color[:3]) + (230,),
            width=max(2, int(highlight.border_width)),
        )

    f = tempfile.NamedTemporaryFile(suffix=".png", delete=False)
    path = f.name
    f.close()
    img.save(path)
    return path


def _parse_hex_color(value: str, fallback: tuple[int, int, int]) -> tuple[int, int, int]:
    text = (value or "").strip().lstrip("#")
    if len(text) != 6:
        return fallback
    try:
        return tuple(int(text[i:i + 2], 16) for i in (0, 2, 4))  # type: ignore[return-value]
    except ValueError:
        return fallback


def _load_frame_font(size: int) -> ImageFont.ImageFont:
    for font_name in ("segoeuib.ttf", "segoeui.ttf", "arial.ttf"):
        try:
            return ImageFont.truetype(font_name, size=max(12, int(size)))
        except OSError:
            continue
    return ImageFont.load_default()


def _wrap_text_for_width(text: str, font: ImageFont.ImageFont, max_width: int, draw: ImageDraw.ImageDraw) -> list[str]:
    lines: list[str] = []
    for paragraph in (text or "").splitlines() or [""]:
        words = paragraph.split()
        if not words:
            lines.append("")
            continue
        current = words[0]
        for word in words[1:]:
            candidate = f"{current} {word}"
            if draw.textbbox((0, 0), candidate, font=font)[2] <= max_width:
                current = candidate
            else:
                lines.append(current)
                current = word
        lines.append(current)
    return lines


def generate_timeline_frame_png(frame: TimelineFrame, w: int, h: int) -> str:
    """Generate a full-canvas PNG for an inserted text or picture frame."""
    bg = _parse_hex_color(frame.background_color, (17, 24, 39))
    canvas = Image.new("RGBA", (w, h), bg + (255,))

    if frame.kind == "image" and frame.image_path and os.path.isfile(frame.image_path):
        try:
            with Image.open(frame.image_path) as source:
                source = source.convert("RGBA")
                source.thumbnail((int(w * 0.92), int(h * 0.88)), Image.Resampling.LANCZOS)
                x = (w - source.width) // 2
                y = (h - source.height) // 2
                canvas.alpha_composite(source, (x, y))
        except Exception as exc:
            logger.warning("Failed to render image frame %s: %s", frame.image_path, exc)

    if frame.kind == "text" or not (frame.kind == "image" and frame.image_path and os.path.isfile(frame.image_path)):
        draw = ImageDraw.Draw(canvas)
        font = _load_frame_font(frame.font_size)
        text_color = _parse_hex_color(frame.text_color, (249, 250, 251))
        text = frame.text or "Add your text"
        max_width = int(w * 0.78)
        lines = _wrap_text_for_width(text, font, max_width, draw)
        line_boxes = [draw.textbbox((0, 0), line or " ", font=font) for line in lines]
        line_height = max((box[3] - box[1] for box in line_boxes), default=max(24, frame.font_size))
        total_height = len(lines) * line_height + max(0, len(lines) - 1) * 12
        y = max(40, (h - total_height) // 2)
        for line, box in zip(lines, line_boxes):
            width = box[2] - box[0]
            x = (w - width) // 2
            draw.text((x, y), line, font=font, fill=text_color + (255,))
            y += line_height + 12

    path = tempfile.NamedTemporaryFile(suffix="_timeline_frame.png", delete=False).name
    canvas.save(path)
    return path


def _segment_speed(segment: VideoSegment) -> float:
    """Return a bounded playback speed for export retiming."""
    try:
        speed = float(segment.speed)
    except (TypeError, ValueError):
        return 1.0
    if speed <= 0:
        return 1.0
    return min(speed, 10.0)


def _normalize_video_segments(
    video_segments: Optional[List[VideoSegment]],
    duration_ms: float,
    fill_gaps: bool = True,
) -> List[VideoSegment]:
    """Return source-time segments for export.

    With editor-authored segments, list order is the output order and gaps are
    real cuts. Legacy/no-segment payloads still get one full-duration segment.
    """
    duration_ms = max(float(duration_ms or 0.0), 0.0)
    if duration_ms <= 0:
        return []

    valid: List[VideoSegment] = []
    source_segments = list(video_segments or [])
    if fill_gaps:
        source_segments.sort(key=lambda s: float(s.start_ms))
    for segment in source_segments:
        start = max(0.0, min(float(segment.start_ms), duration_ms))
        end = max(0.0, min(float(segment.end_ms), duration_ms))
        if end <= start:
            continue
        valid.append(
            VideoSegment(
                id=segment.id,
                start_ms=start,
                end_ms=end,
                speed=_segment_speed(segment),
            )
        )

    if not valid or not fill_gaps:
        if valid:
            return valid
        return [VideoSegment.create(0.0, duration_ms, 1.0)]

    normalized: List[VideoSegment] = []
    cursor = 0.0
    for segment in valid:
        if segment.start_ms > cursor:
            normalized.append(VideoSegment.create(cursor, segment.start_ms, 1.0))
        start = max(segment.start_ms, cursor)
        if segment.end_ms > start:
            normalized.append(
                VideoSegment(
                    id=segment.id,
                    start_ms=start,
                    end_ms=segment.end_ms,
                    speed=segment.speed,
                )
            )
            cursor = segment.end_ms
    if cursor < duration_ms:
        normalized.append(VideoSegment.create(cursor, duration_ms, 1.0))
    return normalized


class _SessionMediaMapper:
    """Map recorder session timestamps onto the encoded MP4 media timeline."""

    def __init__(
        self,
        frame_timestamps: Optional[List[float]],
        media_duration_sec: float,
        fps: float,
    ) -> None:
        self._timestamps = sorted(float(ts) for ts in (frame_timestamps or []) if ts is not None)
        self._media_duration_sec = max(float(media_duration_sec or 0.0), 0.0)
        self._fps = max(float(fps or 0.0), 0.0)
        if self._timestamps and self._media_duration_sec > 0:
            self._frame_duration_sec = self._media_duration_sec / len(self._timestamps)
        elif self._fps > 0:
            self._frame_duration_sec = 1.0 / self._fps
        else:
            self._frame_duration_sec = 1.0 / 30.0

    @property
    def has_timestamps(self) -> bool:
        return bool(self._timestamps)

    def to_media_sec(self, session_time_ms: float, *, for_end: bool = False) -> float:
        if not self._timestamps:
            return max(float(session_time_ms), 0.0) / 1000.0

        target = float(session_time_ms)
        last_media_start = max(0.0, self._media_duration_sec - self._frame_duration_sec)
        if target <= self._timestamps[0]:
            return min(self._frame_duration_sec, self._media_duration_sec) if for_end else 0.0
        if target >= self._timestamps[-1]:
            return self._media_duration_sec if for_end else last_media_start

        idx = bisect.bisect_left(self._timestamps, target)
        if idx <= 0:
            frame_pos = 0.0
        elif idx >= len(self._timestamps):
            frame_pos = float(len(self._timestamps) if for_end else len(self._timestamps) - 1)
        else:
            prev_ts = self._timestamps[idx - 1]
            next_ts = self._timestamps[idx]
            if next_ts <= prev_ts:
                frame_pos = float(idx)
            else:
                ratio = (target - prev_ts) / (next_ts - prev_ts)
                frame_pos = float(idx - 1) + max(0.0, min(1.0, ratio))

        media_sec = frame_pos * self._frame_duration_sec
        return max(0.0, min(self._media_duration_sec, media_sec))

    def segment_bounds(self, segment: VideoSegment) -> tuple[float, float, float]:
        start_sec = self.to_media_sec(segment.start_ms, for_end=False)
        end_sec = self.to_media_sec(segment.end_ms, for_end=True)
        min_duration = max(self._frame_duration_sec, 0.001)
        if end_sec <= start_sec:
            end_sec = min(self._media_duration_sec or start_sec + min_duration, start_sec + min_duration)
        media_duration = max(end_sec - start_sec, min_duration)
        return start_sec, end_sec, media_duration


def _media_keyframes_for_segment(
    keyframes: List[ZoomKeyframe],
    segment: VideoSegment,
    mapper: _SessionMediaMapper,
    media_start_sec: float,
) -> List[ZoomKeyframe]:
    """Filter keyframes into a segment-local media timeline for FFmpeg trim."""
    media_keyframes: List[ZoomKeyframe] = []
    for keyframe in keyframes:
        keyframe_time = float(keyframe.timestamp)
        if not (segment.start_ms <= keyframe_time < segment.end_ms):
            continue
        media_time_ms = max(0.0, (mapper.to_media_sec(keyframe_time) - media_start_sec) * 1000.0)
        duration = max(float(keyframe.duration), 0.0)
        if duration > 0:
            keyframe_end = min(float(segment.end_ms), keyframe_time + duration)
            media_end_ms = max(0.0, (mapper.to_media_sec(keyframe_end, for_end=True) - media_start_sec) * 1000.0)
            duration = max(0.0, media_end_ms - media_time_ms)
        media_keyframes.append(replace(keyframe, timestamp=media_time_ms, duration=duration))
    return media_keyframes


def _media_time_for_segment(
    timestamp_ms: float,
    segment: VideoSegment,
    mapper: _SessionMediaMapper,
    media_start_sec: float,
    *,
    for_end: bool = False,
) -> float:
    """Map an absolute session timestamp to media-local seconds."""
    bounded = max(float(segment.start_ms), min(float(timestamp_ms), float(segment.end_ms)))
    return max(0.0, mapper.to_media_sec(bounded, for_end=for_end) - media_start_sec)


def _timed_overlay_stream(input_node: str, output_node: str, start_sec: float, end_sec: float) -> str:
    """Create a short overlay stream shifted onto a segment-local timeline."""
    start = max(float(start_sec), 0.0)
    duration = max(float(end_sec) - start, 0.001)
    return (
        f"[{input_node}]format=rgba,trim=duration={duration:.6f},"
        f"setpts=PTS+{start:.6f}/TB[{output_node}]"
    )


def _local_clicks_for_segment(
    click_events: Optional[List[ClickEvent]],
    segment: VideoSegment,
) -> List[ClickEvent]:
    """Filter absolute click events into a segment-local zero timeline."""
    return [
        replace(click, timestamp=float(click.timestamp) - segment.start_ms)
        for click in (click_events or [])
        if segment.start_ms <= float(click.timestamp) < segment.end_ms
    ]


def _media_highlights_for_segment(
    highlights: Optional[List[HighlightBox]],
    segment: VideoSegment,
    mapper: _SessionMediaMapper,
    media_start_sec: float,
) -> List[HighlightBox]:
    """Filter highlights into a segment-local media timeline."""
    local: List[HighlightBox] = []
    for highlight in highlights or []:
        start = max(float(highlight.start_ms), float(segment.start_ms))
        end = min(float(highlight.end_ms), float(segment.end_ms))
        if end <= start:
            continue
        media_start_ms = _media_time_for_segment(start, segment, mapper, media_start_sec) * 1000.0
        media_end_ms = _media_time_for_segment(end, segment, mapper, media_start_sec, for_end=True) * 1000.0
        if media_end_ms <= media_start_ms:
            continue
        local.append(
            replace(
                highlight,
                start_ms=media_start_ms,
                end_ms=media_end_ms,
            )
        )
    return local


def _ease_in_out(progress: float) -> float:
    t = max(0.0, min(1.0, float(progress)))
    return 1.0 - pow(1.0 - t, 5.0)


def _zoom_state_at_time(keyframes: List[ZoomKeyframe], time_ms: float) -> tuple[float, float, float]:
    """Compute local zoom/pan state for overlay coordinate mapping."""
    sorted_kfs = sorted(keyframes, key=lambda k: float(k.timestamp))
    active_idx = -1
    for idx in range(len(sorted_kfs) - 1, -1, -1):
        if time_ms >= float(sorted_kfs[idx].timestamp):
            active_idx = idx
            break
    if active_idx < 0:
        return 1.0, 0.5, 0.5

    active = sorted_kfs[active_idx]
    duration = max(float(active.duration), 0.0)
    elapsed = max(0.0, float(time_ms) - float(active.timestamp))
    progress = elapsed / duration if duration > 0 else 1.0
    eased = _ease_in_out(progress)

    prev_zoom = float(sorted_kfs[active_idx - 1].zoom) if active_idx > 0 else 1.0
    prev_x = float(sorted_kfs[active_idx - 1].x) if active_idx > 0 else 0.5
    prev_y = float(sorted_kfs[active_idx - 1].y) if active_idx > 0 else 0.5

    zoom = prev_zoom + (float(active.zoom) - prev_zoom) * eased
    pan_x = prev_x + (float(active.x) - prev_x) * eased
    pan_y = prev_y + (float(active.y) - prev_y) * eased
    return max(1.0, zoom), max(0.0, min(1.0, pan_x)), max(0.0, min(1.0, pan_y))


def _map_zoomed_relative_point(
    rel_x: float,
    rel_y: float,
    local_time_ms: float,
    keyframes: List[ZoomKeyframe],
) -> tuple[float, float]:
    zoom, pan_x, pan_y = _zoom_state_at_time(keyframes, local_time_ms)
    if zoom <= 1.001:
        return _clamp_relative_point(rel_x, rel_y)
    visible_w = 1.0 / zoom
    visible_h = 1.0 / zoom
    crop_x = max(0.0, min(1.0 - visible_w, pan_x - visible_w / 2.0))
    crop_y = max(0.0, min(1.0 - visible_h, pan_y - visible_h / 2.0))
    return _clamp_relative_point((rel_x - crop_x) * zoom, (rel_y - crop_y) * zoom)


def _clamp_relative_point(rel_x: float, rel_y: float) -> tuple[float, float]:
    """Keep overlay anchors inside the visible screen after zoom/crop mapping."""
    return max(0.0, min(1.0, float(rel_x))), max(0.0, min(1.0, float(rel_y)))


def _click_point_for_export(
    click: ClickEvent,
    mouse_track: Optional[List[MousePosition]],
    timestamp_ms: float,
) -> tuple[float, float]:
    """Prefer the sampled cursor track for click placement when available."""
    if not mouse_track:
        return float(click.x), float(click.y)

    before: Optional[MousePosition] = None
    after: Optional[MousePosition] = None
    target = float(timestamp_ms)
    for point in mouse_track:
        point_ts = float(point.timestamp)
        if point_ts <= target:
            before = point
            continue
        after = point
        break

    max_gap_ms = 250.0
    if before and after:
        gap = float(after.timestamp) - float(before.timestamp)
        if 0 < gap <= max_gap_ms:
            ratio = (target - float(before.timestamp)) / gap
            x = float(before.x) + (float(after.x) - float(before.x)) * ratio
            y = float(before.y) + (float(after.y) - float(before.y)) * ratio
            return x, y
        nearest = before if abs(target - float(before.timestamp)) <= abs(float(after.timestamp) - target) else after
        if abs(target - float(nearest.timestamp)) <= max_gap_ms:
            return float(nearest.x), float(nearest.y)
    elif before and abs(target - float(before.timestamp)) <= max_gap_ms:
        return float(before.x), float(before.y)
    elif after and abs(float(after.timestamp) - target) <= max_gap_ms:
        return float(after.x), float(after.y)

    return float(click.x), float(click.y)


def _build_zoompan_filter(keyframes: List[ZoomKeyframe], fps: float) -> str:
    """Build the FFmpeg zoompan filter for a local segment timeline."""
    expr_z = "1"
    expr_px = "0.5"
    expr_py = "0.5"

    for kf in sorted(keyframes, key=lambda k: k.timestamp):
        t_s = max(float(kf.timestamp) / 1000.0, 0.0)
        dur = max(float(kf.duration) / 1000.0, 0.0)
        t_e = t_s + dur

        target_z = max(1.0, float(kf.zoom))
        target_x = float(kf.x)
        target_y = float(kf.y)

        if dur > 0:
            ease = f"(1 - pow(1 - (time - {t_s})/{dur}, 5))"
            expr_z = f"if(lt(time, {t_s}), {expr_z}, if(lt(time, {t_e}), {expr_z} + ({target_z} - ({expr_z})) * {ease}, {target_z}))"
            expr_px = f"if(lt(time, {t_s}), {expr_px}, if(lt(time, {t_e}), {expr_px} + ({target_x} - ({expr_px})) * {ease}, {target_x}))"
            expr_py = f"if(lt(time, {t_s}), {expr_py}, if(lt(time, {t_e}), {expr_py} + ({target_y} - ({expr_py})) * {ease}, {target_y}))"
        else:
            expr_z = f"if(lt(time, {t_s}), {expr_z}, {target_z})"
            expr_px = f"if(lt(time, {t_s}), {expr_px}, {target_x})"
            expr_py = f"if(lt(time, {t_s}), {expr_py}, {target_y})"

    z_var = f"({expr_z})"
    zoompan_x = f"clip(({expr_px}) * iw - (iw/{z_var})/2, 0, iw - iw/{z_var})"
    zoompan_y = f"clip(({expr_py}) * ih - (ih/{z_var})/2, 0, ih - ih/{z_var})"
    return f"zoompan=z='{z_var}':x='{zoompan_x}':y='{zoompan_y}':d=1:fps={fps}"


class VideoExporter:
    """Export pipeline for rendering Zumly sessions to MP4 (Phase 5 Motion Engine)."""

    def __init__(
        self,
        progress_cb: Optional[Callable[[float], None]] = None,
        finished_cb: Optional[Callable[[str], None]] = None,
        error_cb: Optional[Callable[[str], None]] = None,
        status_cb: Optional[Callable[[str], None]] = None,
    ) -> None:
        self._progress_cb = progress_cb
        self._finished_cb = finished_cb
        self._error_cb = error_cb
        self._status_cb = status_cb
        self._thread: Optional[threading.Thread] = None

    def export(
        self,
        input_path: str,
        output_path: str,
        keyframes: List[ZoomKeyframe],
        actual_fps: float = 0.0,
        mouse_track: Optional[List[MousePosition]] = None,
        monitor_rect: Optional[dict] = None,
        bg_preset: Optional[BackgroundPreset] = None,
        frame_preset: Optional[FramePreset] = None,
        target_resolution: Optional[tuple[int, int]] = None,
        click_events: Optional[List[ClickEvent]] = None,
        click_preset: Optional[ClickEffectPreset] = None,
        duration_ms: float = 0.0,
        frame_timestamps: Optional[List[float]] = None,
        trim_start_ms: float = 0.0,
        trim_end_ms: float = 0.0,
        encoder_id: str = "libx264",
        voiceover_segments: Optional[List[VoiceoverSegment]] = None,
        video_segments: Optional[List[VideoSegment]] = None,
        chapters: Optional[List[Chapter]] = None,
        timeline_frames: Optional[List[TimelineFrame]] = None,
        highlights: Optional[List[HighlightBox]] = None,
    ) -> None:
        self._thread = threading.Thread(
            target=self._run,
            args=(input_path, output_path, bg_preset, frame_preset, target_resolution, duration_ms, frame_timestamps, keyframes, mouse_track, click_events, click_preset, actual_fps, monitor_rect, video_segments, timeline_frames, highlights),
            daemon=True,
        )
        self._thread.start()

    def _run(self, input_path: str, output_path: str, bg_preset: BackgroundPreset, frame_preset: FramePreset, target_resolution: Optional[tuple[int, int]], duration_ms: float, frame_timestamps: Optional[List[float]], keyframes: List[ZoomKeyframe], mouse_track: Optional[List[MousePosition]], click_events: Optional[List[ClickEvent]], click_preset: Optional[ClickEffectPreset], actual_fps: float, monitor_rect: Optional[dict], video_segments: Optional[List[VideoSegment]], timeline_frames: Optional[List[TimelineFrame]], highlights: Optional[List[HighlightBox]]):
        temp_files = []
        try:
            if self._status_cb: self._status_cb("Starting export...")
            
            ffmpeg = _ffmpeg_exe()

            # Probe source dimensions and FPS
            ffprobe_cmd = [ffmpeg, "-i", input_path]
            p = subprocess.run(ffprobe_cmd, capture_output=True, text=True, **_subprocess_kwargs())
            
            src_w, src_h = 1920, 1080 
            src_fps = 30.0
            
            m = re.search(r"Video:.* (\d{3,5})x(\d{3,5})", p.stderr)
            if m:
                src_w, src_h = int(m.group(1)), int(m.group(2))
            
            fps_m = re.search(r"(\d+(?:\.\d+)?) fps", p.stderr)
            if fps_m:
                src_fps = float(fps_m.group(1))
            if actual_fps > 0:
                src_fps = actual_fps

            dur_m = re.search(r"Duration: (\d+):(\d+):(\d+\.\d+)", p.stderr)
            total_sec = 0.0
            if dur_m:
                total_sec = int(dur_m.group(1))*3600 + int(dur_m.group(2))*60 + float(dur_m.group(3))
            elif duration_ms:
                total_sec = duration_ms / 1000.0

            out_w, out_h = src_w, src_h
            if target_resolution:
                out_w, out_h = target_resolution
                
            out_w = out_w + (out_w % 2)
            out_h = out_h + (out_h % 2)

            frame_preset = frame_preset or DEFAULT_FRAME
            source_duration_ms = duration_ms or ((frame_timestamps[-1] if frame_timestamps else 0.0) or (total_sec * 1000.0))
            if source_duration_ms > 0:
                keyframes = [kf for kf in keyframes if float(kf.timestamp) <= source_duration_ms]
                if click_events:
                    click_events = [ce for ce in click_events if float(ce.timestamp) <= source_duration_ms]

            geom = GeometryComputer(
                canvas_w=out_w,
                canvas_h=out_h,
                src_w=src_w,
                src_h=src_h,
                frame_preset=frame_preset,
            ).compute()
            scr_x = geom["scr_x"]
            scr_y = geom["scr_y"]
            scr_w = geom["scr_w"]
            scr_h = geom["scr_h"]

            bg_color = "000000"
            if bg_preset and hasattr(bg_preset, "color_top") and bg_preset.color_top:
                r, g, b = bg_preset.color_top
                bg_color = f"{r:02x}{g:02x}{b:02x}"

            # Generate assets
            frame_img_path = generate_device_frame_png(frame_preset, out_w, out_h, geom)
            temp_files.append(frame_img_path)

            explicit_segments = video_segments is not None
            segments = _normalize_video_segments(
                video_segments,
                source_duration_ms,
                fill_gaps=not explicit_segments,
            )
            if not segments:
                if self._error_cb:
                    self._error_cb("Export failed: unknown source duration")
                return
            media_mapper = _SessionMediaMapper(frame_timestamps, total_sec, src_fps)
            if media_mapper.has_timestamps and total_sec > 0:
                logger.info(
                    "Using %d frame timestamps to map %.3fs session timeline onto %.3fs media timeline",
                    len(frame_timestamps or []),
                    source_duration_ms / 1000.0,
                    total_sec,
                )
            segment_media_bounds = [
                media_mapper.segment_bounds(segment)
                for segment in segments
            ]
            output_total_sec = sum(
                ((segment.end_ms - segment.start_ms) / 1000.0) / _segment_speed(segment)
                for segment in segments
            )
            ordered_frames = sorted(timeline_frames or [], key=lambda frame: (float(frame.timestamp_ms), frame.id))
            output_total_sec += sum(max(float(frame.duration_ms), 250.0) / 1000.0 for frame in ordered_frames)
            has_speed_changes = any(abs(_segment_speed(segment) - 1.0) > 0.01 for segment in segments)
            has_timeline_edits = explicit_segments and (
                len(segments) != 1
                or abs(segments[0].start_ms) > 0.5
                or abs(segments[0].end_ms - source_duration_ms) > 0.5
            )
            local_click_sets = [_local_clicks_for_segment(click_events, segment) for segment in segments]
            local_highlight_sets = [
                _media_highlights_for_segment(highlights, segment, media_mapper, segment_media_bounds[idx][0])
                for idx, segment in enumerate(segments)
            ]
            local_click_count = sum(len(items) for items in local_click_sets)
            export_mouse_track = sorted(mouse_track or [], key=lambda point: float(point.timestamp))

            click_img_path = None
            if (
                local_click_count > 0
                and click_preset
                and click_preset.duration_ms > 0
                and click_preset.color[3] > 0
            ):
                click_img_path = generate_click_png(click_preset)
                temp_files.append(click_img_path)
            cursor_img_path = None
            if local_click_count > 0:
                cursor_img_path = generate_cursor_png()
                temp_files.append(cursor_img_path)

            highlight_base_input = 2 + (1 if click_img_path else 0) + (1 if cursor_img_path else 0)
            highlight_img_paths: List[str] = []
            local_highlight_assets: list[list[tuple[HighlightBox, int]]] = []
            for local_highlights in local_highlight_sets:
                asset_rows: list[tuple[HighlightBox, int]] = []
                for highlight in local_highlights:
                    highlight_path = generate_highlight_png(highlight, out_w, out_h, geom)
                    temp_files.append(highlight_path)
                    asset_rows.append((highlight, highlight_base_input + len(highlight_img_paths)))
                    highlight_img_paths.append(highlight_path)
                local_highlight_assets.append(asset_rows)

            frame_base_input = highlight_base_input + len(highlight_img_paths)
            timeline_frame_img_paths: List[str] = []
            for timeline_frame in ordered_frames:
                frame_path = generate_timeline_frame_png(timeline_frame, out_w, out_h)
                temp_files.append(frame_path)
                timeline_frame_img_paths.append(frame_path)

            filter_lines = []

            if len(segments) > 1:
                frame_nodes = "".join(f"[fr{i}]" for i in range(len(segments)))
                filter_lines.append(f"[1:v]split={len(segments)}{frame_nodes}")
            else:
                filter_lines.append("[1:v]null[fr0]")

            click_node_index = 0
            if click_img_path and local_click_count > 1:
                click_nodes = "".join(f"[cl{i}]" for i in range(local_click_count))
                filter_lines.append(f"[2:v]split={local_click_count}{click_nodes}")
            elif click_img_path and local_click_count == 1:
                filter_lines.append("[2:v]null[cl0]")

            cursor_input_index = 2 + (1 if click_img_path else 0)
            if cursor_img_path and local_click_count > 1:
                cursor_nodes = "".join(f"[cu{i}]" for i in range(local_click_count))
                filter_lines.append(f"[{cursor_input_index}:v]split={local_click_count}{cursor_nodes}")
            elif cursor_img_path and local_click_count == 1:
                filter_lines.append(f"[{cursor_input_index}:v]null[cu0]")

            segment_outputs: List[str] = []
            cursor_node_index = 0
            frame_node_index = 0
            appended_frame_ids: set[str] = set()
            for seg_index, segment in enumerate(segments):
                media_start_sec, media_end_sec, media_duration_sec = segment_media_bounds[seg_index]
                session_duration_sec = max((segment.end_ms - segment.start_ms) / 1000.0, 0.001)
                output_duration_sec = session_duration_sec / _segment_speed(segment)
                retime_scale = output_duration_sec / max(media_duration_sec, 0.001)

                media_kfs = _media_keyframes_for_segment(keyframes, segment, media_mapper, media_start_sec)
                local_clicks = local_click_sets[seg_index]
                zoompan_filter = _build_zoompan_filter(media_kfs, src_fps)

                filter_lines.append(
                    f"[0:v]trim=start={media_start_sec:.6f}:end={media_end_sec:.6f},setpts=PTS-STARTPTS[s{seg_index}trim]"
                )
                filter_lines.append(f"[s{seg_index}trim]{zoompan_filter}[s{seg_index}zoom]")
                filter_lines.append(f"[s{seg_index}zoom]scale={scr_w}:{scr_h}[s{seg_index}vid]")

                color_args = f"color=c=0x{bg_color}:s={out_w}x{out_h}:r={src_fps}:d={media_duration_sec}"
                filter_lines.append(f"{color_args}[s{seg_index}bg]")
                filter_lines.append(
                    f"[s{seg_index}bg][s{seg_index}vid]overlay=x={scr_x}:y={scr_y}:"
                    f"shortest=1:eof_action=pass[s{seg_index}base]"
                )
                filter_lines.append(
                    f"[s{seg_index}base]setpts=PTS-STARTPTS[s{seg_index}comp0]"
                )

                current_comp_node = f"s{seg_index}comp0"
                if (
                    local_clicks
                    and click_img_path
                    and click_preset
                    and click_preset.duration_ms > 0
                    and click_preset.color[3] > 0
                ):
                    r = max(int(click_preset.radius), 1)
                    dur_sec = click_preset.duration_ms / 1000.0
                    m_left = monitor_rect.get("left", 0) if monitor_rect else 0
                    m_top = monitor_rect.get("top", 0) if monitor_rect else 0
                    m_w = monitor_rect.get("width", src_w) if monitor_rect else src_w
                    m_h = monitor_rect.get("height", src_h) if monitor_rect else src_h

                    for local_click in local_clicks:
                        abs_click_ms = float(segment.start_ms) + float(local_click.timestamp)
                        t_s = _media_time_for_segment(abs_click_ms, segment, media_mapper, media_start_sec)
                        t_e = min(t_s + dur_sec / max(retime_scale, 0.001), media_duration_sec)
                        click_x, click_y = _click_point_for_export(local_click, export_mouse_track, abs_click_ms)
                        rel_x = (click_x - m_left) / max(m_w, 1)
                        rel_y = (click_y - m_top) / max(m_h, 1)
                        rel_x, rel_y = _map_zoomed_relative_point(
                            rel_x,
                            rel_y,
                            t_s * 1000.0,
                            media_kfs,
                        )
                        cx = int(scr_x + rel_x * scr_w - r)
                        cy = int(scr_y + rel_y * scr_h - r)

                        timed_node = f"s{seg_index}clicksrc{click_node_index}"
                        next_node = f"s{seg_index}click{click_node_index}"
                        filter_lines.append(
                            _timed_overlay_stream(f"cl{click_node_index}", timed_node, t_s, t_e)
                        )
                        filter_lines.append(
                            f"[{current_comp_node}][{timed_node}]overlay=x={cx}:y={cy}:"
                            f"eof_action=pass:repeatlast=0[{next_node}]"
                        )
                        current_comp_node = next_node
                        click_node_index += 1

                if local_clicks and cursor_img_path:
                    cursor_hold_sec = max(
                        (click_preset.duration_ms / 1000.0) if click_preset else 0.0,
                        0.75,
                    )
                    m_left = monitor_rect.get("left", 0) if monitor_rect else 0
                    m_top = monitor_rect.get("top", 0) if monitor_rect else 0
                    m_w = monitor_rect.get("width", src_w) if monitor_rect else src_w
                    m_h = monitor_rect.get("height", src_h) if monitor_rect else src_h

                    for local_click in local_clicks:
                        abs_click_ms = float(segment.start_ms) + float(local_click.timestamp)
                        t_s = _media_time_for_segment(abs_click_ms, segment, media_mapper, media_start_sec)
                        t_e = min(t_s + cursor_hold_sec / max(retime_scale, 0.001), media_duration_sec)
                        click_x, click_y = _click_point_for_export(local_click, export_mouse_track, abs_click_ms)
                        rel_x = (click_x - m_left) / max(m_w, 1)
                        rel_y = (click_y - m_top) / max(m_h, 1)
                        rel_x, rel_y = _map_zoomed_relative_point(
                            rel_x,
                            rel_y,
                            t_s * 1000.0,
                            media_kfs,
                        )
                        cx = int(scr_x + rel_x * scr_w - 4)
                        cy = int(scr_y + rel_y * scr_h - 4)

                        timed_node = f"s{seg_index}cursorsrc{cursor_node_index}"
                        next_node = f"s{seg_index}cursor{cursor_node_index}"
                        filter_lines.append(
                            _timed_overlay_stream(f"cu{cursor_node_index}", timed_node, t_s, t_e)
                        )
                        filter_lines.append(
                            f"[{current_comp_node}][{timed_node}]overlay=x={cx}:y={cy}:"
                            f"eof_action=pass:repeatlast=0[{next_node}]"
                        )
                        current_comp_node = next_node
                        cursor_node_index += 1

                for highlight, input_index in local_highlight_assets[seg_index]:
                    t_s = max(float(highlight.start_ms) / 1000.0, 0.0)
                    t_e = min(float(highlight.end_ms) / 1000.0, media_duration_sec)
                    if t_e <= t_s:
                        continue
                    timed_node = f"s{seg_index}highlightsrc{input_index}"
                    next_node = f"s{seg_index}highlight{input_index}"
                    filter_lines.append(
                        _timed_overlay_stream(f"{input_index}:v", timed_node, t_s, t_e)
                    )
                    filter_lines.append(
                        f"[{current_comp_node}][{timed_node}]overlay=x=0:y=0:"
                        f"eof_action=pass:repeatlast=0[{next_node}]"
                    )
                    current_comp_node = next_node

                framed_node = f"s{seg_index}framed"
                filter_lines.append(
                    f"[{current_comp_node}][fr{seg_index}]overlay=x=0:y=0:"
                    f"shortest=1:eof_action=pass[{framed_node}]"
                )

                out_node = f"s{seg_index}out"
                filter_lines.append(f"[{framed_node}]setpts={retime_scale:.8f}*PTS[{out_node}]")
                segment_outputs.append(out_node)

                for frame_idx, timeline_frame in enumerate(ordered_frames):
                    if timeline_frame.id in appended_frame_ids:
                        continue
                    if float(timeline_frame.timestamp_ms) <= float(segment.end_ms) + 0.5:
                        input_index = frame_base_input + frame_idx
                        frame_out = f"tf{frame_node_index}out"
                        frame_duration = max(float(timeline_frame.duration_ms), 250.0) / 1000.0
                        filter_lines.append(
                            f"[{input_index}:v]scale={out_w}:{out_h}:force_original_aspect_ratio=decrease,"
                            f"pad={out_w}:{out_h}:(ow-iw)/2:(oh-ih)/2,"
                            f"fps={src_fps},trim=duration={frame_duration:.6f},setpts=PTS-STARTPTS[{frame_out}]"
                        )
                        segment_outputs.append(frame_out)
                        appended_frame_ids.add(timeline_frame.id)
                        frame_node_index += 1

            for frame_idx, timeline_frame in enumerate(ordered_frames):
                if timeline_frame.id in appended_frame_ids:
                    continue
                input_index = frame_base_input + frame_idx
                frame_out = f"tf{frame_node_index}out"
                frame_duration = max(float(timeline_frame.duration_ms), 250.0) / 1000.0
                filter_lines.append(
                    f"[{input_index}:v]scale={out_w}:{out_h}:force_original_aspect_ratio=decrease,"
                    f"pad={out_w}:{out_h}:(ow-iw)/2:(oh-ih)/2,"
                    f"fps={src_fps},trim=duration={frame_duration:.6f},setpts=PTS-STARTPTS[{frame_out}]"
                )
                segment_outputs.append(frame_out)
                frame_node_index += 1

            if len(segment_outputs) > 1:
                concat_inputs = "".join(f"[{node}]" for node in segment_outputs)
                filter_lines.append(f"{concat_inputs}concat=n={len(segment_outputs)}:v=1:a=0[out]")
            else:
                filter_lines.append(f"[{segment_outputs[0]}]null[out]")
            
            filtergraph = ";\n".join(filter_lines)
            
            # Write filtergraph to temp file to bypass CLI limits
            f = tempfile.NamedTemporaryFile(suffix=".txt", delete=False)
            graph_path = f.name
            f.close()
            temp_files.append(graph_path)
            
            with open(graph_path, "w", encoding="utf-8") as f:
                f.write(filtergraph)
            logger.info("FFmpeg filter graph retained for debugging: %s", graph_path)

            cmd = [
                ffmpeg, "-y",
                "-i", input_path,
                "-loop", "1",
                "-i", frame_img_path,
            ]
            if click_img_path:
                cmd.extend(["-loop", "1", "-i", click_img_path])
            if cursor_img_path:
                cmd.extend(["-loop", "1", "-i", cursor_img_path])
            for highlight_path in highlight_img_paths:
                cmd.extend(["-loop", "1", "-i", highlight_path])
            for frame_path in timeline_frame_img_paths:
                cmd.extend(["-loop", "1", "-i", frame_path])
                
            cmd.extend([
                "-filter_complex_script", graph_path,
                "-map", "[out]",
                "-c:v", "libx264",
                "-preset", "fast",
            ])
            if has_speed_changes or has_timeline_edits:
                logger.info("Timeline-edited export: dropping source audio for MVP segment retiming/cuts")
            else:
                cmd.extend([
                    "-map", "0:a?",  # Explicitly map audio from input 0, use ? in case it has no audio
                    "-c:a", "aac",
                ])
            if output_total_sec > 0:
                cmd.extend(["-t", f"{output_total_sec:.3f}", "-shortest"])
            cmd.append(output_path)
            
            logger.info("Running FFmpeg with graph: %s", graph_path)
            
            proc = subprocess.Popen(
                cmd,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                universal_newlines=True,
                **_subprocess_kwargs()
            )

            stderr_tail = []
            while True:
                line = proc.stderr.readline()
                if not line:
                    break
                stderr_tail.append(line)
                if len(stderr_tail) > 80:
                    stderr_tail = stderr_tail[-80:]
                
                time_m = re.search(r"time=(\d+):(\d+):(\d+\.\d+)", line)
                if time_m and output_total_sec > 0:
                    curr_sec = int(time_m.group(1))*3600 + int(time_m.group(2))*60 + float(time_m.group(3))
                    prog = min(1.0, curr_sec / output_total_sec)
                    if self._progress_cb: self._progress_cb(prog)

            proc.wait()
            if proc.returncode != 0:
                stderr_excerpt = "".join(stderr_tail)[-4000:]
                logger.error("FFmpeg export failed with return code %d. Stderr: %s", proc.returncode, stderr_excerpt)
                if self._error_cb: self._error_cb("FFmpeg export failed")
            else:
                logger.info("Export completed successfully: %s", output_path)
                if self._progress_cb: self._progress_cb(1.0)
                if self._finished_cb: self._finished_cb(output_path)

        except Exception as exc:
            logger.exception("Export crashed")
            if self._error_cb: self._error_cb(str(exc))
        finally:
            for path in temp_files:
                try:
                    os.remove(path)
                except FileNotFoundError:
                    pass
