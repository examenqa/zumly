import json
import logging
from typing import Optional

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QFrame, QPushButton, QLabel, QSpacerItem, QSizePolicy
)

from .editor_panel import EditorPanel
from .preview_widget import PreviewWidget
from .timeline_widget import TimelineWidget
from ..models import HighlightBox, RecordingSession, VideoSegment
from ..theme import get_theme
from .. import tokens as T

logger = logging.getLogger(__name__)

class TitleBar(QFrame):
    export_clicked = Signal()
    discard_clicked = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("TitleBar")
        self.setFixedHeight(48)
        
        layout = QHBoxLayout(self)
        layout.setContentsMargins(16, 0, 16, 0)
        
        self.lbl_title = QLabel("Zumly Editor")
        self.lbl_title.setObjectName("TitleBarLogo")
        layout.addWidget(self.lbl_title)
        
        layout.addSpacerItem(QSpacerItem(40, 20, QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Minimum))
        
        self.btn_discard = QPushButton("Discard")
        self.btn_discard.setObjectName("DiscardBtn")
        self.btn_discard.clicked.connect(self.discard_clicked.emit)
        layout.addWidget(self.btn_discard)
        
        self.btn_export = QPushButton("Export")
        self.btn_export.setObjectName("ExportBtn")
        self.btn_export.clicked.connect(self.export_clicked.emit)
        layout.addWidget(self.btn_export)


