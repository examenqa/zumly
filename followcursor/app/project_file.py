"""Project file management — save / load .fcproj bundles.

A .fcproj file is a ZIP archive containing:
  - project.json   — session metadata (mouse track, keyframes, key events, etc.)
  - recording.avi  — the raw MJPG intermediate video

This lets users save their work and resume editing later.
"""

import json
import os
import tempfile
import zipfile
from typing import Optional

from .models import RecordingSession
from .backgrounds import BackgroundPreset
from .frames import FramePreset


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
    only the project.json is rewritten — the existing video entry is
    copied byte-for-byte from the old ZIP, avoiding an expensive
    re-read of the source AVI.

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

    json_bytes = json.dumps(data, indent=2)

    if metadata_only and os.path.isfile(output_path):
        # Fast path: rewrite JSON, carry over the video entry untouched
        tmp_path = output_path + ".tmp"
        try:
            with zipfile.ZipFile(output_path, "r") as zf_old, \
                 zipfile.ZipFile(tmp_path, "w", zipfile.ZIP_STORED) as zf_new:
                zf_new.writestr(_JSON_NAME, json_bytes)
                if _VIDEO_NAME in zf_old.namelist():
                    video_info = zf_old.getinfo(_VIDEO_NAME)
                    zf_new.writestr(video_info, zf_old.read(_VIDEO_NAME))
            # Atomic-ish replace
            os.replace(tmp_path, output_path)
        except Exception:
            # If anything went wrong, fall through to full save
            if os.path.exists(tmp_path):
                os.remove(tmp_path)
            raise
    else:
        with zipfile.ZipFile(output_path, "w", zipfile.ZIP_STORED) as zf:
            zf.writestr(_JSON_NAME, json_bytes)
            if video_path and os.path.isfile(video_path):
                zf.write(video_path, _VIDEO_NAME)

    return output_path


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
