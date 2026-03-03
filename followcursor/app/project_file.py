"""Project file management — save / load .fcproj bundles.

A .fcproj file is a ZIP archive containing:
  - project.json   — session metadata (mouse track, keyframes, key events, etc.)
  - recording.avi  — the raw MJPG intermediate video

This lets users save their work and resume editing later.
"""

import json
import logging
import os
import shutil
import struct
import tempfile
import time
import zipfile
import zlib
from typing import Optional

from .models import RecordingSession
from .backgrounds import BackgroundPreset
from .frames import FramePreset

logger = logging.getLogger(__name__)

PROJ_EXT = ".fcproj"
_JSON_NAME = "project.json"
_VIDEO_NAME = "recording.avi"


def save_project(
    output_path: str,
    video_path: str,
    session: RecordingSession,
    monitor_rect: Optional[dict] = None,
    actual_fps: float = 30.0,
    bg_preset: Optional[BackgroundPreset] = None,
    frame_preset: Optional[FramePreset] = None,
    metadata_only: bool = False,
) -> str:
    """Bundle session + raw video into a .fcproj ZIP file.

    When *metadata_only* is True and the output file already exists,
    the save is optimised to avoid re-reading or re-copying the large
    video entry:

    1. **In-place rewrite** (preferred) — if the video is the first
       entry at offset 0, everything after the video's raw data is
       replaced with a fresh JSON entry, central directory, and EOCD
       record.  Total write is O(JSON), the multi-MB video is never
       read or copied.
    2. **Streaming copy** (fallback) — if the layout doesn't allow
       in-place rewrite, the video is streamed in 8 MB chunks to a new
       ZIP (no huge single allocation).

    Returns the final output path.
    """
    if not output_path.lower().endswith(PROJ_EXT):
        output_path += PROJ_EXT

    # Build project JSON (session data + extras)
    data = json.loads(session.to_json())
    if monitor_rect:
        data["monitorRect"] = monitor_rect
    data["actualFps"] = actual_fps
    if bg_preset:
        data["bgPreset"] = bg_preset.to_dict()
    if frame_preset:
        data["framePreset"] = frame_preset.to_dict()

    json_str = json.dumps(data, indent=2)

    if metadata_only and os.path.isfile(output_path):
        t0 = time.perf_counter()
        if _fast_metadata_rewrite(output_path, json_str):
            logger.info(
                "Metadata save (in-place): %.1f ms",
                (time.perf_counter() - t0) * 1000,
            )
            return output_path
        # Fall back to streaming copy (old-layout files or errors)
        logger.info("In-place rewrite unavailable, falling back to streaming copy")
        _streaming_metadata_save(output_path, json_str)
        logger.info(
            "Metadata save (streaming): %.1f ms",
            (time.perf_counter() - t0) * 1000,
        )
        return output_path

    # Full save — video first so future metadata saves can use in-place rewrite
    with zipfile.ZipFile(output_path, "w", zipfile.ZIP_STORED) as zf:
        if video_path and os.path.isfile(video_path):
            zf.write(video_path, _VIDEO_NAME)
        zf.writestr(_JSON_NAME, json_str)

    return output_path


# ── fast metadata helpers ───────────────────────────────────────────