class EditorWindow(QWidget):
    def __init__(self, project_path: str):
        super().__init__()
        self.setWindowTitle("Zumly Editor")
        self.setMinimumSize(900, 600)
        self.resize(1200, 800)
        
        self.setStyleSheet(get_theme(dark=True))
        
        self._project_path = project_path
        
        # Build UI
        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)
        
        self._title_bar = TitleBar(self)
        root.addWidget(self._title_bar)
        
        content = QHBoxLayout()
        content.setContentsMargins(0, 0, 0, 0)
        content.setSpacing(0)
        
        center = QVBoxLayout()
        center.setContentsMargins(0, 0, 0, 0)
        center.setSpacing(0)
        
        preview_area = QWidget()
        preview_area.setObjectName("PreviewArea")
        preview_area.setStyleSheet(f"background-color: {T.SURFACE_BASE};")
        preview_layout = QVBoxLayout(preview_area)
        preview_layout.setContentsMargins(4, 4, 4, 0)
        
        self._preview = PreviewWidget()
        preview_layout.addWidget(self._preview, 1)
        center.addWidget(preview_area, 1)
        
        # 1px DIVIDER between preview area and timeline
        h_divider = QFrame()
        h_divider.setFixedHeight(1)
        h_divider.setStyleSheet(f"background-color: {T.DIVIDER}; border: none;")
        center.addWidget(h_divider)
        
        self._timeline = TimelineWidget()
        self._timeline.setStyleSheet(f"background-color: {T.SURFACE_ELEVATED};")
        center.addWidget(self._timeline)
        
        content.addLayout(center, 1)
        
        # 1px DIVIDER between center and right panel
        v_divider = QFrame()
        v_divider.setFixedWidth(1)
        v_divider.setStyleSheet(f"background-color: {T.DIVIDER}; border: none;")
        content.addWidget(v_divider)
        
        self._editor = EditorPanel()
        content.addWidget(self._editor)
        
        root.addLayout(content)
        
        # Signal wiring
        self._editor.background_changed.connect(self._on_background_changed)
        self._editor.frame_changed.connect(self._on_frame_changed)
        self._editor.click_effect_changed.connect(self._on_click_effect_changed)
        self._editor.output_dimensions_changed.connect(self._on_output_dimensions_changed)
        self._editor.segment_speed_changed.connect(self._on_segment_speed_changed)
        self._editor.segment_cut_requested.connect(self._on_segment_cut_requested)
        self._editor.segment_copy_requested.connect(self._on_segment_copy_requested)
        self._editor.segment_paste_requested.connect(self._on_segment_paste_requested)
        self._editor.segment_delete_requested.connect(self._on_segment_delete_requested)
        self._editor.highlight_add_requested.connect(self._on_highlight_add_requested)
        self._editor.undo_requested.connect(self._on_undo_requested)
        self._editor.redo_requested.connect(self._on_redo_requested)
        self._preview.play_pause_requested.connect(self._toggle_playback)
        self._preview.highlight_picked.connect(self._on_highlight_picked)
        self._timeline.segment_selected.connect(self._on_segment_selected)
        self._timeline.split_requested.connect(self._on_split_requested)
        self._timeline.range_selection_requested.connect(self._on_range_selection_requested)
        self._timeline.video_segment_deleted.connect(self._on_video_segment_deleted)
        self._timeline.keyframe_moved.connect(self._on_zoom_keyframe_moved)
        self._timeline.zoom_segment_moved.connect(self._on_zoom_segment_moved)
        self._timeline.segment_deleted.connect(self._on_zoom_segment_deleted)
        self._timeline.drag_finished.connect(self._on_timeline_drag_finished)

        self._title_bar.export_clicked.connect(self._on_export)
        self._title_bar.discard_clicked.connect(self.close)
        
        from zumly.app.zoom_engine import ZoomEngine
        self._zoom_engine = ZoomEngine()
        
        self._session = None
        self._project_data = {}
        self._selected_video_segment_index = -1
        self._video_segment_clipboard: VideoSegment | None = None
        self._pending_highlight_shape = "rect"
        self._timeline_drag_undo_pushed = False
        
        self._load_project()
        
    def _load_project(self):
        if not self._project_path:
            return
            
        logger.info(f"Loading project from {self._project_path}")
        try:
            import os
            with open(self._project_path, 'r', encoding='utf-8') as f:
                self._project_data = json.load(f)
                
            self._session = RecordingSession.from_json(json.dumps(self._project_data))
            
            if "videoPath" not in self._project_data or "outPath" not in self._project_data:
                raise ValueError("Project JSON missing required 'videoPath' or 'outPath' keys")
                
            video_path = self._project_data["videoPath"]
            if not os.path.isfile(video_path):
                raise ValueError(f"Video file not found at: {video_path}")
                
            logger.info(f"Project loaded successfully: Session ID {self._session.id}, {len(self._session.keyframes)} keyframes.")
            
            # Load zoom engine data
            self._zoom_engine.keyframes = self._session.keyframes
            self._zoom_engine.click_events = self._session.click_events
            self._zoom_engine.highlights = self._session.highlights or []
            if hasattr(self._session, 'voiceover_segments'):
                self._zoom_engine.voiceover_segments = self._session.voiceover_segments
            if hasattr(self._session, 'video_segments'):
                self._zoom_engine.video_segments = self._session.video_segments
            self._zoom_engine.clear_history()
            self._update_undo_redo_controls()
                
            # Load video into preview widget
            actual_fps = self._project_data.get("actualFps", 30.0)
            duration = self._preview.load_video(
                video_path,
                actual_fps=actual_fps,
                duration_ms=self._session.duration,
                frame_timestamps=self._session.frame_timestamps
            )
            self._ensure_video_segments(duration)
            self._zoom_engine.video_segments = self._session.video_segments
            self._preview.set_debug_keyframes(self._session.keyframes)
            self._preview.set_video_segments(self._session.video_segments)
            self._preview.set_highlights(self._session.highlights)
            self._preview.set_cursor_data(
                self._session.mouse_track,
                self._project_data.get("monitorRect", {}),
                self._session.click_events,
            )
            
            # Push loaded settings into UI and Preview
            if self._session.background_id:
                self._editor.set_background_by_name(self._session.background_id)
            if self._session.frame_id:
                self._editor.set_frame_by_name(self._session.frame_id)
            if self._session.click_effect_id:
                self._editor.set_click_effect_by_name(self._session.click_effect_id)
            self._preview.set_bg_preset(self._editor.bg_preset)
            self._preview.set_frame_preset(self._editor.frame_preset)
            self._preview.set_click_preset(self._editor.current_click_preset())
                
            # Load data into timeline
            self._timeline.set_data(
                duration=duration,
                current_time=0.0,
                keyframes=self._session.keyframes,
                mouse_track=self._session.mouse_track,
                key_events=self._session.key_events if hasattr(self._session, 'key_events') else None,
                click_events=self._session.click_events,
                trim_start_ms=0.0,
                trim_end_ms=duration,
                voiceover_segments=self._session.voiceover_segments if hasattr(self._session, 'voiceover_segments') else None,
                video_segments=self._session.video_segments if hasattr(self._session, 'video_segments') else None
            )
            if self._session.video_segments:
                self._sync_video_segments(0, save=False)
            self._refresh_editor_info()

            # Signal wiring between timeline and preview
            self._timeline.play_pause_clicked.connect(self._toggle_playback)
            self._timeline.seek_requested.connect(self._preview.seek_to)
            self._preview.playback_time_changed.connect(self._on_playback_time_changed)
            self._preview.playback_state_changed.connect(self._timeline.set_playing)
            
            # Initial state
            self._on_playback_time_changed(0.0)
            
        except Exception as e:
            logger.error(f"Failed to load project: {e}")
            import traceback
            traceback.print_exc()
            from PySide6.QtWidgets import QMessageBox
            QMessageBox.critical(self, "Load Error", f"Failed to load project state:\n{e}")

    def _on_playback_time_changed(self, time_ms: float):
        self._timeline.set_current_time(time_ms)
        zoom, px, py = self._zoom_engine.compute_at(time_ms)
        self._preview.set_zoom(zoom, px, py)

    def _sync_zoom_engine_from_session(self) -> None:
        if not self._session:
            return
        self._zoom_engine.keyframes = self._session.keyframes or []
        self._zoom_engine.click_events = self._session.click_events or []
        self._zoom_engine.video_segments = self._session.video_segments or []
        self._zoom_engine.voiceover_segments = self._session.voiceover_segments or []
        self._zoom_engine.highlights = self._session.highlights or []

    def _push_undo_snapshot(self, once_per_drag: bool = False) -> None:
        if not self._session:
            return
        if once_per_drag and self._timeline_drag_undo_pushed:
            return
        self._sync_zoom_engine_from_session()
        self._zoom_engine.push_undo()
        if once_per_drag:
            self._timeline_drag_undo_pushed = True
        self._update_undo_redo_controls()

    def _update_undo_redo_controls(self) -> None:
        if hasattr(self, "_editor") and hasattr(self, "_zoom_engine"):
            self._editor.set_undo_redo_enabled(
                self._zoom_engine.can_undo,
                self._zoom_engine.can_redo,
            )

    def _refresh_editor_info(self) -> None:
        if not self._session:
            return
        duration = getattr(self._preview, "_video_duration_ms", self._session.duration)
        self._editor.refresh(
            keyframes=self._session.keyframes or [],
            mouse_track=self._session.mouse_track or [],
            duration=duration,
            monitor_rect=self._project_data.get("monitorRect", {}),
            key_events=self._session.key_events,
            click_events=self._session.click_events,
            trim_start_ms=self._session.trim_start_ms,
            trim_end_ms=self._session.trim_end_ms,
        )

    def _apply_zoom_engine_state(self, save: bool = True) -> None:
        if not self._session:
            return
        self._session.keyframes = self._zoom_engine.keyframes
        self._session.click_events = self._zoom_engine.click_events
        self._session.video_segments = self._zoom_engine.video_segments
        self._session.voiceover_segments = self._zoom_engine.voiceover_segments
        self._session.highlights = self._zoom_engine.highlights
        self._preview.set_cursor_data(
            self._session.mouse_track,
            self._project_data.get("monitorRect", {}),
            self._session.click_events,
        )
        self._refresh_zoom_timeline(save=False)
        self._preview.set_highlights(self._session.highlights)
        self._sync_video_segments(self._selected_video_segment_index, save=False)
        self._refresh_editor_info()
        self._update_undo_redo_controls()
        if save:
            self._save_project()

    def _on_undo_requested(self) -> None:
        if self._zoom_engine.undo():
            self._apply_zoom_engine_state(save=True)

    def _on_redo_requested(self) -> None:
        if self._zoom_engine.redo():
            self._apply_zoom_engine_state(save=True)

    def _refresh_zoom_timeline(self, save: bool = False) -> None:
        if not self._session:
            return
        self._session.keyframes.sort(key=lambda k: k.timestamp)
        self._zoom_engine.keyframes = self._session.keyframes
        self._preview.set_debug_keyframes(self._session.keyframes)
        self._timeline.set_data(
            duration=getattr(self._preview, "_video_duration_ms", self._session.duration),
            current_time=getattr(self._preview, "_current_time_ms", 0.0),
            keyframes=self._session.keyframes,
            mouse_track=self._session.mouse_track,
            key_events=self._session.key_events if hasattr(self._session, "key_events") else None,
            click_events=self._session.click_events,
            trim_start_ms=0.0,
            trim_end_ms=getattr(self._preview, "_video_duration_ms", self._session.duration),
            voiceover_segments=self._session.voiceover_segments if hasattr(self._session, "voiceover_segments") else None,
            video_segments=self._session.video_segments if hasattr(self._session, "video_segments") else None,
        )
        self._on_playback_time_changed(getattr(self._preview, "_current_time_ms", 0.0))
        self._refresh_editor_info()
        if save:
            self._save_project()

    def _find_next_zoom_out_keyframe_index(self, start_index: int) -> int:
        if not self._session:
            return -1
        for idx in range(start_index + 1, len(self._session.keyframes)):
            if self._session.keyframes[idx].zoom <= 1.01:
                return idx
        return -1

    def _on_zoom_keyframe_moved(self, kf_id: str, new_time_ms: float) -> None:
        if not self._session:
            return
        self._push_undo_snapshot(once_per_drag=True)
        self._session.keyframes.sort(key=lambda k: k.timestamp)
        keyframes = self._session.keyframes
        idx = next((i for i, kf in enumerate(keyframes) if kf.id == kf_id), -1)
        if idx < 0:
            return

        duration = getattr(self._preview, "_video_duration_ms", self._session.duration)
        keyframe = keyframes[idx]
        min_gap = 100.0
        proposed = float(new_time_ms)

        if keyframe.zoom > 1.01:
            next_idx = self._find_next_zoom_out_keyframe_index(idx)
            min_time = 0.0
            if idx > 0:
                prev_kf = keyframes[idx - 1]
                if prev_kf.zoom <= 1.01:
                    min_time = prev_kf.timestamp + max(0.0, float(prev_kf.duration)) + min_gap
                else:
                    min_time = prev_kf.timestamp + min_gap
            max_time = float(duration)
            if next_idx >= 0:
                max_time = keyframes[next_idx].timestamp - min_gap
            keyframe.timestamp = max(min_time, min(proposed, max_time))
        else:
            prev_idx = idx - 1
            while prev_idx >= 0 and keyframes[prev_idx].zoom <= 1.01:
                prev_idx -= 1
            min_time = keyframes[prev_idx].timestamp + min_gap if prev_idx >= 0 else 0.0
            max_time = max(0.0, float(duration) - max(0.0, float(keyframe.duration)))
            next_idx = idx + 1
            if next_idx < len(keyframes):
                max_time = min(max_time, keyframes[next_idx].timestamp - max(0.0, float(keyframe.duration)) - min_gap)
            keyframe.timestamp = max(min_time, min(proposed, max_time))

        self._refresh_zoom_timeline(save=False)

    def _on_zoom_segment_moved(self, start_kf_id: str, end_kf_id: str, start_ms: float, end_ms: float) -> None:
        if not self._session:
            return
        self._push_undo_snapshot(once_per_drag=True)
        keyframes = sorted(self._session.keyframes, key=lambda k: k.timestamp)
        start_kf = next((kf for kf in keyframes if kf.id == start_kf_id), None)
        end_kf = next((kf for kf in keyframes if kf.id == end_kf_id), None) if end_kf_id else None
        if not start_kf:
            return
        start_idx = next((idx for idx, kf in enumerate(keyframes) if kf.id == start_kf.id), -1)
        end_idx = next((idx for idx, kf in enumerate(keyframes) if kf.id == end_kf.id), -1) if end_kf else -1
        if start_idx < 0:
            return

        duration = float(getattr(self._preview, "_video_duration_ms", self._session.duration))
        old_start = float(start_kf.timestamp)
        old_end = float(end_kf.timestamp) if end_kf else old_start
        timestamp_span = old_end - old_start if end_kf else 0.0
        visual_duration = float(end_kf.timestamp + end_kf.duration - old_start) if end_kf else duration - old_start
        if end_kf:
            visual_duration = max(visual_duration, float(end_ms) - float(start_ms) + max(0.0, float(end_kf.duration)))
        else:
            visual_duration = max(0.0, duration - float(start_ms))

        new_start = max(0.0, min(float(start_ms), max(0.0, duration - visual_duration)))
        delta = new_start - old_start
        move_until = end_idx if end_idx >= 0 else len(keyframes) - 1
        for idx in range(start_idx, move_until + 1):
            keyframes[idx].timestamp += delta

        self._refresh_zoom_timeline(save=False)

    def _on_zoom_segment_deleted(self, start_kf_id: str) -> None:
        if not self._session:
            return
        keyframes = sorted(self._session.keyframes, key=lambda k: k.timestamp)
        start_idx = next((i for i, kf in enumerate(keyframes) if kf.id == start_kf_id), -1)
        if start_idx < 0:
            return
        self._push_undo_snapshot()
        out_idx = -1
        for idx in range(start_idx + 1, len(keyframes)):
            if keyframes[idx].zoom <= 1.01:
                out_idx = idx
                break
        remove_until = out_idx if out_idx >= 0 else len(keyframes) - 1
        remove_ids = {kf.id for kf in keyframes[start_idx:remove_until + 1]}
        self._session.keyframes = [kf for kf in self._session.keyframes if kf.id not in remove_ids]
        self._refresh_zoom_timeline(save=True)

    def _on_timeline_drag_finished(self) -> None:
        if self._session:
            self._timeline_drag_undo_pushed = False
            self._update_undo_redo_controls()
            self._save_project()

    def _toggle_playback(self):
        if self._preview.is_playing:
            self._preview.pause()
        else:
            self._preview.play()

    def _on_background_changed(self, preset):
        self._preview.set_bg_preset(preset)
        if self._session:
            self._session.background_id = preset.name
            self._save_project()

    def _on_frame_changed(self, preset):
        self._preview.set_frame_preset(preset)
        if self._session:
            self._session.frame_id = preset.name
            self._save_project()

    def _on_click_effect_changed(self, preset):
        self._preview.set_click_preset(preset)
        if self._session:
            self._session.click_effect_id = preset.name
            self._save_project()

    def _on_output_dimensions_changed(self, dim):
        self._preview.set_output_dim(dim)
        if self._session:
            if isinstance(dim, tuple):
                self._session.output_dimensions = list(dim)
            else:
                self._session.output_dimensions = dim
            self._save_project()

    def _ensure_video_segments(self, duration_ms: float) -> None:
        if not self._session:
            return
        duration = max(float(duration_ms or 0.0), float(self._session.duration or 0.0))
        segments = []
        for seg in self._session.video_segments or []:
            start = max(0.0, min(float(seg.start_ms), duration))
            end = max(0.0, min(float(seg.end_ms), duration))
            if end - start >= 1.0:
                seg.start_ms = start
                seg.end_ms = end
                try:
                    seg.speed = max(0.1, min(10.0, float(seg.speed)))
                except (TypeError, ValueError):
                    seg.speed = 1.0
                segments.append(seg)
        if not segments and duration > 0:
            segments = [VideoSegment.create(0.0, duration, 1.0)]
        self._session.video_segments = segments

    def _find_video_segment_index(self, time_ms: float) -> int:
        if not self._session or not self._session.video_segments:
            return -1
        for idx, seg in enumerate(self._session.video_segments):
            if seg.start_ms <= time_ms <= seg.end_ms:
                return idx
        return -1

    def _sync_video_segments(self, selected_index: int | None = None, save: bool = True) -> None:
        if not self._session:
            return
        self._session.video_segments = list(self._session.video_segments or [])
        if selected_index is not None:
            self._selected_video_segment_index = selected_index
        if self._session.video_segments:
            self._selected_video_segment_index = max(
                0,
                min(self._selected_video_segment_index, len(self._session.video_segments) - 1),
            )
        else:
            self._selected_video_segment_index = -1
        self._zoom_engine.video_segments = self._session.video_segments
        self._preview.set_video_segments(self._session.video_segments)
        self._timeline.set_video_segments(self._session.video_segments, self._selected_video_segment_index)
        if self._selected_video_segment_index >= 0:
            seg = self._session.video_segments[self._selected_video_segment_index]
            self._editor.set_selected_segment_speed(seg.speed, self._selected_video_segment_index)
        else:
            self._editor.set_selected_segment_speed(None)
        if hasattr(self._editor, "set_range_actions_enabled"):
            self._editor.set_range_actions_enabled(
                self._selected_video_segment_index >= 0,
                self._video_segment_clipboard is not None,
            )
        self._refresh_editor_info()
        self._update_undo_redo_controls()
        if save:
            self._save_project()

    def _on_segment_selected(self, index: int) -> None:
        if not self._session or not self._session.video_segments:
            self._selected_video_segment_index = -1
            self._editor.set_selected_segment_speed(None)
            self._editor.set_range_actions_enabled(False, self._video_segment_clipboard is not None)
            return
        if index < 0 or index >= len(self._session.video_segments):
            return
        self._selected_video_segment_index = index
        seg = self._session.video_segments[index]
        self._editor.set_selected_segment_speed(seg.speed, index)
        self._editor.set_range_actions_enabled(True, self._video_segment_clipboard is not None)

    def _on_segment_speed_changed(self, speed: float) -> None:
        if not self._session or not self._session.video_segments:
            return
        idx = self._selected_video_segment_index
        if idx < 0 or idx >= len(self._session.video_segments):
            return
        self._push_undo_snapshot()
        self._session.video_segments[idx].speed = max(0.1, min(10.0, float(speed)))
        self._sync_video_segments(idx, save=True)

    def _on_split_requested(self, time_ms: float) -> None:
        if not self._session:
            return
        duration = self._session.duration
        if duration <= 0:
            duration = getattr(self._preview, "_video_duration_ms", 0.0)
        self._ensure_video_segments(duration)
        idx = self._find_video_segment_index(float(time_ms))
        if idx < 0:
            return
        seg = self._session.video_segments[idx]
        split_at = max(seg.start_ms, min(float(time_ms), seg.end_ms))
        min_len = 250.0
        if split_at - seg.start_ms < min_len or seg.end_ms - split_at < min_len:
            return
        self._push_undo_snapshot()
        original_end = seg.end_ms
        seg.end_ms = split_at
        right = VideoSegment.create(split_at, original_end, seg.speed)
        self._session.video_segments.insert(idx + 1, right)
        self._sync_video_segments(idx + 1, save=True)

    def _on_range_selection_requested(self, start_ms: float, end_ms: float) -> None:
        if not self._session:
            return
        duration = self._session.duration
        if duration <= 0:
            duration = getattr(self._preview, "_video_duration_ms", 0.0)
        if duration <= 0:
            return

        self._ensure_video_segments(duration)
        start = max(0.0, min(float(start_ms), float(duration)))
        end = max(0.0, min(float(end_ms), float(duration)))
        if end < start:
            start, end = end, start
        if end - start < 250.0:
            return
        self._push_undo_snapshot()

        old_segments = sorted(self._session.video_segments or [], key=lambda s: s.start_ms)
        eps = 0.5

        def _speed_at(time_ms: float) -> float:
            for seg in old_segments:
                if seg.start_ms - eps <= time_ms <= seg.end_ms + eps:
                    try:
                        return max(0.1, min(10.0, float(seg.speed)))
                    except (TypeError, ValueError):
                        return 1.0
            return 1.0

        raw_boundaries = [0.0, float(duration), start, end]
        for seg in old_segments:
            for boundary in (float(seg.start_ms), float(seg.end_ms)):
                boundary = max(0.0, min(boundary, float(duration)))
                if boundary <= start + eps or boundary >= end - eps:
                    raw_boundaries.append(boundary)

        boundaries: list[float] = []
        for boundary in sorted(raw_boundaries):
            if boundaries and abs(boundary - boundaries[-1]) <= eps:
                boundaries[-1] = boundary if abs(boundary - start) <= eps or abs(boundary - end) <= eps else boundaries[-1]
                continue
            boundaries.append(boundary)

        new_segments: list[VideoSegment] = []
        selected_index = -1
        for idx in range(len(boundaries) - 1):
            seg_start = boundaries[idx]
            seg_end = boundaries[idx + 1]
            if seg_end - seg_start < 1.0:
                continue
            speed = _speed_at((seg_start + seg_end) / 2.0)
            new_seg = VideoSegment.create(seg_start, seg_end, speed)
            if abs(seg_start - start) <= eps and abs(seg_end - end) <= eps:
                selected_index = len(new_segments)
            new_segments.append(new_seg)

        if not new_segments:
            return
        self._session.video_segments = new_segments
        if selected_index < 0:
            midpoint = (start + end) / 2.0
            selected_index = self._find_video_segment_index(midpoint)
        self._sync_video_segments(selected_index, save=True)

    def _on_video_segment_deleted(self, segment_id: str) -> None:
        if not self._session or not self._session.video_segments:
            return
        segments = self._session.video_segments
        idx = next((i for i, seg in enumerate(segments) if seg.id == segment_id), -1)
        if idx < 0 or len(segments) <= 1:
            return
        self._push_undo_snapshot()
        del segments[idx]
        next_selected = min(idx, len(segments) - 1)
        self._sync_video_segments(next_selected, save=True)

    def _selected_video_segment(self) -> VideoSegment | None:
        if not self._session or not self._session.video_segments:
            return None
        idx = self._selected_video_segment_index
        if idx < 0 or idx >= len(self._session.video_segments):
            return None
        return self._session.video_segments[idx]

    def _clone_video_segment(self, segment: VideoSegment) -> VideoSegment:
        return VideoSegment.create(
            float(segment.start_ms),
            float(segment.end_ms),
            max(0.1, min(10.0, float(segment.speed))),
        )

    def _on_segment_copy_requested(self) -> None:
        segment = self._selected_video_segment()
        if not segment:
            return
        self._video_segment_clipboard = self._clone_video_segment(segment)
        self._editor.set_range_actions_enabled(True, True)

    def _on_segment_cut_requested(self) -> None:
        segment = self._selected_video_segment()
        if not segment:
            return
        self._video_segment_clipboard = self._clone_video_segment(segment)
        self._editor.set_range_actions_enabled(True, True)
        self._on_segment_delete_requested()

    def _on_segment_paste_requested(self) -> None:
        if not self._session or not self._video_segment_clipboard:
            return
        self._ensure_video_segments(getattr(self._preview, "_video_duration_ms", self._session.duration))
        insert_at = self._selected_video_segment_index + 1
        if insert_at <= 0:
            insert_at = len(self._session.video_segments or [])
        self._push_undo_snapshot()
        cloned = self._clone_video_segment(self._video_segment_clipboard)
        self._session.video_segments.insert(insert_at, cloned)
        self._sync_video_segments(insert_at, save=True)

    def _on_segment_delete_requested(self) -> None:
        segment = self._selected_video_segment()
        if not segment:
            return
        self._on_video_segment_deleted(segment.id)

    def _on_highlight_add_requested(self, shape: str) -> None:
        self._pending_highlight_shape = shape if shape in ("rect", "circle") else "rect"
        self._preview.enter_highlight_pick_mode(self._pending_highlight_shape)

    def _on_highlight_picked(self, pan_x: float, pan_y: float, shape: str) -> None:
        if not self._session or pan_x < 0.0 or pan_y < 0.0:
            return
        duration = float(getattr(self._preview, "_video_duration_ms", self._session.duration) or self._session.duration)
        start_ms = max(0.0, min(float(getattr(self._preview, "_current_time_ms", 0.0)), max(duration - 250.0, 0.0)))
        end_ms = min(duration if duration > 0 else start_ms + 2500.0, start_ms + 2500.0)
        width = 0.26 if shape == "circle" else 0.30
        height = 0.26 if shape == "circle" else 0.18
        x = max(0.0, min(1.0 - width, float(pan_x) - width / 2.0))
        y = max(0.0, min(1.0 - height, float(pan_y) - height / 2.0))

        self._push_undo_snapshot()
        highlights = list(self._session.highlights or [])
        highlights.append(
            HighlightBox.create(
                start_ms=start_ms,
                end_ms=end_ms,
                x=x,
                y=y,
                width=width,
                height=height,
                shape=shape if shape in ("rect", "circle") else "rect",
            )
        )
        self._session.highlights = highlights
        self._zoom_engine.highlights = highlights
        self._preview.set_highlights(highlights)
        self._update_undo_redo_controls()
        self._save_project()

    def _save_project(self):
        if not self._project_path or not self._session:
            return
        try:
            new_data = json.loads(self._session.to_json())
            self._project_data.update(new_data)
            with open(self._project_path, 'w', encoding='utf-8') as f:
                json.dump(self._project_data, f, indent=2)
        except Exception as e:
            logger.error(f"Failed to save project: {e}")

    def _on_export(self):
        logger.info("Export clicked. Triggering export_app.py headless exporter...")
        import subprocess
        import sys
        import os
        from pathlib import Path

        if getattr(sys, "frozen", False):
            base_dir = os.path.dirname(sys.executable)
            cmd = [os.path.join(base_dir, "export_app.exe"), "--project", self._project_path]
            cwd = base_dir
        else:
            script_dir = Path(__file__).resolve().parent.parent.parent.parent
            cmd = [sys.executable, "export_app.py", "--project", self._project_path]
            cwd = str(script_dir)

        subprocess.Popen(
            cmd,
            cwd=cwd
        )
        self.close()

