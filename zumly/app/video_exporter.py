"""Export pipeline for rendering Zumly sessions to MP4/GIF.

Algorithm overview
==================

1. Read source video frames from OpenCV (recorded AVI).
2. Build a *source timeline* from per-frame timestamps when available
    (variable-rate WGC capture), otherwise derive timestamps from source FPS.
3. Build a *constant-FPS output timeline* used by ffmpeg output.
4. For each output timestamp:
    - pick the source frame active at that time (binary search in timestamps),
    - compute zoom/pan from :class:`ZoomEngine`,
    - draw cursor/click overlays at the same timestamp,
    - composite frame + bezel/background with NumPy/OpenCV,
    - enqueue raw BGR bytes for the writer thread.
5. Writer thread drains a bounded queue into ffmpeg stdin.
6. ffmpeg encodes MP4 (hardware encoder with fallback chain) or GIF.

The key design goal is timeline determinism: exported visuals, click effects,
zoom transitions, and optional voiceover audio are all evaluated on the same
timeline so playback stays synchronized.
"""

import logging
import os
import queue
import subprocess
"""Export pipeline for rendering Zumly sessions to MP4/GIF.

Algorithm overview
==================

1. Read source video frames from OpenCV (recorded AVI).
2. Build a *source timeline* from per-frame timestamps when available
    (variable-rate WGC capture), otherwise derive timestamps from source FPS.
3. Build a *constant-FPS output timeline* used by ffmpeg output.
4. For each output timestamp:
    - pick the source frame active at that time (binary search in timestamps),
    - compute zoom/pan from :class:`ZoomEngine`,
    - draw cursor/click overlays at the same timestamp,
    - composite frame + bezel/background with NumPy/OpenCV,
    - enqueue raw BGR bytes for the writer thread.
5. Writer thread drains a bounded queue into ffmpeg stdin.
6. ffmpeg encodes MP4 (hardware encoder with fallback chain) or GIF.

The key design goal is timeline determinism: exported visuals, click effects,
zoom transitions, and optional voiceover audio are all evaluated on the same
timeline so playback stays synchronized.
"""

import logging
import os
import queue
import subprocess
import threading
import time
import bisect
from dataclasses import dataclass

logger = logging.getLogger(__name__)
from typing import List, Optional, Callable

import cv2
import numpy as np