def _fast_metadata_rewrite(zip_path: str, json_str: str) -> bool:
    """Rewrite only the JSON in a .fcproj ZIP without touching video data.

    Requires the video entry to be the first entry at offset 0 — the
    layout produced by ``save_project`` full saves.  Everything after
    the video's raw data is replaced with a fresh JSON local-file-header,
    central directory, and end-of-central-directory record.

    Returns True on success, False when a fallback is needed.
    """
    try:
        # ── Validate layout ─────────────────────────────────────────
        with zipfile.ZipFile(zip_path, "r") as zf:
            if _VIDEO_NAME not in zf.namelist():
                return False
            vi = zf.getinfo(_VIDEO_NAME)
            if vi.header_offset != 0:
                return False          # video not first — can't truncate
            if vi.file_size >= 0x7FFFFFFF:
                return False          # ZIP64 territory — fall back

        # Parse the actual local-file-header to get field lengths
        with open(zip_path, "rb") as f:
            lfh = f.read(30)
        if lfh[:4] != b"PK\x03\x04":
            return False
        fn_len, extra_len = struct.unpack_from("<HH", lfh, 26)
        video_end = 30 + fn_len + extra_len + vi.compress_size

        # ── Build new tail (JSON + CD + EOCD) ───────────────────────
        json_raw = json_str.encode("utf-8")
        json_crc = zlib.crc32(json_raw) & 0xFFFFFFFF
        jfn = _JSON_NAME.encode("utf-8")
        vfn = _VIDEO_NAME.encode("utf-8")

        buf = bytearray()

        # JSON local-file-header + filename + data
        json_lfh_offset = video_end
        buf += struct.pack(
            "<4sHHHHHIIIHH",
            b"PK\x03\x04", 20, 0, 0, 0, 0,
            json_crc, len(json_raw), len(json_raw), len(jfn), 0,
        )
        buf += jfn
        buf += json_raw

        # Central directory
        cd_offset = video_end + len(buf)
        cd_start = len(buf)

        # Video CD entry
        buf += struct.pack(
            "<4sHHHHHHIIIHHHHHII",
            b"PK\x01\x02", 20, 20, 0, 0, 0, 0,
            vi.CRC, vi.compress_size, vi.file_size,
            len(vfn), 0, 0, 0, 0, 0, 0,
        )
        buf += vfn

        # JSON CD entry
        buf += struct.pack(
            "<4sHHHHHHIIIHHHHHII",
            b"PK\x01\x02", 20, 20, 0, 0, 0, 0,
            json_crc, len(json_raw), len(json_raw),
            len(jfn), 0, 0, 0, 0, 0, json_lfh_offset,
        )
        buf += jfn

        cd_size = len(buf) - cd_start

        # End of central directory
        buf += struct.pack(
            "<4sHHHHIIH",
            b"PK\x05\x06", 0, 0, 2, 2,
            cd_size, cd_offset, 0,
        )

        # ── Write in-place ──────────────────────────────────────────
        with open(zip_path, "r+b") as f:
            f.seek(video_end)
            f.write(bytes(buf))
            f.truncate()

        return True
    except Exception:
        logger.exception("Fast metadata rewrite failed, will use fallback")
        return False


def _streaming_metadata_save(zip_path: str, json_str: str) -> None:
    """Rewrite project ZIP with updated JSON, streaming video in chunks.

    Writes video first so future saves can use the fast in-place rewrite.
    """
    tmp_path = zip_path + ".tmp"
    try:
        with zipfile.ZipFile(zip_path, "r") as zf_old, \
             zipfile.ZipFile(tmp_path, "w", zipfile.ZIP_STORED) as zf_new:
            if _VIDEO_NAME in zf_old.namelist():
                with zf_old.open(_VIDEO_NAME) as src, \
                     zf_new.open(_VIDEO_NAME, "w") as dst:
                    shutil.copyfileobj(src, dst, length=8 * 1024 * 1024)
            zf_new.writestr(_JSON_NAME, json_str)
        os.replace(tmp_path, zip_path)
    except Exception:
        if os.path.exists(tmp_path):
            os.remove(tmp_path)
        raise


def load_project(input_path: str) -> dict:
    """Extract a .fcproj ZIP and return all project data.

    Returns dict with keys:
      - session: RecordingSession
      - video_path: str — path to extracted AVI (in temp dir)
      - monitor_rect: dict | None
      - actual_fps: float
    """
    if not zipfile.is_zipfile(input_path):
        raise ValueError(f"Not a valid project file: {input_path}")

    # Extract to a temp directory
    extract_dir = tempfile.mkdtemp(prefix="followcursor_proj_")

    with zipfile.ZipFile(input_path, "r") as zf:
        zf.extractall(extract_dir)

    json_path = os.path.join(extract_dir, _JSON_NAME)
    video_path = os.path.join(extract_dir, _VIDEO_NAME)

    if not os.path.isfile(json_path):
        raise ValueError(f"Project file missing {_JSON_NAME}")

    with open(json_path, "r", encoding="utf-8") as f:
        data = json.loads(f.read())

    session = RecordingSession.from_json(json.dumps(data))

    monitor_rect = data.get("monitorRect")
    actual_fps = data.get("actualFps", 30.0)

    bg_preset = None
    if "bgPreset" in data:
        try:
            bg_preset = BackgroundPreset.from_dict(data["bgPreset"])
        except Exception:
            pass

    frame_preset = None
    if "framePreset" in data:
        try:
            frame_preset = FramePreset.from_dict(data["framePreset"])
        except Exception:
            pass

    return {
        "session": session,
        "video_path": video_path if os.path.isfile(video_path) else "",
        "monitor_rect": monitor_rect,
        "actual_fps": actual_fps,
        "bg_preset": bg_preset,
        "frame_preset": frame_preset,
    }
