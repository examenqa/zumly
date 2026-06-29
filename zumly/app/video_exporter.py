import logging
import os
import subprocess
import threading
import time
import tempfile
import re
from dataclasses import dataclass
from typing import Any, List, Optional, Callable

from PIL import Image, ImageDraw

from .models import ZoomKeyframe, MousePosition, ClickEvent, VideoSegment, VoiceoverSegment, ClickEffectPreset, DEFAULT_CLICK_EFFECT, Chapter
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
    
    path = os.path.join(tempfile.gettempdir(), f"frame_{os.getpid()}_{int(time.time())}.png")
    img.save(path)
    return path

def generate_click_png(preset: ClickEffectPreset) -> str:
    """Generate a visible click marker PNG for FFmpeg overlay."""
    r = max(int(preset.radius), 1)
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

    path = os.path.join(tempfile.gettempdir(), f"click_{os.getpid()}_{int(time.time())}.png")
    img.save(path)
    return path


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
    ) -> None:
        self._thread = threading.Thread(
            target=self._run,
            args=(input_path, output_path, bg_preset, frame_preset, target_resolution, duration_ms, keyframes, click_events, click_preset, actual_fps, monitor_rect),
            daemon=True,
        )
        self._thread.start()

    def _run(self, input_path: str, output_path: str, bg_preset: BackgroundPreset, frame_preset: FramePreset, target_resolution: Optional[tuple[int, int]], duration_ms: float, keyframes: List[ZoomKeyframe], click_events: Optional[List[ClickEvent]], click_preset: Optional[ClickEffectPreset], actual_fps: float, monitor_rect: Optional[dict]):
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
            if total_sec > 0:
                keyframes = [kf for kf in keyframes if (kf.timestamp / 1000.0) <= total_sec]
                if click_events:
                    click_events = [ce for ce in click_events if (ce.timestamp / 1000.0) <= total_sec]

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
            
            click_img_path = None
            if click_events and click_preset:
                click_img_path = generate_click_png(click_preset)
                temp_files.append(click_img_path)

            # Build motion nodes
            expr_z = "1"
            expr_px = "0.5"
            expr_py = "0.5"
            
            if keyframes:
                # Sort by timestamp
                sorted_kfs = sorted(keyframes, key=lambda k: k.timestamp)
                for kf in sorted_kfs:
                    t_s = kf.timestamp / 1000.0
                    dur = kf.duration / 1000.0
                    t_e = t_s + dur
                    
                    target_z = max(1.0, kf.zoom)
                    target_x = kf.x
                    target_y = kf.y
                    
                    if dur > 0:
                        ease = f"(1 - pow(1 - (time - {t_s})/{dur}, 5))"
                        
                        expr_z = f"if(lt(time, {t_s}), {expr_z}, if(lt(time, {t_e}), {expr_z} + ({target_z} - ({expr_z})) * {ease}, {target_z}))"
                        expr_px = f"if(lt(time, {t_s}), {expr_px}, if(lt(time, {t_e}), {expr_px} + ({target_x} - ({expr_px})) * {ease}, {target_x}))"
                        expr_py = f"if(lt(time, {t_s}), {expr_py}, if(lt(time, {t_e}), {expr_py} + ({target_y} - ({expr_py})) * {ease}, {target_y}))"
                    else:
                        expr_z = f"if(lt(time, {t_s}), {expr_z}, {target_z})"
                        expr_px = f"if(lt(time, {t_s}), {expr_px}, {target_x})"
                        expr_py = f"if(lt(time, {t_s}), {expr_py}, {target_y})"
            
            # clamp x and y to avoid out of bounds
            # iw and ih are input width and height
            # x_px = px * iw - (iw/z)/2
            z_var = f"({expr_z})"
            zoompan_x = f"clip(({expr_px}) * iw - (iw/{z_var})/2, 0, iw - iw/{z_var})"
            zoompan_y = f"clip(({expr_py}) * ih - (ih/{z_var})/2, 0, ih - ih/{z_var})"
            
            zoompan_filter = f"zoompan=z='{z_var}':x='{zoompan_x}':y='{zoompan_y}':d=1:fps={src_fps}"
            
            filter_lines = []
            
            # 1. Motion node
            filter_lines.append(f"[0:v]{zoompan_filter}[zoomed]")
            
            # 2. Static composition
            filter_lines.append(f"[{'zoomed'}]scale={scr_w}:{scr_h}[vid]")
            color_args = f"color=c=0x{bg_color}:s={out_w}x{out_h}:r={src_fps}"
            if total_sec > 0:
                color_args += f":d={total_sec}"
            filter_lines.append(f"{color_args}[bg]")
            filter_lines.append(f"[bg][vid]overlay=x={scr_x}:y={scr_y}[bg_vid]")

            # 3. Click effects are drawn on the final canvas so they remain
            # visible even when the source video is zoomed or cropped.
            current_comp_node = "bg_vid"
            if click_events and click_img_path and click_preset and click_preset.duration_ms > 0 and click_preset.color[3] > 0:
                r = max(int(click_preset.radius), 1)
                dur_sec = click_preset.duration_ms / 1000.0
                m_left = monitor_rect.get("left", 0) if monitor_rect else 0
                m_top = monitor_rect.get("top", 0) if monitor_rect else 0
                m_w = monitor_rect.get("width", src_w) if monitor_rect else src_w
                m_h = monitor_rect.get("height", src_h) if monitor_rect else src_h

                if len(click_events) > 1:
                    split_nodes = "".join([f"[cl{i}]" for i in range(len(click_events))])
                    filter_lines.append(f"[2:v]split={len(click_events)}{split_nodes}")
                elif len(click_events) == 1:
                    filter_lines.append("[2:v]null[cl0]")

                for i, ce in enumerate(click_events):
                    t_s = ce.timestamp / 1000.0
                    t_e = t_s + dur_sec
                    rel_x = (ce.x - m_left) / max(m_w, 1)
                    rel_y = (ce.y - m_top) / max(m_h, 1)
                    cx = int(scr_x + rel_x * scr_w - r)
                    cy = int(scr_y + rel_y * scr_h - r)

                    next_node = f"fc{i}"
                    filter_lines.append(
                        f"[{current_comp_node}][cl{i}]overlay=x={cx}:y={cy}:"
                        f"enable='between(t,{t_s},{t_e})'[{next_node}]"
                    )
                    current_comp_node = next_node

            filter_lines.append(f"[{current_comp_node}][1:v]overlay=x=0:y=0[out]")
            
            filtergraph = ";\n".join(filter_lines)
            
            # Write filtergraph to temp file to bypass CLI limits
            graph_path = os.path.join(tempfile.gettempdir(), f"graph_{os.getpid()}_{int(time.time())}.txt")
            
            with open(graph_path, "w", encoding="utf-8") as f:
                f.write(filtergraph)
            logger.info("FFmpeg filter graph retained for debugging: %s", graph_path)

            cmd = [
                ffmpeg, "-y",
                "-i", input_path,
                "-i", frame_img_path,
            ]
            if click_img_path:
                cmd.extend(["-loop", "1", "-i", click_img_path])
                
            cmd.extend([
                "-filter_complex_script", graph_path,
                "-map", "[out]",
                "-map", "0:a?",  # Explicitly map audio from input 0, use ? in case it has no audio
                "-c:v", "libx264",
                "-preset", "fast",
                "-c:a", "aac",
            ])
            if total_sec > 0:
                cmd.extend(["-t", f"{total_sec:.3f}", "-shortest"])
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
                if time_m and total_sec > 0:
                    curr_sec = int(time_m.group(1))*3600 + int(time_m.group(2))*60 + float(time_m.group(3))
                    prog = min(1.0, curr_sec / total_sec)
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
                if os.path.exists(path):
                    try:
                        os.remove(path)
                    except OSError:
                        pass
