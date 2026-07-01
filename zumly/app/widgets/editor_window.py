import json
import logging
import os
import re
import sys
from typing import Optional

from PySide6.QtCore import Qt, Signal, QProcess
from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QFrame, QPushButton, QLabel, QSpacerItem, QSizePolicy,
    QDialog, QProgressBar, QPlainTextEdit, QFileDialog,
)

from .editor_panel import EditorPanel
from .preview_widget import PreviewWidget
from .timeline_widget import TimelineWidget
from ..models import HighlightBox, RecordingSession, TimelineFrame, VideoSegment
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

    def set_exporting(self, exporting: bool) -> None:
        self.btn_export.setEnabled(not exporting)
        self.btn_export.setText("Exporting..." if exporting else "Export")
        self.btn_discard.setEnabled(not exporting)


class ExportProgressDialog(QDialog):
    cancel_requested = Signal()

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.setWindowTitle("Exporting video")
        self.setModal(False)
        self.setMinimumWidth(460)
        self.setStyleSheet(
            f"QDialog {{ background: {T.BG_LAYER_2}; color: {T.FG_PRIMARY}; }}"
            f"QLabel {{ color: {T.FG_PRIMARY}; font-size: {T.FONT_SIZE_BODY}px; }}"
            f"QProgressBar {{ background: {T.BG_LAYER_1}; color: {T.FG_PRIMARY};"
            f"  border: 1px solid {T.STROKE_2}; border-radius: {T.RADIUS_SMALL}px;"
            f"  text-align: center; height: 18px; }}"
            f"QProgressBar::chunk {{ background: {T.BRAND}; border-radius: {T.RADIUS_SMALL}px; }}"
            f"QPlainTextEdit {{ background: {T.BG_LAYER_1}; color: {T.FG_2};"
            f"  border: 1px solid {T.STROKE_2}; border-radius: {T.RADIUS_SMALL}px;"
            f"  font-family: Consolas; font-size: {T.FONT_SIZE_CAPTION}px; }}"
        )
        layout = QVBoxLayout(self)
        layout.setContentsMargins(T.SPACE_LG, T.SPACE_LG, T.SPACE_LG, T.SPACE_LG)
        layout.setSpacing(T.SPACE_MD)

        self._status = QLabel("Preparing export...")
        layout.addWidget(self._status)

        self._progress = QProgressBar()
        self._progress.setRange(0, 100)
        self._progress.setValue(0)
        layout.addWidget(self._progress)

        self._log = QPlainTextEdit()
        self._log.setReadOnly(True)
        self._log.setFixedHeight(112)
        layout.addWidget(self._log)

        row = QHBoxLayout()
        row.addStretch()
        self._cancel = QPushButton("Cancel")
        self._cancel.setFixedHeight(32)
        self._cancel.clicked.connect(self.cancel_requested.emit)
        row.addWidget(self._cancel)
        self._close = QPushButton("Close")
        self._close.setFixedHeight(32)
        self._close.setVisible(False)
        self._close.clicked.connect(self.accept)
        row.addWidget(self._close)
        layout.addLayout(row)

    def append_output(self, text: str) -> None:
        clean = text.replace("\r", "\n")
        for line in clean.splitlines():
            line = line.strip()
            if not line:
                continue
            self._log.appendPlainText(line)
            match = re.search(r"Export progress:\s*([0-9.]+)%", line)
            if match:
                self.set_progress(float(match.group(1)))

    def set_progress(self, value: float) -> None:
        pct = max(0, min(100, int(round(value))))
        self._progress.setValue(pct)
        self._status.setText(f"Exporting video... {pct}%")

    def finish(self, success: bool, message: str) -> None:
        self._progress.setValue(100 if success else self._progress.value())
        self._status.setText(message)
        self._cancel.setVisible(False)
        self._close.setVisible(True)


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
        self._editor.ai_zoom_requested.connect(self._on_ai_zoom_requested)
        self._editor.generate_narration_requested.connect(self._on_generate_narration_requested)
        self._editor.generate_chapters_requested.connect(self._on_generate_chapters_requested)
        self._editor.segment_speed_changed.connect(self._on_segment_speed_changed)
        self._editor.segment_cut_requested.connect(self._on_segment_cut_requested)
        self._editor.segment_copy_requested.connect(self._on_segment_copy_requested)
        self._editor.segment_paste_requested.connect(self._on_segment_paste_requested)
        self._editor.segment_delete_requested.connect(self._on_segment_delete_requested)
        self._editor.text_frame_add_requested.connect(self._on_text_frame_add_requested)
        self._editor.image_frame_add_requested.connect(self._on_image_frame_add_requested)
        self._editor.timeline_frame_changed.connect(self._on_timeline_frame_changed)
        self._editor.timeline_frame_delete_requested.connect(self._on_timeline_frame_delete_requested)
        self._editor.timeline_frame_select_requested.connect(self._on_timeline_frame_selected)
        self._editor.highlight_add_requested.connect(self._on_highlight_add_requested)
        self._editor.highlight_timing_changed.connect(self._on_highlight_timing_changed)
        self._editor.highlight_delete_requested.connect(self._on_highlight_delete_requested)
        self._editor.undo_requested.connect(self._on_undo_requested)
        self._editor.redo_requested.connect(self._on_redo_requested)
        self._preview.play_pause_requested.connect(self._toggle_playback)
        self._preview.highlight_picked.connect(self._on_highlight_picked)
        self._preview.highlight_selected.connect(self._on_highlight_selected)
        self._preview.annotation_dragged.connect(self._on_annotation_dragged)
        self._preview.highlight_resized.connect(self._on_highlight_resized)
        self._timeline.segment_selected.connect(self._on_segment_selected)
        self._timeline.timeline_frame_selected.connect(self._on_timeline_frame_selected)
        self._timeline.text_frame_requested_at.connect(self._on_text_frame_add_requested)
        self._timeline.image_frame_requested_at.connect(self._on_image_frame_add_requested)
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
        self._selected_timeline_frame_id = ""
        self._pending_highlight_shape = "rect"
        self._selected_highlight_id = ""
        self._timeline_drag_undo_pushed = False
        self._export_process: QProcess | None = None
        self._export_dialog: ExportProgressDialog | None = None
        self._ai_worker = None
        self._ai_task = ""
        
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
            if hasattr(self._session, 'timeline_frames'):
                self._zoom_engine.timeline_frames = self._session.timeline_frames or []
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
            self._preview.set_timeline_frames(self._session.timeline_frames, self._selected_timeline_frame_id)
            if hasattr(self._editor, "set_timeline_frames"):
                self._editor.set_timeline_frames(self._session.timeline_frames or [], self._selected_timeline_frame_id)
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
                video_segments=self._session.video_segments if hasattr(self._session, 'video_segments') else None,
                timeline_frames=self._session.timeline_frames if hasattr(self._session, 'timeline_frames') else None,
            )
            self._timeline.chapters = self._session.chapters or []
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
        if hasattr(self._editor, "set_frame_insert_timestamp"):
            duration = getattr(self._preview, "_video_duration_ms", self._session.duration if self._session else 0.0)
            self._editor.set_frame_insert_timestamp(time_ms, duration)

    def _sync_zoom_engine_from_session(self) -> None:
        if not self._session:
            return
        self._zoom_engine.keyframes = self._session.keyframes or []
        self._zoom_engine.click_events = self._session.click_events or []
        self._zoom_engine.video_segments = self._session.video_segments or []
        self._zoom_engine.timeline_frames = self._session.timeline_frames or []
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
        self._session.timeline_frames = self._zoom_engine.timeline_frames
        self._session.voiceover_segments = self._zoom_engine.voiceover_segments
        self._session.highlights = self._zoom_engine.highlights
        self._preview.set_cursor_data(
            self._session.mouse_track,
            self._project_data.get("monitorRect", {}),
            self._session.click_events,
        )
        self._refresh_zoom_timeline(save=False)
        self._sync_highlights(save=False)
        self._sync_video_segments(self._selected_video_segment_index, save=False)
        self._sync_timeline_frames(getattr(self, "_selected_timeline_frame_id", ""), save=False)
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
        self._zoom_engine.timeline_frames = self._session.timeline_frames or []
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
            timeline_frames=self._session.timeline_frames if hasattr(self._session, "timeline_frames") else None,
        )
        if hasattr(self._timeline, "set_timeline_frames"):
            self._timeline.set_timeline_frames(self._session.timeline_frames or [], getattr(self, "_selected_timeline_frame_id", ""))
        if hasattr(self._editor, "set_timeline_frames"):
            self._editor.set_timeline_frames(self._session.timeline_frames or [], getattr(self, "_selected_timeline_frame_id", ""))
        self._timeline.chapters = self._session.chapters or []
        self._on_playback_time_changed(getattr(self._preview, "_current_time_ms", 0.0))
        self._refresh_editor_info()
        if save:
            self._save_project()

    def _load_ai_settings(self):
        from PySide6.QtCore import QSettings
        from ..ai_service import AISettings
        from ..credentials import unprotect

        settings = QSettings("zumly", "zumly")
        return AISettings(
            endpoint=settings.value("ai/endpoint", "") or "",
            api_key=unprotect(settings.value("ai/apiKey", "") or ""),
            chat_model=settings.value("ai/chatModel", "") or "",
            narration_model=settings.value("ai/narrationModel", "gpt-5.4") or "gpt-5.4",
            tts_voice=settings.value("ai/ttsVoice", "en-US-Ava:DragonHDLatestNeural") or "en-US-Ava:DragonHDLatestNeural",
        )

    def _base_ai_kwargs(self) -> dict:
        if not self._session:
            return {}
        return {
            "video_path": self._project_data.get("videoPath", ""),
            "mouse_track": self._session.mouse_track or [],
            "monitor_rect": self._project_data.get("monitorRect", {}),
            "duration_ms": float(getattr(self._preview, "_video_duration_ms", self._session.duration) or self._session.duration),
            "key_events": self._session.key_events,
            "click_events": self._session.click_events,
            "zoom_keyframes": self._session.keyframes or [],
            "frame_timestamps": self._session.frame_timestamps,
        }

    def _start_ai_worker(self, task: str):
        if not self._session:
            return None
        if self._ai_worker and self._ai_worker.isRunning():
            self._set_ai_task_status(task, "Another AI task is already running.")
            return None

        from ..ai_service import AIWorker

        worker = AIWorker(self)
        worker.zoom_result.connect(self._on_ai_zoom_result)
        worker.chapters_result.connect(self._on_ai_chapters_result)
        worker.narration_result.connect(self._on_ai_narration_result)
        worker.tts_result.connect(self._on_ai_tts_result)
        worker.error.connect(self._on_ai_error)
        worker.status.connect(self._on_ai_status)
        worker.finished.connect(self._on_ai_finished)
        self._ai_worker = worker
        self._ai_task = task
        self._editor.set_ai_busy(True)
        return worker

    def _set_ai_task_status(self, task: str, text: str) -> None:
        if task == "zoom":
            self._editor.set_ai_zoom_status(text)
        elif task == "chapters":
            self._editor.set_chapters_status(text)
        elif task == "narration":
            self._editor.set_narration_status(text)
        elif task == "tts":
            self._editor.set_voiceover_status(text)

    def _on_ai_status(self, text: str) -> None:
        self._set_ai_task_status(self._ai_task, text)
        self._editor.set_ai_busy(True)

    def _on_ai_error(self, task: str, message: str) -> None:
        logger.error("AI %s failed: %s", task, message)
        self._set_ai_task_status(task, f"AI {task} failed: {message}")

    def _on_ai_finished(self) -> None:
        if self._ai_worker:
            self._ai_worker.deleteLater()
        self._ai_worker = None
        self._ai_task = ""
        self._editor.set_ai_busy(False)

    def _on_ai_zoom_requested(self, max_clusters: int, zoom_level: float, min_gap_ms: int) -> None:
        settings = self._load_ai_settings()
        if not settings.chat_configured:
            self._editor.set_ai_zoom_status("Open AI Settings and add endpoint, key, and chat model.")
            return
        worker = self._start_ai_worker("zoom")
        if not worker:
            return
        worker.run_zoom_analysis(
            settings,
            mouse_track=self._session.mouse_track or [],
            monitor_rect=self._project_data.get("monitorRect", {}),
            duration_ms=float(getattr(self._preview, "_video_duration_ms", self._session.duration) or self._session.duration),
            key_events=self._session.key_events,
            click_events=self._session.click_events,
            max_clusters=max_clusters,
            zoom_level=zoom_level,
            min_gap_ms=min_gap_ms,
        )

    def _on_generate_chapters_requested(self) -> None:
        settings = self._load_ai_settings()
        if not settings.narration_configured:
            self._editor.set_chapters_status("Open AI Settings and add endpoint, key, and narration model.")
            return
        worker = self._start_ai_worker("chapters")
        if not worker:
            return
        worker.run_chapters(settings, **self._base_ai_kwargs())

    def _on_generate_narration_requested(self, voice: str, guidance: str) -> None:
        settings = self._load_ai_settings()
        if not settings.narration_configured:
            self._editor.set_narration_status("Open AI Settings and add endpoint, key, and narration model.")
            return
        worker = self._start_ai_worker("narration")
        if not worker:
            return
        kwargs = self._base_ai_kwargs()
        kwargs.update(
            {
                "voice": voice or settings.tts_voice,
                "synthesize_audio": True,
                "guidance_prompt": guidance,
            }
        )
        worker.run_narration(settings, **kwargs)

    def _on_ai_zoom_result(self, keyframes: list) -> None:
        if not self._session:
            return
        self._push_undo_snapshot()
        self._session.keyframes = list(keyframes or [])
        self._refresh_zoom_timeline(save=True)
        self._editor.set_ai_zoom_status(f"Generated {len(self._session.keyframes)} AI zoom keyframes.")

    def _on_ai_chapters_result(self, chapters: list) -> None:
        if not self._session:
            return
        manual = [chapter for chapter in (self._session.chapters or []) if not chapter.auto_detected]
        generated = list(chapters or [])
        merged = sorted(manual + generated, key=lambda chapter: chapter.timestamp_ms)
        self._push_undo_snapshot()
        self._session.chapters = merged
        self._timeline.chapters = merged
        self._save_project()
        self._editor.set_chapters_status(f"Generated {len(generated)} AI chapters.")

    def _on_ai_narration_result(self, result) -> None:
        if not self._session:
            return
        from ..ai_service import replace_generated_narration_segments

        generated = list(getattr(result, "voiceover_segments", []) or [])
        self._push_undo_snapshot()
        self._session.voiceover_segments = replace_generated_narration_segments(
            self._session.voiceover_segments,
            generated,
        )
        self._zoom_engine.voiceover_segments = self._session.voiceover_segments or []
        self._refresh_zoom_timeline(save=False)
        self._save_project()
        script_path = getattr(result, "script_path", "")
        suffix = f" Script: {script_path}" if script_path else ""
        self._editor.set_narration_status(f"Generated {len(generated)} narration segments.{suffix}")

    def _on_ai_tts_result(self, segment_id: str, audio_file_path: str) -> None:
        if not self._session or not self._session.voiceover_segments:
            return
        for segment in self._session.voiceover_segments:
            if segment.id == segment_id:
                segment.audio_path = audio_file_path
                break
        self._refresh_zoom_timeline(save=False)
        self._save_project()
        self._editor.set_voiceover_status("Voiceover audio generated.")

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

    def _edited_duration_ms(self) -> float:
        if not self._session or not self._session.video_segments:
            return 0.0
        total = 0.0
        for seg in self._session.video_segments:
            try:
                speed = max(0.1, min(10.0, float(seg.speed)))
            except (TypeError, ValueError):
                speed = 1.0
            total += max(0.0, float(seg.end_ms) - float(seg.start_ms)) / speed
        return total

    def _sync_video_segments(self, selected_index: int | None = None, save: bool = True) -> None:
        if not self._session:
            return
        self._session.video_segments = list(self._session.video_segments or [])
        self._zoom_engine.timeline_frames = self._session.timeline_frames or []
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
        if hasattr(self._preview, "seek_to"):
            self._preview.seek_to(getattr(self._preview, "playback_pos_ms", 0.0))
        if self._selected_video_segment_index >= 0:
            seg = self._session.video_segments[self._selected_video_segment_index]
            self._editor.set_selected_segment_speed(seg.speed, self._selected_video_segment_index)
        else:
            self._editor.set_selected_segment_speed(None)
        if hasattr(self._editor, "set_edited_preview_summary"):
            self._editor.set_edited_preview_summary(
                self._edited_duration_ms(),
                getattr(self._preview, "_video_duration_ms", self._session.duration),
            )
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

        old_segments = list(self._session.video_segments or [])
        eps = 0.5

        parts: list[tuple[float, float, float, bool]] = []
        for old_seg in old_segments:
            seg_start = max(0.0, min(float(old_seg.start_ms), float(duration)))
            seg_end = max(0.0, min(float(old_seg.end_ms), float(duration)))
            if seg_end - seg_start < 1.0:
                continue

            boundaries = [seg_start, seg_end]
            if seg_start + eps < start < seg_end - eps:
                boundaries.append(start)
            if seg_start + eps < end < seg_end - eps:
                boundaries.append(end)
            boundaries = sorted(boundaries)

            for idx in range(len(boundaries) - 1):
                part_start = boundaries[idx]
                part_end = boundaries[idx + 1]
                if part_end - part_start < 1.0:
                    continue
                try:
                    speed = max(0.1, min(10.0, float(old_seg.speed)))
                except (TypeError, ValueError):
                    speed = 1.0
                midpoint = (part_start + part_end) / 2.0
                parts.append((part_start, part_end, speed, start - eps <= midpoint <= end + eps))

        def _speed_at_source_time(time_ms: float) -> float:
            for seg in old_segments:
                if float(seg.start_ms) - eps <= time_ms <= float(seg.end_ms) + eps:
                    try:
                        return max(0.1, min(10.0, float(seg.speed)))
                    except (TypeError, ValueError):
                        return 1.0
            return 1.0

        new_segments: list[VideoSegment] = []
        selected_index = -1
        idx = 0
        while idx < len(parts):
            part_start, part_end, part_speed, is_selected = parts[idx]
            if not is_selected:
                new_segments.append(VideoSegment.create(part_start, part_end, part_speed))
                idx += 1
                continue

            run_start = part_start
            run_end = part_end
            idx += 1
            while idx < len(parts) and parts[idx][3] and abs(parts[idx][0] - run_end) <= eps:
                run_end = parts[idx][1]
                idx += 1
            run_speed = _speed_at_source_time((run_start + run_end) / 2.0)
            if selected_index < 0:
                selected_index = len(new_segments)
            new_segments.append(VideoSegment.create(run_start, run_end, run_speed))

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

    def _timeline_frame_insert_time(self) -> float:
        if self._session and self._session.video_segments and 0 <= self._selected_video_segment_index < len(self._session.video_segments):
            return float(self._session.video_segments[self._selected_video_segment_index].end_ms)
        return float(getattr(self._preview, "_current_time_ms", 0.0))

    def _split_video_segment_for_insert(self, timestamp_ms: float) -> None:
        if not self._session:
            return
        duration = float(getattr(self._preview, "_video_duration_ms", self._session.duration) or self._session.duration)
        self._ensure_video_segments(duration)
        timestamp = max(0.0, min(float(timestamp_ms), duration))
        for idx, segment in enumerate(list(self._session.video_segments or [])):
            if segment.start_ms + 250.0 < timestamp < segment.end_ms - 250.0:
                right = VideoSegment.create(timestamp, segment.end_ms, segment.speed)
                segment.end_ms = timestamp
                self._session.video_segments.insert(idx + 1, right)
                self._selected_video_segment_index = idx
                self._sync_video_segments(idx, save=False)
                return

    def _sync_timeline_frames(self, selected_id: str | None = None, save: bool = True) -> None:
        if not self._session:
            return
        frames = sorted(self._session.timeline_frames or [], key=lambda frame: (float(frame.timestamp_ms), frame.id))
        self._session.timeline_frames = frames
        if selected_id is not None:
            self._selected_timeline_frame_id = selected_id
        if not hasattr(self, "_selected_timeline_frame_id"):
            self._selected_timeline_frame_id = ""
        if self._selected_timeline_frame_id and not any(frame.id == self._selected_timeline_frame_id for frame in frames):
            self._selected_timeline_frame_id = ""
        if hasattr(self._timeline, "set_timeline_frames"):
            self._timeline.set_timeline_frames(frames, self._selected_timeline_frame_id)
        if hasattr(self._preview, "set_timeline_frames"):
            self._preview.set_timeline_frames(frames, self._selected_timeline_frame_id)
        if hasattr(self._editor, "set_timeline_frames"):
            self._editor.set_timeline_frames(frames, self._selected_timeline_frame_id)
        selected = self._selected_timeline_frame()
        if selected and hasattr(self._editor, "set_selected_timeline_frame"):
            self._editor.set_selected_timeline_frame(
                selected.id,
                selected.kind,
                selected.timestamp_ms,
                selected.duration_ms,
                selected.text,
                selected.image_path,
                selected.background_color,
                selected.text_color,
                selected.font_size,
            )
        elif hasattr(self._editor, "set_selected_timeline_frame"):
            self._editor.set_selected_timeline_frame("")
        if save:
            self._save_project()

    def _selected_timeline_frame(self) -> TimelineFrame | None:
        if not self._session or not self._session.timeline_frames or not self._selected_timeline_frame_id:
            return None
        return next((frame for frame in self._session.timeline_frames if frame.id == self._selected_timeline_frame_id), None)

    def _add_timeline_frame(self, frame: TimelineFrame) -> None:
        if not self._session:
            return
        self._push_undo_snapshot()
        self._split_video_segment_for_insert(frame.timestamp_ms)
        frames = list(self._session.timeline_frames or [])
        frames.append(frame)
        self._session.timeline_frames = frames
        self._sync_timeline_frames(frame.id, save=True)

    def _on_text_frame_add_requested(self, timestamp_ms: float | None = None) -> None:
        insert_time = self._timeline_frame_insert_time() if timestamp_ms is None else float(timestamp_ms)
        self._add_timeline_frame(
            TimelineFrame.create(
                insert_time,
                kind="text",
                duration_ms=2500.0,
                text="Add your title or note here",
            )
        )
        self._preview.seek_to(insert_time)

    def _on_image_frame_add_requested(self, timestamp_ms: float | None = None) -> None:
        if not self._session:
            return
        path, _ = QFileDialog.getOpenFileName(
            self,
            "Choose picture frame",
            "",
            "Images (*.png *.jpg *.jpeg *.webp *.bmp);;All files (*.*)",
        )
        if not path:
            return
        insert_time = self._timeline_frame_insert_time() if timestamp_ms is None else float(timestamp_ms)
        self._add_timeline_frame(
            TimelineFrame.create(
                insert_time,
                kind="image",
                duration_ms=2500.0,
                image_path=path,
            )
        )
        self._preview.seek_to(insert_time)

    def _on_timeline_frame_selected(self, frame_id: str) -> None:
        self._selected_timeline_frame_id = frame_id
        self._sync_timeline_frames(frame_id, save=False)
        frame = self._selected_timeline_frame()
        if frame:
            self._preview.seek_to(frame.timestamp_ms)

    def _on_timeline_frame_changed(
        self,
        frame_id: str,
        timestamp_ms: float,
        duration_ms: float,
        text: str,
        background_color: str,
        text_color: str,
        font_size: int,
    ) -> None:
        if not self._session or not self._session.timeline_frames:
            return
        frame = next((item for item in self._session.timeline_frames if item.id == frame_id), None)
        if not frame:
            return
        duration = float(getattr(self._preview, "_video_duration_ms", self._session.duration) or self._session.duration)
        old_timestamp = float(frame.timestamp_ms)
        frame.timestamp_ms = max(0.0, min(float(timestamp_ms), duration))
        frame.duration_ms = max(250.0, min(float(duration_ms), 600000.0))
        frame.background_color = background_color or "#111827"
        frame.text_color = text_color or "#f9fafb"
        frame.font_size = max(12, min(int(font_size or 54), 180))
        if frame.kind == "text":
            frame.text = text
        if abs(frame.timestamp_ms - old_timestamp) > 0.5:
            self._split_video_segment_for_insert(frame.timestamp_ms)
        self._sync_timeline_frames(frame_id, save=True)
        self._preview.seek_to(frame.timestamp_ms)

    def _on_timeline_frame_delete_requested(self, frame_id: str) -> None:
        if not self._session or not self._session.timeline_frames:
            return
        self._push_undo_snapshot()
        self._session.timeline_frames = [
            frame for frame in self._session.timeline_frames
            if frame.id != frame_id
        ]
        self._selected_timeline_frame_id = ""
        self._sync_timeline_frames("", save=True)

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
        highlight = HighlightBox.create(
            start_ms=start_ms,
            end_ms=end_ms,
            x=x,
            y=y,
            width=width,
            height=height,
            shape=shape if shape in ("rect", "circle") else "rect",
        )
        highlights.append(highlight)
        self._session.highlights = highlights
        self._zoom_engine.highlights = highlights
        self._selected_highlight_id = highlight.id
        self._sync_highlights(save=False)
        self._update_undo_redo_controls()
        self._save_project()

    def _selected_highlight(self) -> HighlightBox | None:
        if not self._session or not self._session.highlights:
            return None
        selected_id = getattr(self, "_selected_highlight_id", "")
        return next((hl for hl in self._session.highlights if hl.id == selected_id), None)

    def _sync_highlights(self, save: bool = True) -> None:
        if not self._session:
            return
        highlights = list(self._session.highlights or [])
        self._session.highlights = highlights
        self._zoom_engine.highlights = highlights
        self._preview.set_highlights(highlights)
        selected_id = getattr(self, "_selected_highlight_id", "")
        if selected_id:
            self._preview.select_highlight(selected_id)
        selected = self._selected_highlight()
        if selected and hasattr(self._editor, "set_selected_highlight_timing"):
            self._editor.set_selected_highlight_timing(
                selected.start_ms,
                selected.end_ms,
                getattr(self._preview, "_video_duration_ms", self._session.duration),
            )
        elif hasattr(self._editor, "set_selected_highlight_timing"):
            self._editor.set_selected_highlight_timing(None)
        if save:
            self._save_project()

    def _on_highlight_selected(self, highlight_id: str) -> None:
        self._selected_highlight_id = highlight_id
        self._sync_highlights(save=False)

    def _on_annotation_dragged(self, annotation_type: str, annotation_id: str, new_x: float, new_y: float) -> None:
        if annotation_type != "highlight" or not self._session or not self._session.highlights:
            return
        highlight = next((hl for hl in self._session.highlights if hl.id == annotation_id), None)
        if not highlight:
            return
        self._selected_highlight_id = annotation_id
        highlight.x = max(0.0, min(1.0 - float(highlight.width), float(new_x)))
        highlight.y = max(0.0, min(1.0 - float(highlight.height), float(new_y)))
        self._sync_highlights(save=True)

    def _on_highlight_resized(self, highlight_id: str, x: float, y: float, width: float, height: float) -> None:
        if not self._session or not self._session.highlights:
            return
        highlight = next((hl for hl in self._session.highlights if hl.id == highlight_id), None)
        if not highlight:
            return
        self._selected_highlight_id = highlight_id
        highlight.x = max(0.0, min(1.0, float(x)))
        highlight.y = max(0.0, min(1.0, float(y)))
        highlight.width = max(0.03, min(1.0 - highlight.x, float(width)))
        highlight.height = max(0.03, min(1.0 - highlight.y, float(height)))
        self._sync_highlights(save=True)

    def _on_highlight_timing_changed(self, start_ms: float, end_ms: float) -> None:
        highlight = self._selected_highlight()
        if not highlight:
            return
        duration = float(getattr(self._preview, "_video_duration_ms", self._session.duration) or self._session.duration)
        start = max(0.0, min(float(start_ms), max(duration - 1.0, 0.0)))
        end = max(start + 250.0, min(float(end_ms), duration if duration > 0 else start + 250.0))
        highlight.start_ms = start
        highlight.end_ms = end
        self._sync_highlights(save=True)

    def _on_highlight_delete_requested(self) -> None:
        if not self._session or not self._selected_highlight_id:
            return
        self._push_undo_snapshot()
        self._session.highlights = [
            hl for hl in (self._session.highlights or [])
            if hl.id != self._selected_highlight_id
        ]
        self._selected_highlight_id = ""
        self._sync_highlights(save=True)

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
        from pathlib import Path

        if self._export_process and self._export_process.state() != QProcess.ProcessState.NotRunning:
            if self._export_dialog:
                self._export_dialog.raise_()
                self._export_dialog.activateWindow()
            return

        self._save_project()

        if getattr(sys, "frozen", False):
            base_dir = os.path.dirname(sys.executable)
            program = os.path.join(base_dir, "export_app.exe")
            args = ["--project", self._project_path]
            cwd = base_dir
        else:
            script_dir = Path(__file__).resolve().parent.parent.parent.parent
            program = sys.executable
            args = ["export_app.py", "--project", self._project_path]
            cwd = str(script_dir)

        self._export_dialog = ExportProgressDialog(self)
        self._export_dialog.cancel_requested.connect(self._cancel_export)
        self._export_dialog.show()
        self._title_bar.set_exporting(True)

        process = QProcess(self)
        process.setWorkingDirectory(cwd)
        process.setProgram(program)
        process.setArguments(args)
        process.readyReadStandardOutput.connect(self._on_export_stdout)
        process.readyReadStandardError.connect(self._on_export_stderr)
        process.finished.connect(self._on_export_finished)
        process.errorOccurred.connect(self._on_export_error)
        self._export_process = process
        process.start()
        if not process.waitForStarted(1500):
            self._on_export_error(process.error())

    def _cancel_export(self) -> None:
        if self._export_process and self._export_process.state() != QProcess.ProcessState.NotRunning:
            self._export_process.kill()
        if self._export_dialog:
            self._export_dialog.finish(False, "Export cancelled.")
        self._title_bar.set_exporting(False)

    def _on_export_stdout(self) -> None:
        if not self._export_process or not self._export_dialog:
            return
        text = bytes(self._export_process.readAllStandardOutput()).decode("utf-8", errors="replace")
        self._export_dialog.append_output(text)

    def _on_export_stderr(self) -> None:
        if not self._export_process or not self._export_dialog:
            return
        text = bytes(self._export_process.readAllStandardError()).decode("utf-8", errors="replace")
        self._export_dialog.append_output(text)

    def _on_export_error(self, error) -> None:
        logger.error("Export process error: %s", error)
        if self._export_dialog:
            self._export_dialog.finish(False, f"Export failed to start: {error}")
        self._title_bar.set_exporting(False)

    def _on_export_finished(self, exit_code: int, exit_status) -> None:
        if self._export_process:
            self._on_export_stdout()
            self._on_export_stderr()
        success = exit_code == 0 and exit_status == QProcess.ExitStatus.NormalExit
        if self._export_dialog:
            if success:
                self._export_dialog.finish(True, "Export complete.")
            else:
                self._export_dialog.finish(False, f"Export failed with exit code {exit_code}.")
        self._title_bar.set_exporting(False)
        self._export_process = None

