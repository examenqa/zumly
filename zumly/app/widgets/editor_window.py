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
        self._editor.setStyleSheet(f"background-color: {T.SURFACE_ELEVATED};")
        content.addWidget(self._editor)
        
        root.addLayout(content)
        
        # Signal wiring
        self._editor.background_changed.connect(self._on_background_changed)
        self._editor.frame_changed.connect(self._on_frame_changed)
        self._editor.click_effect_changed.connect(self._on_click_effect_changed)
        self._editor.output_dimensions_changed.connect(self._on_output_dimensions_changed)

        self._title_bar.export_clicked.connect(self._on_export)
        self._title_bar.discard_clicked.connect(self.close)
        
        from zumly.app.zoom_engine import ZoomEngine
        self._zoom_engine = ZoomEngine()
        
        self._session = None
        self._project_data = {}
        
        self._load_project()
        
    def _load_project(self):
        if not self._project_path:
            return
            
        logger.info(f"Loading project from {self._project_path}")
        try:
            import os
            from zumly.app.models import RecordingSession
            
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
            if hasattr(self._session, 'voiceover_segments'):
                self._zoom_engine.voiceover_segments = self._session.voiceover_segments
            if hasattr(self._session, 'video_segments'):
                self._zoom_engine.video_segments = self._session.video_segments
                
            # Load video into preview widget
            actual_fps = self._project_data.get("actualFps", 30.0)
            duration = self._preview.load_video(
                video_path,
                actual_fps=actual_fps,
                duration_ms=self._session.duration,
                frame_timestamps=self._session.frame_timestamps
            )
            self._preview.set_debug_keyframes(self._session.keyframes)
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