from .models import ZoomKeyframe, MousePosition, ClickEvent, VideoSegment, VoiceoverSegment, ClickEffectPreset, DEFAULT_CLICK_EFFECT, Chapter
from .zoom_engine import ZoomEngine
from .cursor_renderer import draw_cursor_cv, draw_clicks_cv, _build_cursor_template
from .backgrounds import BackgroundPreset, DEFAULT_PRESET, WAVE_LAYERS
    # ── public API ──────────────────────────────────────────────────

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
        click_events: Optional[List[ClickEvent]] = None,
        click_preset: Optional[ClickEffectPreset] = None,
        output_dim=None,
        duration_ms: float = 0.0,
        frame_timestamps: Optional[List[float]] = None,
        trim_start_ms: float = 0.0,
        trim_end_ms: float = 0.0,
        encoder_id: str = "libx264",
        voiceover_segments: Optional[List[VoiceoverSegment]] = None,
        video_segments: Optional[List[VideoSegment]] = None,
        chapters: Optional[List[Chapter]] = None,
    ) -> None:
        """Start export in a background thread.

        *output_dim* — (width, height) tuple or ``"auto"`` / ``None``
        to use the source video's native resolution.
        *duration_ms* — wall-clock recording duration for accurate
        progress tracking (``cv2.CAP_PROP_FRAME_COUNT`` is unreliable
        for huffyuv AVI containers).
        *frame_timestamps* — per-frame ms offsets from recording start.
        When provided, gives accurate time mapping for variable-rate
        recordings (e.g. WGC capture).
        *trim_start_ms* / *trim_end_ms* — if non-zero, only export the
        trimmed region of the video.
        *encoder_id* — ffmpeg encoder to use (e.g. ``"h264_nvenc"``,
        ``"h264_qsv"``, ``"h264_amf"``, ``"libx264"``).
        *voiceover_segments* — optional list of ``VoiceoverSegment``
        objects; each with an audio file to mux at a specific time.
        *video_segments* — optional list of ``VideoSegment`` objects;
        when present, only frames within these time ranges are exported.
        *chapters* — optional list of ``Chapter`` objects for MP4 chapter metadata.
        """
        self._thread = threading.Thread(
            target=self._run,
            args=(input_path, output_path, keyframes, actual_fps,
                  mouse_track or [], monitor_rect or {},
                  bg_preset or DEFAULT_PRESET,
                  frame_preset or DEFAULT_FRAME,
                  click_events or [],
                  click_preset or DEFAULT_CLICK_EFFECT,
                  output_dim,
                  duration_ms,
                  frame_timestamps,
                  trim_start_ms,
                  trim_end_ms,
                  encoder_id,
                  voiceover_segments or [],
                  video_segments or [],
                  chapters or []),
            daemon=True,
        )
        self._thread.start()

    # ── internal ────────────────────────────────────────────────────

    def _probe_video(
        self,
        cap: cv2.VideoCapture,
        actual_fps: float,
        duration_ms: float,
        frame_timestamps: Optional[List[float]],
        output_dim,
        output_path: str,
    ) -> Optional[VideoProbeResult]:
        """Phase 1: Probe source video metadata and determine output parameters.

        Returns:
            VideoProbeResult on success, None on error (emits self.error signal).
        """
        src_fps = cap.get(cv2.CAP_PROP_FPS)
        if src_fps <= 0 or src_fps > 120:
            src_fps = 30.0
        if actual_fps > 0:
            src_fps = actual_fps
        cap_frame_count = int(cap.get(cv2.CAP_PROP_FRAME_COUNT))

        # After the post-recording remux the metadata is correct.
        # For legacy videos, detect metadata/duration mismatch and
        # recount frames if needed.
        meta_dur = (cap_frame_count / src_fps * 1000) if src_fps > 0 else 0
        need_recount = False
        if duration_ms > 0 and meta_dur > 0:
            ratio = meta_dur / duration_ms
            if ratio < 0.90 or ratio > 1.10:
                need_recount = True

        if need_recount:
            real_frame_count = 0
            while cap.grab():
                real_frame_count += 1
            cap.set(cv2.CAP_PROP_POS_FRAMES, 0)
            if duration_ms > 0 and real_frame_count > 0:
                src_fps = real_frame_count / (duration_ms / 1000.0)
            total_frames = real_frame_count if real_frame_count > 0 else cap_frame_count
        else:
            total_frames = cap_frame_count
        if total_frames <= 0 and frame_timestamps:
            total_frames = len(frame_timestamps)
        src_w = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH))
        src_h = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))

        if src_w == 0 or src_h == 0:
            if self._error_cb: self._error_cb("Invalid video dimensions")
            return None

        # Determine output canvas size
        if output_dim and output_dim != "auto" and isinstance(output_dim, (tuple, list)):
            if len(output_dim) < 2:
                if self._error_cb: self._error_cb("output_dim must have 2 elements (width, height)")
                return None
            out_w, out_h = int(output_dim[0]), int(output_dim[1])
            if out_w <= 0 or out_h <= 0:
                if self._error_cb: self._error_cb("output_dim width and height must be positive")
                return None
            # Ensure even dimensions (required by H.264)
        # Ensure even dimensions (H.264 requires even dimensions)
        out_w = out_w + (out_w % 2)
        out_h = out_h + (out_h % 2)

        # Normalise extension: allow .gif; everything else becomes .mp4
        is_gif = output_path.lower().endswith(".gif")

        # Export on a stable CFR timeline.  WGC recordings can have sparse,
        # irregular source frames (only when the image changes).  Keeping
        # a very low averaged FPS here makes output choppy and drifts
        # overlays/audio relative to visual content.  Use at least 24 fps
        # for MP4 (cinematic standard — smooth enough without inflating
        # frame count as much as 30 fps would).
        fps = src_fps
        if not is_gif and fps < 24.0:
            fps = 24.0
        logger.info(
            "Export timing | source_fps=%.2f output_fps=%.2f total_frames=%d frame_timestamps=%d",
            src_fps,
            fps,
            total_frames,
            len(frame_timestamps or []),
        )

        return VideoProbeResult(
            src_fps=src_fps,
            total_frames=total_frames,
            src_w=src_w,
            src_h=src_h,
            out_w=out_w,
            out_h=out_h,
            fps=fps,
            is_gif=is_gif,
        )

    def _compute_geometry(
        self,
        probe: VideoProbeResult,
        bg_preset: BackgroundPreset,
        frame_preset: FramePreset,
    ) -> GeometryResult:
        """Phase 2: Compute device/frame layout and build static layers.

        Returns:
            GeometryResult containing screen coordinates, canvas, and masks.
        """
        w, h = probe.out_w, probe.out_h

        # Pre-build the gradient background
        if self._status_cb: self._status_cb("Building background & frame…")
        bg_top_bgr, bg_bottom_bgr = _preset_to_bgr(bg_preset)
        bg = _build_background(w, h, bg_top_bgr, bg_bottom_bgr,
                               kind=bg_preset.kind)

        # Compute device geometry using GeometryComputer
        geom_comp = GeometryComputer(w, h, probe.src_w, probe.src_h, frame_preset)
        geom = geom_comp.compute()

        scr_x = geom["scr_x"]
        scr_y = geom["scr_y"]
        scr_w = geom["scr_w"]
        scr_h = geom["scr_h"]

        # Build canvas and masks based on frame preset
        if frame_preset.is_none:
            # No frame — simple bg canvas
            base_canvas = bg.copy()
            screen_mask = np.zeros((h, w), dtype=np.uint8)
            screen_mask[scr_y:scr_y + scr_h, scr_x:scr_x + scr_w] = 255
            device_mask_u8 = None
        else:
            bw = geom["bw"]
            if bw > 0:
                # Pre-render the bezel (bg + shadow + bezel + edge)
                base_canvas, screen_mask, _ = _build_bezel_layer(
                    h, w, bg,
                    geom["dev_x"], geom["dev_y"], geom["dev_w"], geom["dev_h"],
                    scr_x, scr_y, scr_w, scr_h,
                    geom["outer_r"], geom["inner_r"], geom["edge_thickness"],
                )
            else:
                # Shadow-only or zero-bezel: just shadow + rounded screen
                base_canvas = bg.copy()
                if frame_preset.shadow_layers > 0 and geom["outer_r"] > 0:
                    for i in range(frame_preset.shadow_layers):
                        off = 2 + i * 2
                        shadow_mask = np.zeros((h, w), dtype=np.uint8)
                        s_pts = _rounded_rect_contour(
                            geom["dev_x"] + int(off * 0.3), geom["dev_y"] + off,
                            geom["dev_w"], geom["dev_h"], geom["outer_r"] + 2
                        )
                        cv2.fillPoly(shadow_mask, [s_pts], 255)
                        alpha = max(40 - i * 10, 5) / 255.0
                        shadow_region = shadow_mask > 0
                        base_canvas[shadow_region] = (
                            base_canvas[shadow_region].astype(np.float32) * (1 - alpha)
                        ).astype(np.uint8)
                screen_mask = np.zeros((h, w), dtype=np.uint8)
                if geom["inner_r"] > 0:
                    inner_pts = _rounded_rect_contour(scr_x, scr_y, scr_w, scr_h, geom["inner_r"])
                    cv2.fillPoly(screen_mask, [inner_pts], 255)
                else:
                    screen_mask[scr_y:scr_y + scr_h, scr_x:scr_x + scr_w] = 255
                base_canvas[screen_mask > 0] = 0

            # Pre-compute device region mask for zoom compositing
            device_mask_u8 = (np.any(base_canvas != bg, axis=2)
                              .astype(np.uint8) * 255)

        return GeometryResult(
            scr_x=scr_x,
            scr_y=scr_y,
            scr_w=scr_w,
            scr_h=scr_h,
            base_canvas=base_canvas,
            screen_mask=screen_mask,
            device_mask_u8=device_mask_u8,
            bg=bg,
        )

    def _run(
        self,
        input_path: str,
        output_path: str,
        keyframes: List[ZoomKeyframe],
        actual_fps: float,
        mouse_track: List[MousePosition],
        monitor_rect: dict,
        bg_preset: BackgroundPreset,
        frame_preset: FramePreset,
        click_events: List[ClickEvent],
        click_preset: ClickEffectPreset,
        output_dim=None,
        duration_ms: float = 0.0,
        frame_timestamps: Optional[List[float]] = None,
        trim_start_ms: float = 0.0,
        trim_end_ms: float = 0.0,
        encoder_id: str = "libx264",
        voiceover_segments: Optional[List[VoiceoverSegment]] = None,
        video_segments: Optional[List[VideoSegment]] = None,
        chapters: Optional[List] = None,
    ) -> None:
        """Execute the full export algorithm on a worker thread.

        High-level phases:
        1. Probe source metadata and reconcile FPS/frame-count uncertainty.
        2. Precompute static composition layers (background, bezel masks).
        3. Prepare audio (optional voiceover merge).
        4. Render frames on a deterministic CFR output timeline:
           source timestamp lookup -> zoom/cursor/click -> compose -> queue.
        5. Encode via ffmpeg with hardware fallback chain.

        Error handling strategy:
        - Recover from unsupported HW encoders by retrying others.
        - Catch pipe errors from ffmpeg stdin writes.
        - Emit user-facing signal messages instead of raising to UI thread.
        """
        proc: subprocess.Popen | None = None
        cap: cv2.VideoCapture | None = None
        _merged_audio_path: str = ""
        try:
            if self._status_cb: self._status_cb("Preparing video…")
            cap = cv2.VideoCapture(input_path)
            if not cap.isOpened():
                if self._error_cb: self._error_cb(f"Cannot open {input_path}")
                return

            # Phase 1: Probe video metadata
            probe = self._probe_video(
                cap, actual_fps, duration_ms, frame_timestamps,
                output_dim, output_path
            )
            if probe is None:
                return  # Error already emitted

            w, h = probe.out_w, probe.out_h
            src_fps = probe.src_fps
            total_frames = probe.total_frames
            fps = probe.fps
            _is_gif = probe.is_gif

            # Update output_path extension if needed
            if not _is_gif and not output_path.lower().endswith(".mp4"):
                output_path = output_path.rsplit(".", 1)[0] + ".mp4"

            # Phase 2: Compute geometry and build static layers
            geom = self._compute_geometry(probe, bg_preset, frame_preset)
            scr_x, scr_y, scr_w, scr_h = geom.scr_x, geom.scr_y, geom.scr_w, geom.scr_h
            base_canvas = geom.base_canvas
            screen_mask = geom.screen_mask
            _device_mask_u8 = geom.device_mask_u8
            bg = geom.bg

            if w < 2 or h < 2:
                if self._error_cb: self._error_cb("Output dimensions too small for encoding")
                return

            # Pipe raw BGR frames to ffmpeg for encoding (H.264 or GIF)
            ffmpeg = _ffmpeg_exe()
            original_encoder_id = encoder_id

            # Build merged audio from voiceover segments (if any)
            _has_audio = False
            if voiceover_segments and not _is_gif:
                ready = [s for s in voiceover_segments if s.audio_path and os.path.isfile(s.audio_path)]
                if ready:
                    if self._status_cb: self._status_cb(f"Merging {len(ready)} voiceover segment(s)\u2026")
                    _merged_audio_path = _merge_voiceover_segments(
                        ready, duration_ms, trim_start_ms, trim_end_ms, ffmpeg
                    )
                    _has_audio = bool(_merged_audio_path) and os.path.isfile(_merged_audio_path)
                    if _has_audio:
                        logger.info("Voiceover audio ready: %s", _merged_audio_path)
                    else:
                        logger.warning("Voiceover merge produced no output, exporting without audio")

            # Build chapter metadata file (if chapters exist and not GIF)
            _chapters_metadata_path = ""
            if chapters and not _is_gif:
                import tempfile
                # Create metadata file in the same directory as the output
                output_dir = os.path.dirname(output_path) or "."
                fd, _chapters_metadata_path = tempfile.mkstemp(
                    suffix=".txt", prefix="chapters_", dir=output_dir
                )
                try:
                    # Compute trimmed video end time for last chapter's END
                    if trim_end_ms > 0:
                        video_end_ms_trimmed = int(trim_end_ms - trim_start_ms)
                    elif duration_ms > 0:
                        video_end_ms_trimmed = int(duration_ms - trim_start_ms)
                    else:
                        video_end_ms_trimmed = 0

                    # Collect valid chapters with adjusted start times
                    valid_chapters = []
                    for chapter in sorted(chapters, key=lambda c: c.timestamp_ms):
                        chap_ms = chapter.timestamp_ms - trim_start_ms
                        if chap_ms < 0:
                            continue  # Chapter is before trim start
                        if trim_end_ms > 0 and chapter.timestamp_ms > trim_end_ms:
                            continue  # Chapter is after trim end
                        valid_chapters.append((int(chap_ms), chapter.name))

                    with os.fdopen(fd, "w", encoding="utf-8") as f:
                        f.write(";FFMETADATA1\n")
                        for idx, (start_ms, name) in enumerate(valid_chapters):
                            # END = next chapter's start, or video end for last
                            if idx + 1 < len(valid_chapters):
                                end_ms = valid_chapters[idx + 1][0]
                            elif video_end_ms_trimmed > start_ms:
                                end_ms = video_end_ms_trimmed
                            else:
                                end_ms = start_ms + 1000  # fallback
                            # Sanitize chapter name for ffmetadata format:
                            # escape special chars and strip newlines.
                            safe_name = name.replace("\\", "\\\\").replace("=", "\\=").replace(";", "\\;").replace("#", "\\#").replace("\n", " ")
                            f.write("[CHAPTER]\n")
                            f.write(f"TIMEBASE=1/1000\n")
                            f.write(f"START={start_ms}\n")
                            f.write(f"END={end_ms}\n")
                            f.write(f"title={safe_name}\n")
                    logger.info("Chapter metadata file created: %s", _chapters_metadata_path)
                except Exception as exc:
                    logger.warning("Failed to create chapter metadata: %s", exc)
                    if _chapters_metadata_path and os.path.exists(_chapters_metadata_path):
                        try:
                            os.remove(_chapters_metadata_path)
                        except Exception:
                            pass
                    _chapters_metadata_path = ""

            def _launch_ffmpeg(enc_id: str) -> subprocess.Popen:
                """Start ffmpeg process configured for current export mode.

                MP4 mode:
                - receives raw BGR frames on stdin,
                - encodes using selected H.264 encoder args,
                - optionally muxes merged voiceover WAV.

                GIF mode:
                - receives raw BGR frames on stdin,
                - applies palettegen/paletteuse filtergraph.
                """
                if _is_gif:
                    gif_args = _build_gif_args()
                    cmd = [
                        ffmpeg, "-y",
                        "-f", "rawvideo",
                        "-vcodec", "rawvideo",
                        "-s", f"{w}x{h}",
                        "-pix_fmt", "bgr24",
                        "-r", str(fps),
                        "-i", "pipe:",
                    ] + gif_args + [
                        output_path,
                    ]
                    logger.info("Launching ffmpeg for GIF export: %s", " ".join(cmd))
                else:
                    enc_args = _build_encoder_args(enc_id)
                    cmd = [
                        ffmpeg, "-y",
                        "-f", "rawvideo",
                        "-vcodec", "rawvideo",
                        "-s", f"{w}x{h}",
                        "-pix_fmt", "bgr24",
                        "-r", str(fps),
                        "-i", "pipe:",
                    ]
                    # Add merged audio input if available
                    if _has_audio:
                        cmd += ["-i", _merged_audio_path]
                    # Add chapter metadata if available
                    if _chapters_metadata_path:
                        cmd += ["-f", "ffmetadata", "-i", _chapters_metadata_path]
                        chap_input_idx = 2 if _has_audio else 1
                        cmd += ["-map_metadata", str(chap_input_idx)]
                        cmd += ["-map_chapters", str(chap_input_idx)]
                    cmd += enc_args
                    if _has_audio:
                        cmd += ["-c:a", "aac", "-b:a", "192k"]
                    cmd += [output_path]
                    logger.info("Launching ffmpeg with encoder %s: %s", enc_id, " ".join(cmd))
                return subprocess.Popen(
                    cmd,
                    stdin=subprocess.PIPE,
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.PIPE,
                    **_subprocess_kwargs(),
                )

            def _encode_frames(proc: subprocess.Popen) -> bool:
                """Render and feed frames to ffmpeg using producer/consumer flow.

                Timeline algorithm
                ------------------
                - Build ``source_timestamps`` from recorded frame timestamps.
                - Iterate output timeline in fixed steps: ``t = start + n/fps``.
                - For each output time ``t``:
                    - binary-search source frame index active at ``t``,
                    - advance OpenCV decoder up to that index,
                    - render overlays/composition evaluated at ``t``.

                Concurrency algorithm
                ---------------------
                - Producer (this thread): compose ndarray -> bytes -> queue.
                - Consumer (writer thread): queue -> ``proc.stdin.write``.

                This overlap prevents encoder starvation and keeps export
                throughput high without sacrificing timeline correctness.
                """
                nonlocal frame_timestamps  # read-only access

                engine = ZoomEngine()
                for kf in keyframes:
                    engine.add_keyframe(kf)

                # Pre-build cursor template for overlay
                # Use scr_h (screen area height) with same factor as preview
                # compositor (screen_rect_h * 0.032) for visual consistency
                cursor_h_px = max(16, int(scr_h * 0.032))
                c_bgr, c_alpha = _build_cursor_template(cursor_h_px)
                m_left = monitor_rect.get("left", 0)
                m_top = monitor_rect.get("top", 0)
                m_w = max(monitor_rect.get("width", w), 1)
                m_h = max(monitor_rect.get("height", h), 1)
                _has_cursor = len(mouse_track) > 0 and m_w > 0
                _has_clicks = len(click_events) > 0 and m_w > 0

                # Build source-frame timestamps used to map output timeline
                # time -> source frame index.  This mirrors preview playback,
                # which also picks frames from per-frame timestamps.
                if frame_timestamps:
                    source_timestamps = []
                    last_ts = 0.0
                    for t in frame_timestamps[:total_frames]:
                        ts = float(t)
                        if ts < last_ts:
                            ts = last_ts
                        source_timestamps.append(ts)
                        last_ts = ts
                else:
                    source_timestamps = [
                        (i / src_fps) * 1000.0 for i in range(total_frames)
                    ]
                if not source_timestamps:
                    return False

                # ── Pipeline: compositor → queue → writer thread → ffmpeg ──
                _QUEUE_DEPTH = 16
                frame_q: queue.Queue = queue.Queue(maxsize=_QUEUE_DEPTH)
                pipe_err = threading.Event()

                def _pipe_writer() -> None:
                    """Drain frame queue into ffmpeg's stdin pipe."""
                    while True:
                        data = frame_q.get()
                        if data is None:
                            break
                        try:
                            proc.stdin.write(data)
                        except (BrokenPipeError, OSError, ValueError):
                            pipe_err.set()
                            break

                writer_t = threading.Thread(target=_pipe_writer, daemon=True)
                writer_t.start()

                def _enqueue(data: bytes) -> bool:
                    """Put frame data into the queue.  Returns False on pipe error."""
                    while not pipe_err.is_set():
                        try:
                            frame_q.put(data, timeout=0.5)
                            return True
                        except queue.Full:
                            continue
                    return False

                cap.set(cv2.CAP_PROP_POS_FRAMES, 0)
                exported = 0
                ret, first_frame = cap.read()
                if not ret:
                    frame_q.put(None)
                    writer_t.join(timeout=5)
                    return False
                src_idx = 0
                last_f = first_frame
                _needs_overlay = _has_cursor or _has_clicks

                eff_ts = trim_start_ms if trim_start_ms > 0 else 0.0
                if trim_end_ms > 0:
                    eff_te = trim_end_ms
                elif duration_ms > 0:
                    eff_te = duration_ms
                else:
                    eff_te = source_timestamps[-1]
                if eff_te < eff_ts:
                    eff_te = eff_ts

                # Build a sorted list of (start, end) time ranges from video
                # segments.  Only frames whose source timestamp falls inside
                # one of these ranges will be exported (ripple delete).
                _seg_ranges: list[tuple[float, float]] = []
                _seg_starts: list[float] = []  # pre-extracted for bisect
                if video_segments:
                    _seg_ranges = sorted(
                        (s.start_ms, s.end_ms) for s in video_segments
                    )
                    _seg_starts = [s for s, _ in _seg_ranges]

                # Move decoder to the source frame active at trim start.
                start_src_idx = max(0, bisect.bisect_right(source_timestamps, eff_ts) - 1)
                start_src_idx = min(start_src_idx, len(source_timestamps) - 1)
                while src_idx < start_src_idx:
                    ret, frame = cap.read()
                    if not ret:
                        break
                    src_idx += 1
                    last_f = frame

                t_total = max(1, int(engine.compute_output_duration(
                    duration_ms or eff_te, eff_ts, eff_te,
                ) / 1000.0 * fps) + 1)
                out_idx = 0
                t_ms = eff_ts  # recording-time cursor (advances by speed-adjusted intervals)

                # Pre-allocate reusable frame buffers to avoid per-frame copies
                _buf_canvas = base_canvas.copy()
                _buf_result = bg.copy() if bg is not None else None

                while True:
                    if t_ms > eff_te + 0.0001:
                        break

                    # Skip frames outside any video segment (ripple delete).
                    # Uses half-open intervals [start, end) for interior
                    # segments; the last segment's end is inclusive to
                    # avoid clipping the final frame.
                    # Uses bisect for O(log n) membership check.
                    if _seg_ranges:
                        in_seg = False
                        # Binary search: find the last segment whose start <= t_ms
                        idx = bisect.bisect_right(_seg_starts, t_ms) - 1
                        if idx >= 0:
                            s, e = _seg_ranges[idx]
                            # Last segment uses inclusive end [s, e]; others use [s, e)
                            if idx == len(_seg_ranges) - 1:
                                in_seg = (s <= t_ms <= e)
                            else:
                                in_seg = (s <= t_ms < e)
                        if not in_seg:
                            # Jump to the start of the next segment to avoid
                            # spinning on the same t_ms (which never advances
                            # and would loop forever).
                            next_seg = bisect.bisect_right(_seg_starts, t_ms)
                            if next_seg < len(_seg_starts):
                                t_ms = _seg_starts[next_seg]
                            else:
                                break  # no more segments
                            continue

                    # Pick the source frame for this output timestamp
                    target_src_idx = max(0, bisect.bisect_right(source_timestamps, t_ms) - 1)
                    target_src_idx = min(target_src_idx, len(source_timestamps) - 1)

                    while src_idx < target_src_idx:
                        ret, frame = cap.read()
                        if not ret:
                            # Keep reusing the last decoded frame if the
                            # container has fewer readable frames than expected.
                            src_idx = target_src_idx
                            break
                        src_idx += 1
                        last_f = frame

                    # Only copy when overlays will draw in-place on this frame
                    frame = last_f.copy() if _needs_overlay else last_f

                    zoom, px, py = engine.compute_at(t_ms)

                    if _has_cursor:
                        draw_cursor_cv(
                            frame, mouse_track, t_ms,
                            m_left, m_top, m_w, m_h,
                            c_bgr, c_alpha,
                        )
                    if _has_clicks:
                        draw_clicks_cv(
                            frame, click_events, t_ms,
                            m_left, m_top, m_w, m_h,
                            click_preset,
                        )
                    composed = _compose_cv(
                        frame, zoom, px, py, w, h,
                        base_canvas, screen_mask,
                        scr_x, scr_y, scr_w, scr_h,
                        zoom_video_only=frame_preset.is_none,
                        bg_canvas=bg,
                        device_mask_u8=_device_mask_u8,
                        _buf_canvas=_buf_canvas,
                        _buf_result=_buf_result,
                    )
                    if not _enqueue(composed.tobytes()):
                        break
                    exported += 1
                    out_idx += 1
                    # Advance recording-time cursor by speed-adjusted interval
                    seg_speed = engine.get_speed_at(t_ms, duration_ms or eff_te)
                    t_ms += (1.0 / fps) * 1000.0 * max(seg_speed, 0.01)

                    if exported % 10 == 0:
                        if self._progress_cb: self._progress_cb(min(1.0, exported / t_total))

                # Extra frames for zoom-out tail
                if last_f is not None and engine.keyframes and not pipe_err.is_set():
                    last_kf = engine.keyframes[-1]
                    end_time = last_kf.timestamp + last_kf.duration
                    # Use output-aligned time for the last exported frame
                    video_end_ms = eff_te
                    if trim_end_ms <= 0 and end_time > video_end_ms:
                        extra = int((end_time - video_end_ms) / 1000.0 * fps) + 1
                        for ei in range(extra):
                            t_ms = video_end_ms + ((ei + 1) / fps) * 1000.0
                            zoom, px, py = engine.compute_at(t_ms)
                            fc = last_f.copy()

                            if _has_cursor:
                                draw_cursor_cv(
                                    fc, mouse_track, t_ms,
                                    m_left, m_top, m_w, m_h,
                                    c_bgr, c_alpha,
                                )
                            if _has_clicks:
                                draw_clicks_cv(
                                    fc, click_events, t_ms,
                                    m_left, m_top, m_w, m_h,
                                    click_preset,
                                )
                            composed = _compose_cv(
                                fc, zoom, px, py, w, h,
                                base_canvas, screen_mask,
                                scr_x, scr_y, scr_w, scr_h,
                                zoom_video_only=frame_preset.is_none,
                                bg_canvas=bg,
                                device_mask_u8=_device_mask_u8,
                                _buf_canvas=_buf_canvas,
                                _buf_result=_buf_result,
                            )
                            if not _enqueue(composed.tobytes()):
                                break

                # Signal writer thread to finish and wait for it
                frame_q.put(None)
                writer_t.join(timeout=30)
                return not pipe_err.is_set()

            # ── Try encoding (with HW fallback chain for MP4; direct for GIF) ──

            if _is_gif:
                # GIF export uses palette-based encoding — no fallback chain
                if self._status_cb: self._status_cb("Rendering frames for GIF\u2026")
                proc = _launch_ffmpeg(encoder_id)
                pipe_ok = _encode_frames(proc)
                proc.stdin.close()
                if self._status_cb: self._status_cb("Generating GIF palette\u2026")
                # GIF palettegen buffers all frames before writing; allow more time
                try:
                    stderr_out = proc.communicate(timeout=300)[1]
                except subprocess.TimeoutExpired:
                    proc.kill()
                    try:
                        stderr_out = proc.communicate(timeout=5)[1]
                    except subprocess.TimeoutExpired:
                        logger.error("ffmpeg process still alive after kill+5s timeout (pid=%s)", proc.pid)
                        stderr_out = b""
                stderr_text = stderr_out.decode(errors="replace") if stderr_out else ""
                if proc.returncode != 0:
                    err_msg = stderr_text.strip()[-800:] if stderr_text else "Unknown ffmpeg error"
                    logger.error("GIF export failed (rc=%s): %s", proc.returncode, err_msg)
                    if self._error_cb: self._error_cb(f"GIF export error: {err_msg[:500]}")
                    return
                if self._progress_cb: self._progress_cb(1.0)
                if self._finished_cb: self._finished_cb(output_path)
                return

            # ── MP4: try encoding with HW fallback chain ──────────────
            #
            # Build a fallback chain: try other available HW encoders
            # before falling back to software (libx264).
            from .utils import detect_available_encoders, encoder_display_name
            enc_label = encoder_display_name(encoder_id)
            if self._status_cb: self._status_cb(f"Encoding with {enc_label}…")
            available = detect_available_encoders()
            # Build chain: encoders after the current one in preference order
            _fallback_chain: List[str] = []
            if encoder_id in available:
                idx = available.index(encoder_id)
                _fallback_chain = available[idx + 1:]
            elif encoder_id != "libx264":
                _fallback_chain = [e for e in available if e != encoder_id]
            # Ensure libx264 is always at the end
            if "libx264" not in _fallback_chain:
                _fallback_chain.append("libx264")

            proc = _launch_ffmpeg(encoder_id)

            def _kill_proc(p: subprocess.Popen) -> None:
                """Safely terminate an ffmpeg process and close its pipes."""
                if p is None:
                    return
                try:
                    if p.stdin and not p.stdin.closed:
                        p.stdin.close()
                except OSError:
                    pass
                try:
                    p.kill()
                    p.wait(timeout=5)
                except (OSError, subprocess.TimeoutExpired):
                    pass
                try:
                    if p.stderr and not p.stderr.closed:
                        p.stderr.close()
                except OSError:
                    pass

            # Check for immediate launch failure
            import time as _time
            _time.sleep(0.1)
            if proc.poll() is not None and encoder_id != "libx264":
                stderr_early = proc.stderr.read().decode(errors="replace")[:500] if proc.stderr else ""
                logger.warning(
                    "Encoder %s failed immediately (%s)",
                    encoder_id, stderr_early.strip(),
                )
                # Try next in fallback chain
                launched = False
                for fallback_id in _fallback_chain:
                    fb_name = encoder_display_name(fallback_id)
                    if self._status_cb: self._status_cb(f"{encoder_display_name(encoder_id)} failed, trying {fb_name}\u2026")
                    logger.info("Trying fallback encoder: %s", fallback_id)
                    encoder_id = fallback_id
                    _kill_proc(proc)
                    proc = _launch_ffmpeg(encoder_id)
                    _time.sleep(0.1)
                    if proc.poll() is None:
                        launched = True
                        break
                    else:
                        logger.warning("Fallback encoder %s also failed immediately", encoder_id)
                if not launched:
                    _kill_proc(proc)
                    if self._error_cb: self._error_cb("All encoders failed to launch")
                    return

            pipe_ok = _encode_frames(proc)

            proc.stdin.close()
            if self._status_cb: self._status_cb("Finalizing…")
            try:
                stderr_out = proc.communicate(timeout=60)[1]
            except subprocess.TimeoutExpired:
                proc.kill()
                try:
                    stderr_out = proc.communicate(timeout=5)[1]
                except subprocess.TimeoutExpired:
                    logger.error("ffmpeg process still alive after kill+5s timeout (pid=%s)", proc.pid)
                    stderr_out = b""

            stderr_text = stderr_out.decode(errors="replace") if stderr_out else ""

            # If encoder failed mid-stream, try fallback chain
            if (proc.returncode != 0 or not pipe_ok) and encoder_id != "libx264":
                failed_id = encoder_id
                logger.warning(
                    "Encoder %s failed mid-export (rc=%s): %s",
                    failed_id, proc.returncode, stderr_text[:300].strip(),
                )
                # Try remaining encoders in fallback chain
                remaining = _fallback_chain[_fallback_chain.index(failed_id) + 1:] if failed_id in _fallback_chain else _fallback_chain
                if not remaining:
                    remaining = ["libx264"]

                for fallback_id in remaining:
                    fb_name = encoder_display_name(fallback_id)
                    if self._status_cb: self._status_cb(f"{encoder_display_name(failed_id)} failed mid-export, trying {fb_name}\u2026")
                    encoder_id = fallback_id
                    _kill_proc(proc)
                    proc = _launch_ffmpeg(encoder_id)
                    cap.set(cv2.CAP_PROP_POS_FRAMES, 0)
                    pipe_ok = _encode_frames(proc)
                    proc.stdin.close()
                    try:
                        stderr_out = proc.communicate(timeout=60)[1]
                    except subprocess.TimeoutExpired:
                        proc.kill()
                        try:
                            stderr_out = proc.communicate(timeout=5)[1]
                        except subprocess.TimeoutExpired:
                            logger.error("ffmpeg process still alive after kill+5s timeout (pid=%s)", proc.pid)
                            stderr_out = b""
                    stderr_text = stderr_out.decode(errors="replace") if stderr_out else ""
                    if proc.returncode == 0 and pipe_ok:
                        break  # success
                    failed_id = encoder_id
                    logger.warning("Fallback encoder %s also failed (rc=%s)", encoder_id, proc.returncode)

            if proc.returncode != 0:
                err_msg = stderr_text.strip()[-800:] if stderr_text else "Unknown ffmpeg error"
                logger.error("Export failed (encoder=%s, rc=%s): %s", encoder_id, proc.returncode, err_msg)
                if self._error_cb: self._error_cb(f"ffmpeg error ({encoder_id}): {err_msg[:500]}")
                return

            # Verify the output file exists and is non-trivial.
            # A valid MP4 is at least a few KB (moov atom + ftyp box).
            if not os.path.isfile(output_path):
                if self._error_cb: self._error_cb("Export produced no output file")
                return
            file_size = os.path.getsize(output_path)
            if file_size < 1024:
                logger.error("Export file suspiciously small (%d bytes): %s", file_size, output_path)
                if self._error_cb: self._error_cb("Export failed: output file is empty or corrupt")
                return

            if encoder_id != original_encoder_id:
                logger.info("Export completed with fallback encoder %s (originally %s)", encoder_id, original_encoder_id)

            if self._progress_cb: self._progress_cb(1.0)
            if self._finished_cb: self._finished_cb(output_path)

        except Exception as exc:
            if self._error_cb: self._error_cb(str(exc))
        finally:
            # Ensure cv2.VideoCapture is released
            if cap is not None:
                try:
                    cap.release()
                    logger.debug("cv2.VideoCapture released")
                except Exception as e:
                    logger.warning("Failed to release cv2.VideoCapture: %s", e)
            # Ensure ffmpeg process is not leaked on any error path
            if proc is not None and proc.poll() is None:
                logger.warning("Cleaning up orphaned ffmpeg process (pid=%s)", proc.pid)
                _kill_proc(proc)
            # Clean up merged voiceover temp file
            if _merged_audio_path and os.path.isfile(_merged_audio_path):
                try:
                    os.remove(_merged_audio_path)
                except OSError:
                    pass
            # Clean up chapter metadata temp file
            if _chapters_metadata_path and os.path.isfile(_chapters_metadata_path):
                try:
                    os.remove(_chapters_metadata_path)
                except OSError:
                    pass


