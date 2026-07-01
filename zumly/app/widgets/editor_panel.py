"""Right-hand editor panel: zoom settings, smart auto-zoom, background/frame pickers."""

import logging
from typing import List, Tuple

logger = logging.getLogger(__name__)

from PySide6.QtCore import Qt, Signal, QPropertyAnimation, QEasingCurve, QSize, QEvent, QObject
from PySide6.QtGui import QColor, QTextOption, QTextBlockFormat, QTextCursor
from PySide6.QtWidgets import (
    QWidget,
    QVBoxLayout,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QFrame,
    QComboBox,
    QLineEdit,
    QPlainTextEdit,
    QDialog,
    QDialogButtonBox,
    QFormLayout,
    QScrollArea,
    QDoubleSpinBox,
    QSpinBox,
    QColorDialog,
    QAbstractSpinBox,
)

from .. import tokens as T
from ..fluent_effects import apply_shadow, install_focus_ring
from ..icon_loader import load_icon
from ..models import (
    ZoomKeyframe,
    MousePosition,
    KeyEvent,
    ClickEvent,
    ClickEffectPreset,
    CLICK_EFFECT_PRESETS,
    DEFAULT_CLICK_EFFECT,
    TimelineFrame,
)
from ..activity_analyzer import analyze_activity
from ..backgrounds import (
    PRESETS, DEFAULT_PRESET, BackgroundPreset,
    SOLID_PRESETS, GRADIENT_PRESETS, PATTERN_PRESETS,
    CAT_SOLID, CAT_GRADIENT, CAT_PATTERN, CATEGORY_LABELS,
)
from ..frames import FRAME_PRESETS, DEFAULT_FRAME, FramePreset
from ..utils import (
    fmt_time as _fmt,
    detect_available_encoders as _detect_encoders,
    encoder_display_name as _encoder_name,
    best_hw_encoder as _best_encoder,
)

# Zoom depth presets: label → zoom level
ZOOM_DEPTHS = {
    "Subtle":   1.25,
    "Medium":   1.5,
    "Close":    2.0,
    "Detail":   2.5,
}

# Output dimension presets: label → (width, height) or "auto"
OUTPUT_DIMENSIONS: dict[str, Tuple[int, int] | str] = {
    "Auto (source)":  "auto",
    "16:9  (1920×1080)": (1920, 1080),
    "3:2   (1620×1080)": (1620, 1080),
    "4:3   (1440×1080)": (1440, 1080),
    "1:1   (1080×1080)": (1080, 1080),
    "9:16  (1080×1920)": (1080, 1920),
}

# Autozoom sensitivity presets: label → (max_clusters, min_gap_ms)
SENSITIVITY_PRESETS = {
    "Low":    (3, 6000),
    "Medium": (6, 4000),
    "High":   (10, 2500),
}

# TTS voice options
# TTS voice cache (populated from Azure Speech Service on first settings save)
_cached_voices: list[str] = []


class _CollapsibleSection(QWidget):
    """A section header that toggles visibility of its body widget."""

    def __init__(
        self,
        title: str,
        body: QWidget,
        collapsed: bool = False,
        parent: QWidget | None = None,
        *,
        icon_name: str = "",
        subtitle: str = "",
    ) -> None:
        super().__init__(parent)
        self.setObjectName("AccordionSection")
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        # Header button
        self._btn = QPushButton()
        self._btn.setObjectName("AccordionHeader")
        self._btn.setFixedHeight(46)
        self._btn.setCursor(Qt.CursorShape.PointingHandCursor)
        if icon_name:
            self._btn.setIcon(load_icon(icon_name, color=T.FG_2))
            self._btn.setIconSize(QSize(18, 18))
        if subtitle:
            self._btn.setToolTip(subtitle)
        self._btn.setStyleSheet(
            f"QPushButton#AccordionHeader {{"
            f"  background: qlineargradient(x1:0, y1:0, x2:1, y2:0,"
            f"    stop:0 rgba(139, 92, 246, 0.20), stop:0.18 {T.BG_LAYER_3}, stop:1 {T.BG_LAYER_2});"
            f"  color: {T.FG_2};"
            f"  font-size: {T.FONT_SIZE_CAPTION}px;"
            f"  font-weight: 650; border: 1px solid {T.STROKE_2};"
            f"  border-left: 3px solid {T.BRAND};"
            f"  border-radius: {T.RADIUS_MEDIUM}px;"
            f"  text-align: left;"
            f"  padding: 0 12px 0 10px; }}"
            f"QPushButton#AccordionHeader:hover {{"
            f"  background: qlineargradient(x1:0, y1:0, x2:1, y2:0,"
            f"    stop:0 rgba(139, 92, 246, 0.32), stop:0.24 {T.BG_LAYER_4}, stop:1 {T.BG_LAYER_3});"
            f"  color: {T.FG_PRIMARY};"
            f"  border-color: {T.STROKE_1}; }}"
        )
        self._btn.clicked.connect(self._toggle)
        layout.addWidget(self._btn)

        self._body_shell = QFrame()
        self._body_shell.setObjectName("AccordionBody")
        self._body_shell.setStyleSheet(
            f"QFrame#AccordionBody {{"
            f"  background: qlineargradient(x1:0, y1:0, x2:0, y2:1,"
            f"    stop:0 {T.BG_LAYER_3}, stop:1 {T.BG_LAYER_2});"
            f"  border-left: 1px solid {T.STROKE_2};"
            f"  border-right: 1px solid {T.STROKE_2};"
            f"  border-bottom: 1px solid {T.STROKE_2};"
            f"  border-bottom-left-radius: {T.RADIUS_MEDIUM}px;"
            f"  border-bottom-right-radius: {T.RADIUS_MEDIUM}px; }}"
        )
        shell_layout = QVBoxLayout(self._body_shell)
        shell_layout.setContentsMargins(0, 0, 0, 0)
        shell_layout.setSpacing(0)
        shell_layout.addWidget(body)
        layout.addWidget(self._body_shell)

        self._title = title
        self._subtitle = subtitle
        self._collapsed = collapsed
        self._anim = QPropertyAnimation(self._body_shell, b"maximumHeight", self)
        self._anim.setDuration(T.DURATION_GENTLE)
        self._anim.setEasingCurve(QEasingCurve.Type.OutCubic)
        self._anim.finished.connect(self._on_animation_finished)
        self._body_shell.setVisible(not collapsed)
        self._body_shell.setMaximumHeight(0 if collapsed else 16777215)
        self._update_text()

    def _toggle(self) -> None:
        self.set_collapsed(not self._collapsed)

    def expand(self) -> None:
        self.set_collapsed(False)

    def set_collapsed(self, collapsed: bool) -> None:
        if self._collapsed == collapsed and self._anim.state() != QPropertyAnimation.State.Running:
            return
        self._anim.stop()
        self._collapsed = collapsed
        self._update_text()

        if collapsed:
            start = self._body_shell.height() or self._body_shell.sizeHint().height()
            end = 0
        else:
            self._body_shell.setVisible(True)
            self._body_shell.setMaximumHeight(0)
            start = 0
            end = max(1, self._body_shell.sizeHint().height())

        self._anim.setStartValue(start)
        self._anim.setEndValue(end)
        self._anim.start()

    def _on_animation_finished(self) -> None:
        if self._collapsed:
            self._body_shell.setVisible(False)
        else:
            self._body_shell.setMaximumHeight(16777215)

    def _update_text(self) -> None:
        arrow = "▸" if self._collapsed else "▾"
        self._btn.setText(f"{arrow}  {self._title}")


class _WheelToScrollFilter(QObject):
    """Route wheel gestures on option controls to the panel scroll bar."""

    def __init__(self, scroll: QScrollArea, parent: QObject | None = None) -> None:
        super().__init__(parent or scroll)
        self._scroll = scroll

    def eventFilter(self, obj: QObject, event: QEvent) -> bool:
        if event.type() != QEvent.Type.Wheel:
            return False
        delta = event.angleDelta().y()
        if delta:
            bar = self._scroll.verticalScrollBar()
            bar.setValue(bar.value() - delta)
        event.accept()
        return True


class _AISettingsDialog(QDialog):
    """Modal dialog for configuring Azure AI Foundry API credentials."""

    def __init__(self, current_settings, parent=None) -> None:
        super().__init__(parent)
        self.setWindowTitle("AI Settings — Azure AI Foundry")
        self.setMinimumWidth(420)
        self.setStyleSheet(
            f"QDialog {{ background: {T.BG_SURFACE}; }}"
            f"QLabel {{ color: {T.FG_PRIMARY}; font-size: {T.FONT_SIZE_BODY}px; }}"
            f"QLineEdit {{ background: {T.BG_INTERACTIVE}; color: {T.FG_PRIMARY};"
            f"  border: 1px solid {T.CARD_BORDER};"
            f"  border-radius: {T.RADIUS_SMALL}px; padding: 6px;"
            f"  font-size: {T.FONT_SIZE_BODY}px; }}"
            f"QComboBox {{ background: {T.BG_INTERACTIVE}; color: {T.FG_PRIMARY};"
            f"  border: 1px solid {T.CARD_BORDER};"
            f"  border-radius: {T.RADIUS_SMALL}px; padding: {T.SPACE_XXS}px {T.SPACE_XS}px;"
            f"  font-size: {T.FONT_SIZE_BODY}px; }}"
            f"QPushButton {{ background: {T.BG_INTERACTIVE}; color: {T.FG_PRIMARY};"
            f"  border: 1px solid {T.CARD_BORDER};"
            f"  border-radius: {T.RADIUS_SMALL}px; padding: 6px {T.SPACE_MD}px;"
            f"  min-width: 80px; }}"
            f"QPushButton:hover {{ background: {T.BRAND}; }}"
        )

        # Fluent 2 — medium shadow on floating dialog
        apply_shadow(self, level="medium")

        layout = QVBoxLayout(self)
        layout.setSpacing(T.SPACE_MD)

        info = QLabel(
            "Configure your Azure AI Foundry credentials.\n"
            "Smart Zoom uses the chat model deployment.\n"
            "Narration and chapters use the narration deployment.\n"
            "TTS uses Azure Speech Service with the same key."
        )
        info.setWordWrap(True)
        info.setStyleSheet(f"color: {T.FG_SECONDARY}; font-size: 12px;")
        layout.addWidget(info)

        form = QFormLayout()
        form.setSpacing(T.SPACE_SM)

        self._endpoint = QLineEdit(current_settings.endpoint)
        self._endpoint.setPlaceholderText("https://models.inference.ai.azure.com")
        form.addRow("Endpoint:", self._endpoint)

        self._api_key = QLineEdit(current_settings.api_key)
        self._api_key.setEchoMode(QLineEdit.EchoMode.Password)
        self._api_key.setPlaceholderText("Your API key or token")
        form.addRow("API Key:", self._api_key)

        self._chat_model = QLineEdit(current_settings.chat_model)
        self._chat_model.setPlaceholderText("e.g. gpt-4o-mini (Smart Zoom)")
        form.addRow("Chat Model:", self._chat_model)

        self._narration_model = QLineEdit(current_settings.narration_model)
        self._narration_model.setPlaceholderText("e.g. gpt-5.4")
        form.addRow("Narration Model:", self._narration_model)

        self._tts_voice = QLineEdit(current_settings.tts_voice)
        self._tts_voice.setPlaceholderText("e.g. en-US-Ava:DragonHDLatestNeural")
        form.addRow("TTS Voice:", self._tts_voice)

        layout.addLayout(form)

        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel
        )
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

        # Fluent 2 — focus rings on dialog input fields
        for child in self.findChildren(QLineEdit):
            install_focus_ring(child)

    def get_settings(self):
        from ..ai_service import AISettings
        return AISettings(
            endpoint=self._endpoint.text().strip(),
            api_key=self._api_key.text().strip(),
            chat_model=self._chat_model.text().strip(),
            narration_model=self._narration_model.text().strip() or "gpt-5.4",
            tts_voice=self._tts_voice.text().strip() or "en-US-Ava:DragonHDLatestNeural",
        )


def _panel_group_label(text: str) -> QLabel:
    label = QLabel(text)
    label.setObjectName("PanelGroupLabel")
    label.setStyleSheet(
        f"QLabel#PanelGroupLabel {{ color: {T.FG_3};"
        f"  font-size: {T.FONT_SIZE_CAPTION_2}px;"
        f"  font-weight: {T.FONT_WEIGHT_SEMIBOLD};"
        f"  letter-spacing: 0px;"
        f"  padding: {T.SPACE_SM}px {T.SPACE_XS}px {T.SPACE_XXS}px {T.SPACE_XS}px;"
        f"}}"
    )
    return label


class EditorPanel(QWidget):
    """Right-hand sidebar with zoom controls, auto-zoom, background/frame pickers.

    Contains the manual zoom-add button, smart auto-zoom with
    configurable sensitivity and depth, background and device frame
    swatches, output dimension selector, undo/redo buttons, encoder
    selection, and a settings menu with debug overlay toggle.
    """

    remove_keyframe = Signal(str)          # kf id
    add_keyframe_at = Signal(float, float)  # timestamp, zoom
    auto_keyframes_generated = Signal(list)  # list of ZoomKeyframe
    background_changed = Signal(object)     # BackgroundPreset
    frame_changed = Signal(object)          # FramePreset
    click_effect_changed = Signal(object)   # ClickEffectPreset
    debug_overlay_changed = Signal(bool)    # show/hide debug overlay
    output_dimensions_changed = Signal(object)  # (w, h) tuple or "auto"
    undo_requested = Signal()               # undo zoom keyframe change
    redo_requested = Signal()               # redo zoom keyframe change
    encoder_changed = Signal(str)            # encoder_id (e.g. "h264_nvenc")
    # AI feature signals
    ai_zoom_requested = Signal(int, float, int)  # max_clusters, zoom_level, min_gap_ms
    generate_narration_requested = Signal(str, str)  # default voice name, guidance prompt
    add_voiceover_requested = Signal(float, str)  # timestamp_ms, voice
    ai_settings_changed = Signal()               # settings were updated
    auto_detect_chapters_requested = Signal()   # deprecated compatibility signal
    generate_chapters_requested = Signal()      # request AI chapter generation
    chapter_added = Signal(object)               # Chapter object
    chapter_removed = Signal(int)                # chapter timestamp_ms
    segment_speed_changed = Signal(float)        # selected video segment speed
    segment_cut_requested = Signal()
    segment_copy_requested = Signal()
    segment_paste_requested = Signal()
    segment_delete_requested = Signal()
    text_frame_add_requested = Signal(float)      # insert timestamp ms
    image_frame_add_requested = Signal(float)     # insert timestamp ms
    timeline_frame_changed = Signal(str, float, float, str, str, str, int)
    # frame id, timestamp ms, duration ms, text, background, text color, font px
    timeline_frame_delete_requested = Signal(str)
    timeline_frame_select_requested = Signal(str)
    highlight_add_requested = Signal(str)        # shape: "rect" or "circle"
    highlight_timing_changed = Signal(float, float)  # start/end ms for selected highlight
    highlight_delete_requested = Signal()

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("EditorPanel")
        self.setFixedWidth(320)

        self.setStyleSheet(f"""
            QWidget#EditorPanel {{
                background: qlineargradient(x1:0, y1:0, x2:0, y2:1,
                    stop:0 #18181f, stop:0.42 {T.BG_LAYER_1}, stop:1 #101014);
                border-left: 1px solid {T.DIVIDER};
            }}
            QPushButton#CtrlBtn {{
                background: qlineargradient(x1:0, y1:0, x2:0, y2:1,
                    stop:0 {T.BG_LAYER_4}, stop:1 {T.BG_LAYER_3});
                border-radius: {T.RADIUS_MEDIUM}px;
                border: 1px solid rgba(255, 255, 255, 0.10);
                color: {T.TEXT_PRIMARY};
                padding: 0 {T.SPACE_MD}px;
                font-size: {T.FONT_SIZE_BODY}px;
                font-weight: {T.FONT_WEIGHT_MEDIUM};
            }}
            QPushButton#CtrlBtn:hover {{
                background: qlineargradient(x1:0, y1:0, x2:0, y2:1,
                    stop:0 #45414f, stop:1 {T.BG_LAYER_4});
                border-color: rgba(139, 92, 246, 0.58);
            }}
            QPushButton#CtrlBtn:pressed {{
                background-color: {T.BG_LAYER_2};
            }}
            QPushButton#CtrlBtn:disabled {{
                color: {T.FG_DISABLED};
                border-color: {T.STROKE_SUBTLE};
                background-color: {T.BG_LAYER_2};
            }}
            QComboBox#DepthCombo {{
                border: 1px solid rgba(255, 255, 255, 0.11);
                border-radius: {T.RADIUS_MEDIUM}px;
                padding: 4px 8px;
                background: qlineargradient(x1:0, y1:0, x2:0, y2:1,
                    stop:0 #26262d, stop:1 #19191f);
                color: {T.TEXT_PRIMARY};
                font-size: {T.FONT_SIZE_BODY}px;
            }}
            QComboBox#DepthCombo::drop-down {{
                border: none;
            }}
            QComboBox#DepthCombo:hover {{
                border-color: rgba(139, 92, 246, 0.58);
                background-color: {T.BG_LAYER_3};
            }}
            QDoubleSpinBox#DepthCombo,
            QSpinBox#DepthCombo,
            QLineEdit#DepthCombo {{
                border: 1px solid rgba(255, 255, 255, 0.11);
                border-radius: {T.RADIUS_MEDIUM}px;
                padding: 4px 8px;
                background: qlineargradient(x1:0, y1:0, x2:0, y2:1,
                    stop:0 #26262d, stop:1 #19191f);
                color: {T.TEXT_PRIMARY};
                font-size: {T.FONT_SIZE_BODY}px;
            }}
            QDoubleSpinBox#DepthCombo::up-button,
            QDoubleSpinBox#DepthCombo::down-button,
            QSpinBox#DepthCombo::up-button,
            QSpinBox#DepthCombo::down-button {{
                width: 22px;
                border-left: 1px solid {T.STROKE_2};
                background-color: {T.BG_LAYER_3};
            }}
            QDoubleSpinBox#DepthCombo::up-button:hover,
            QDoubleSpinBox#DepthCombo::down-button:hover,
            QSpinBox#DepthCombo::up-button:hover,
            QSpinBox#DepthCombo::down-button:hover {{
                background-color: {T.BG_LAYER_4};
            }}
            QPlainTextEdit#FrameTextEdit {{
                background: qlineargradient(x1:0, y1:0, x2:0, y2:1,
                    stop:0 #24242b, stop:1 #17171d);
                color: {T.TEXT_PRIMARY};
                border: 1px solid rgba(255, 255, 255, 0.11);
                border-radius: {T.RADIUS_MEDIUM}px;
                padding: 8px;
                font-size: {T.FONT_SIZE_BODY}px;
                selection-background-color: {T.BRAND};
            }}
            QPushButton#ColorSwatch {{
                border: 1px solid rgba(255, 255, 255, 0.12);
                border-radius: {T.RADIUS_MEDIUM}px;
                color: {T.TEXT_PRIMARY};
                padding: 0 8px;
                text-align: left;
            }}
            QPushButton#FramePill {{
                background: qlineargradient(x1:0, y1:0, x2:1, y2:0,
                    stop:0 rgba(139, 92, 246, 0.20), stop:1 {T.BG_LAYER_3});
                border: 1px solid rgba(255, 255, 255, 0.10);
                border-radius: {T.RADIUS_MEDIUM}px;
                color: {T.TEXT_PRIMARY};
                padding: 0 10px;
                text-align: left;
                font-size: {T.FONT_SIZE_CAPTION}px;
            }}
            QPushButton#FramePill:hover {{
                background-color: {T.BG_LAYER_4};
                border-color: rgba(139, 92, 246, 0.58);
            }}
            QPushButton#FramePill[active="true"] {{
                background: qlineargradient(x1:0, y1:0, x2:1, y2:0,
                    stop:0 rgba(139, 92, 246, 0.52), stop:1 rgba(59, 130, 246, 0.24));
                border-color: {T.BRAND};
                color: {T.FG_PRIMARY};
            }}
            QGroupBox {{
                border: none;
                padding: 8px;
            }}
            QLabel#Secondary {{
                color: {T.TEXT_MUTED};
                font-size: {T.FONT_SIZE_CAPTION}px;
            }}
        """)

        # Outer layout: collapsible sections + fixed bottom bar
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)

        # Scrollable content area (for when many sections are expanded)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        scroll.setStyleSheet(
            f"QScrollArea {{ border: none; background: transparent; }}"
            f"QScrollBar:vertical {{ background: transparent; width: 10px; margin: 4px 2px 4px 2px; }}"
            f"QScrollBar::handle:vertical {{ background: rgba(139, 92, 246, 0.34);"
            f"  min-height: 28px; border-radius: 4px; }}"
            f"QScrollBar::handle:vertical:hover {{ background: rgba(139, 92, 246, 0.56); }}"
            f"QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {{ height: 0; }}"
            f"QScrollBar::add-page:vertical, QScrollBar::sub-page:vertical {{ background: transparent; }}"
        )
        self._settings_scroll = scroll
        scroll_content = QWidget()
        self._container = QVBoxLayout(scroll_content)
        self._container.setContentsMargins(T.SPACE_MD, T.SPACE_MD, T.SPACE_MD, T.SPACE_MD)
        self._container.setSpacing(T.SPACE_SM)
        scroll.setWidget(scroll_content)
        outer.addWidget(scroll, 1)

        self._current_zoom_level = ZOOM_DEPTHS["Medium"]
        self._trim_start_ms: float = 0.0
        self._trim_end_ms: float = 0.0
        self._duration: float = 0.0

        self._container.addWidget(_panel_group_label("Automation"))

        # ── Smart Zoom (collapsible) ─────────────────────────────────
        zoom_body = QWidget()
        zoom_lay = QVBoxLayout(zoom_body)
        zoom_lay.setContentsMargins(T.SPACE_LG, T.SPACE_MD, T.SPACE_LG, T.SPACE_SM)
        zoom_lay.setSpacing(T.SPACE_SM)

        qa_desc = QLabel("Analyze activity to auto-generate zoom keyframes.")
        qa_desc.setObjectName("Secondary")
        qa_desc.setWordWrap(True)
        zoom_lay.addWidget(qa_desc)

        sens_row = QHBoxLayout()
        sens_row.setSpacing(T.SPACE_SM)
        sens_label = QLabel("Sensitivity")
        sens_label.setObjectName("Secondary")
        sens_label.setFixedWidth(65)
        sens_row.addWidget(sens_label)
        self._sensitivity_combo = QComboBox()
        self._sensitivity_combo.setObjectName("DepthCombo")
        self._sensitivity_combo.setFixedHeight(30)
        for name in SENSITIVITY_PRESETS:
            self._sensitivity_combo.addItem(name)
        self._sensitivity_combo.setCurrentText("Medium")
        self._sensitivity_combo.setToolTip(
            "Low = fewer zoom keyframes (major activity only)\n"
            "Medium = balanced\n"
            "High = more zoom keyframes (follows smaller movements)"
        )
        sens_row.addWidget(self._sensitivity_combo, 1)
        zoom_lay.addLayout(sens_row)

        activity_btn = QPushButton("Auto-generate zoom (local)")
        activity_btn.setIcon(load_icon("gauge", color=T.FG_1))
        activity_btn.setObjectName("CtrlBtn")
        activity_btn.setFixedHeight(36)
        activity_btn.clicked.connect(self._auto_keyframe)
        zoom_lay.addWidget(activity_btn)

        self._auto_status = QLabel("")
        self._auto_status.setObjectName("Secondary")
        self._auto_status.setWordWrap(True)
        self._auto_status.setVisible(False)
        zoom_lay.addWidget(self._auto_status)

        # "or" separator
        or_row = QHBoxLayout()
        or_row.setSpacing(T.SPACE_SM)
        or_line_l = QFrame()
        or_line_l.setFrameShape(QFrame.Shape.HLine)
        or_line_l.setStyleSheet(f"background-color: {T.BORDER_SUBTLE}; max-height: 1px;")
        or_row.addWidget(or_line_l, 1)
        or_label = QLabel("or")
        or_label.setObjectName("Secondary")
        or_label.setStyleSheet(f"color: {T.FG_MUTED}; font-size: {T.FONT_SIZE_CAPTION}px;")
        or_row.addWidget(or_label)
        or_line_r = QFrame()
        or_line_r.setFrameShape(QFrame.Shape.HLine)
        or_line_r.setStyleSheet(f"background-color: {T.BORDER_SUBTLE}; max-height: 1px;")
        or_row.addWidget(or_line_r, 1)
        zoom_lay.addLayout(or_row)

        ai_zoom_btn = QPushButton("  Auto-generate zoom (AI)")
        ai_zoom_btn.setIcon(load_icon("search", color=T.FG_PRIMARY))
        ai_zoom_btn.setObjectName("CtrlBtn")
        ai_zoom_btn.setFixedHeight(36)
        ai_zoom_btn.setToolTip("Use AI (Azure AI Foundry) to analyze activity\nand generate zoom keyframes.")
        ai_zoom_btn.clicked.connect(self._on_ai_zoom)
        zoom_lay.addWidget(ai_zoom_btn)
        self._btn_ai_zoom = ai_zoom_btn

        self._ai_zoom_status = QLabel("")
        self._ai_zoom_status.setObjectName("Secondary")
        self._ai_zoom_status.setWordWrap(True)
        self._ai_zoom_status.setVisible(False)
        zoom_lay.addWidget(self._ai_zoom_status)

        self._container.addWidget(
            _CollapsibleSection(
                "Smart Zoom",
                zoom_body,
                icon_name="search",
                subtitle="Generate and tune zoom keyframes from activity.",
            )
        )

        self._container.addWidget(_panel_group_label("Story"))

        # ── Chapters (collapsible) ───────────────────────────────────
        chapters_body = QWidget()
        chapters_lay = QVBoxLayout(chapters_body)
        chapters_lay.setContentsMargins(T.SPACE_LG, T.SPACE_MD, T.SPACE_LG, T.SPACE_SM)
        chapters_lay.setSpacing(T.SPACE_SM)

        chapters_desc = QLabel(
            "Generate AI chapter markers for navigation in long recordings.\n"
            "Chapters reuse the same recording understanding as narration — shared frame samples, cursor activity, click beats, and zoom edits."
        )
        chapters_desc.setObjectName("Secondary")
        chapters_desc.setWordWrap(True)
        chapters_lay.addWidget(chapters_desc)

        self._btn_generate_chapters = QPushButton("  Generate chapters")
        self._btn_generate_chapters.setIcon(load_icon("video", color=T.FG_PRIMARY))
        self._btn_generate_chapters.setObjectName("CtrlBtn")
        self._btn_generate_chapters.setFixedHeight(32)
        self._btn_generate_chapters.setToolTip(
            "Use GPT-5.4 to suggest chapter markers from the same shared recording context\n"
            "as narration. Re-running replaces only generated chapters and keeps manual markers."
        )
        self._btn_generate_chapters.clicked.connect(self._on_generate_chapters)
        chapters_lay.addWidget(self._btn_generate_chapters)

        self._btn_add_chapter = QPushButton("+ Add chapter manually")
        self._btn_add_chapter.setObjectName("CtrlBtn")
        self._btn_add_chapter.setFixedHeight(32)
        self._btn_add_chapter.setToolTip("Add a chapter marker at the current playback position.")
        self._btn_add_chapter.clicked.connect(self._on_add_chapter)
        chapters_lay.addWidget(self._btn_add_chapter)

        chapters_hint = QLabel(
            "Generated chapters refresh only the AI markers. Manual chapter markers stay where you place them."
        )
        chapters_hint.setObjectName("Secondary")
        chapters_hint.setWordWrap(True)
        chapters_lay.addWidget(chapters_hint)

        self._chapters_status = QLabel("")
        self._chapters_status.setObjectName("Secondary")
        self._chapters_status.setWordWrap(True)
        self._chapters_status.setVisible(False)
        chapters_lay.addWidget(self._chapters_status)

        self._container.addWidget(
            _CollapsibleSection(
                "Chapters",
                chapters_body,
                collapsed=True,
                icon_name="video",
                subtitle="Create chapter markers for long recordings.",
            )
        )

        # ── Voiceover (collapsible) ──────────────────────────────────
        vo_body = QWidget()
        vo_lay = QVBoxLayout(vo_body)
        vo_lay.setContentsMargins(T.SPACE_LG, T.SPACE_MD, T.SPACE_LG, T.SPACE_SM)
        vo_lay.setSpacing(T.SPACE_SM)

        vo_desc = QLabel(
            "Draft five presentation-style voiceover segments for the full recording and\n"
            "let zumly voice them automatically. Narration reuses the same recording\n"
            "understanding as AI chapters, while the script stays focused on the takeaway rather than on-screen mechanics."
        )
        vo_desc.setObjectName("Secondary")
        vo_desc.setWordWrap(True)
        vo_lay.addWidget(vo_desc)

        vo_guidance_label = QLabel("Guidance (optional)")
        vo_guidance_label.setObjectName("Secondary")
        vo_guidance_label.setStyleSheet(
            f"color: {T.FG_MUTED}; font-size: {T.FONT_SIZE_CAPTION}px;"
        )
        vo_lay.addWidget(vo_guidance_label)

        self._narration_guidance = QPlainTextEdit()
        self._narration_guidance.setObjectName("NarrationGuidance")
        self._narration_guidance.setPlaceholderText(
            "Steer what the narration focuses on - e.g. 'lead with the time saved' "
            "or 'emphasize this is a one-click flow'."
        )
        self._narration_guidance.setFixedHeight(64)
        self._narration_guidance.setStyleSheet(
            f"QPlainTextEdit#NarrationGuidance {{"
            f"  background-color: {T.BG_ELEVATED};"
            f"  color: {T.FG_PRIMARY};"
            f"  border: 1px solid {T.BORDER_SUBTLE};"
            f"  border-radius: 4px;"
            f"  padding: 4px 6px;"
            f"  font-size: {T.FONT_SIZE_BODY}px;"
            f"}}"
            f"QPlainTextEdit#NarrationGuidance:focus {{"
            f"  border-color: {T.BRAND};"
            f"}}"
        )
        vo_lay.addWidget(self._narration_guidance)

        self._btn_generate_narration = QPushButton("  Generate narration")
        self._btn_generate_narration.setIcon(load_icon("edit", color=T.FG_PRIMARY))
        self._btn_generate_narration.setObjectName("CtrlBtn")
        self._btn_generate_narration.setFixedHeight(32)
        self._btn_generate_narration.setToolTip(
            "Use GPT-5.4 to draft five presentation-style voiceover segments,\n"
            "keep the wording focused on what matters rather than on-screen mechanics,\n"
            "save the combined script beside the recording, then start speech automatically through the normal voiceover flow.\n"
            "Open any generated segment to review the spoken line, then drag or delete it like any other voiceover."
        )
        self._btn_generate_narration.clicked.connect(self._on_generate_narration)
        vo_lay.addWidget(self._btn_generate_narration)

        self._narration_status = QLabel("")
        self._narration_status.setObjectName("Secondary")
        self._narration_status.setWordWrap(True)
        self._narration_status.setVisible(False)
        vo_lay.addWidget(self._narration_status)

        vo_or_row = QHBoxLayout()
        vo_or_row.setSpacing(T.SPACE_SM)
        vo_or_line_l = QFrame()
        vo_or_line_l.setFrameShape(QFrame.Shape.HLine)
        vo_or_line_l.setStyleSheet(
            f"background-color: {T.BORDER_SUBTLE}; max-height: 1px;"
        )
        vo_or_row.addWidget(vo_or_line_l, 1)
        vo_or_label = QLabel("or")
        vo_or_label.setObjectName("Secondary")
        vo_or_label.setStyleSheet(
            f"color: {T.FG_MUTED}; font-size: {T.FONT_SIZE_CAPTION}px;"
        )
        vo_or_row.addWidget(vo_or_label)
        vo_or_line_r = QFrame()
        vo_or_line_r.setFrameShape(QFrame.Shape.HLine)
        vo_or_line_r.setStyleSheet(
            f"background-color: {T.BORDER_SUBTLE}; max-height: 1px;"
        )
        vo_or_row.addWidget(vo_or_line_r, 1)
        vo_lay.addLayout(vo_or_row)

        self._btn_add_voiceover = QPushButton("  Add voiceover")
        self._btn_add_voiceover.setIcon(load_icon("mic", color=T.FG_PRIMARY))
        self._btn_add_voiceover.setObjectName("CtrlBtn")
        self._btn_add_voiceover.setFixedHeight(32)
        self._btn_add_voiceover.setToolTip(
            "Add a manual text-to-speech voiceover segment at the current playback position.\n"
            "Use the Voice track to review, drag, or delete it later."
        )
        self._btn_add_voiceover.clicked.connect(self._on_add_voiceover)
        vo_lay.addWidget(self._btn_add_voiceover)

        self._vo_status = QLabel("")
        self._vo_status.setObjectName("Secondary")
        self._vo_status.setWordWrap(True)
        self._vo_status.setVisible(False)
        vo_lay.addWidget(self._vo_status)

        self._container.addWidget(
            _CollapsibleSection(
                "Narration & Voiceover",
                vo_body,
                collapsed=True,
                icon_name="mic",
                subtitle="Generate or add spoken narration.",
            )
        )

        self._container.addWidget(_panel_group_label("Presentation"))

        # ── Background picker (collapsible) ──────────────────────────
        bg_body = QWidget()
        bg_lay = QVBoxLayout(bg_body)
        bg_lay.setContentsMargins(T.SPACE_LG, T.SPACE_MD, T.SPACE_LG, T.SPACE_SM)
        bg_lay.setSpacing(T.SPACE_SM)

        self._bg_category_combo = QComboBox()
        self._bg_category_combo.setObjectName("DepthCombo")
        self._bg_category_combo.setFixedHeight(30)
        for cat_key in (CAT_SOLID, CAT_GRADIENT, CAT_PATTERN):
            self._bg_category_combo.addItem(CATEGORY_LABELS[cat_key], cat_key)
        self._bg_category_combo.currentIndexChanged.connect(self._on_bg_category_changed)
        bg_lay.addWidget(self._bg_category_combo)

        from PySide6.QtWidgets import QStackedWidget
        self._bg_stack = QStackedWidget()
        self._bg_buttons: list[QPushButton] = []
        self._bg_category_widgets: dict[str, QWidget] = {}
        for cat_key, cat_presets in (
            (CAT_SOLID, SOLID_PRESETS),
            (CAT_GRADIENT, GRADIENT_PRESETS),
            (CAT_PATTERN, PATTERN_PRESETS),
        ):
            page = QWidget()
            page.setStyleSheet("background: transparent;")
            grid = self._build_bg_grid(cat_presets, cat_key)
            page.setLayout(grid)
            self._bg_stack.addWidget(page)
            self._bg_category_widgets[cat_key] = page
        bg_lay.addWidget(self._bg_stack)
        self._current_bg_preset = DEFAULT_PRESET

        self._background_section = _CollapsibleSection(
            "Background",
            bg_body,
            collapsed=True,
            icon_name="brightness_high",
            subtitle="Choose the canvas treatment behind the recording.",
        )
        self._container.addWidget(self._background_section)

        # ── Frame picker (collapsible) ───────────────────────────────
        fr_body = QWidget()
        fr_lay = QVBoxLayout(fr_body)
        fr_lay.setContentsMargins(T.SPACE_LG, T.SPACE_MD, T.SPACE_LG, T.SPACE_SM)
        fr_lay.setSpacing(T.SPACE_SM)

        self._frame_combo = QComboBox()
        self._frame_combo.setObjectName("DepthCombo")
        self._frame_combo.setFixedHeight(30)
        for fp in FRAME_PRESETS:
            self._frame_combo.addItem(fp.name)
        self._frame_combo.setCurrentText(DEFAULT_FRAME.name)
        self._frame_combo.currentTextChanged.connect(self._on_frame_changed)
        fr_lay.addWidget(self._frame_combo)
        self._current_frame_preset = DEFAULT_FRAME

        self._frame_section = _CollapsibleSection(
            "Device Frame",
            fr_body,
            collapsed=True,
            icon_name="desktop",
            subtitle="Wrap the recording in a device/browser frame.",
        )
        self._container.addWidget(self._frame_section)

        # ── Click effect picker (collapsible) ────────────────────────
        click_body = QWidget()
        click_lay = QVBoxLayout(click_body)
        click_lay.setContentsMargins(T.SPACE_LG, T.SPACE_MD, T.SPACE_LG, T.SPACE_SM)
        click_lay.setSpacing(T.SPACE_SM)

        self._click_combo = QComboBox()
        self._click_combo.setObjectName("DepthCombo")
        self._click_combo.setFixedHeight(30)
        for preset in CLICK_EFFECT_PRESETS:
            self._click_combo.addItem(preset.name)
        self._click_combo.setCurrentText(DEFAULT_CLICK_EFFECT.name)
        self._click_combo.currentTextChanged.connect(self._on_click_changed)
        click_lay.addWidget(self._click_combo)
        self._current_click_preset = DEFAULT_CLICK_EFFECT

        self._click_section = _CollapsibleSection(
            "Click Effects",
            click_body,
            collapsed=True,
            icon_name="location",
            subtitle="Style click rings and cursor emphasis in exports.",
        )
        self._container.addWidget(self._click_section)

        self._container.addWidget(_panel_group_label("Timeline"))

        # ── Selected range actions (collapsible) ─────────────────────
        retime_body = QWidget()
        retime_lay = QVBoxLayout(retime_body)
        retime_lay.setContentsMargins(T.SPACE_LG, T.SPACE_MD, T.SPACE_LG, T.SPACE_SM)
        retime_lay.setSpacing(T.SPACE_SM)

        self._edited_preview_status = QLabel("Edited preview follows kept ranges during playback.")
        self._edited_preview_status.setObjectName("Secondary")
        self._edited_preview_status.setWordWrap(True)
        retime_lay.addWidget(self._edited_preview_status)

        self._speed_combo = QComboBox()
        self._speed_combo.setObjectName("DepthCombo")
        self._speed_combo.setFixedHeight(30)
        for label, speed in (("1x", 1.0), ("1.5x", 1.5), ("2x", 2.0), ("4x", 4.0), ("8x", 8.0)):
            self._speed_combo.addItem(label, speed)
        self._speed_combo.setEnabled(False)
        self._speed_combo.currentIndexChanged.connect(self._on_segment_speed_changed)
        self._speed_combo.setToolTip("Select a range in the timeline, then choose its playback speed.")
        retime_lay.addWidget(self._speed_combo)

        range_action_row = QHBoxLayout()
        range_action_row.setSpacing(T.SPACE_XS)
        self._btn_range_cut = QPushButton("Cut")
        self._btn_range_cut.setObjectName("CtrlBtn")
        self._btn_range_cut.setFixedHeight(30)
        self._btn_range_cut.clicked.connect(self.segment_cut_requested.emit)
        range_action_row.addWidget(self._btn_range_cut, 1)

        self._btn_range_copy = QPushButton("Copy")
        self._btn_range_copy.setObjectName("CtrlBtn")
        self._btn_range_copy.setFixedHeight(30)
        self._btn_range_copy.clicked.connect(self.segment_copy_requested.emit)
        range_action_row.addWidget(self._btn_range_copy, 1)

        self._btn_range_paste = QPushButton("Paste")
        self._btn_range_paste.setObjectName("CtrlBtn")
        self._btn_range_paste.setFixedHeight(30)
        self._btn_range_paste.clicked.connect(self.segment_paste_requested.emit)
        range_action_row.addWidget(self._btn_range_paste, 1)
        retime_lay.addLayout(range_action_row)

        self._btn_range_delete = QPushButton("Delete selected range")
        self._btn_range_delete.setObjectName("CtrlBtn")
        self._btn_range_delete.setFixedHeight(32)
        self._btn_range_delete.clicked.connect(self.segment_delete_requested.emit)
        retime_lay.addWidget(self._btn_range_delete)

        for button in (
            self._btn_range_cut,
            self._btn_range_copy,
            self._btn_range_paste,
            self._btn_range_delete,
        ):
            button.setEnabled(False)

        self._retime_status = QLabel("Select a range in the timeline")
        self._retime_status.setObjectName("Secondary")
        self._retime_status.setWordWrap(True)
        retime_lay.addWidget(self._retime_status)

        self._retiming_section = _CollapsibleSection(
            "Selected Range",
            retime_body,
            collapsed=True,
            icon_name="cut",
            subtitle="Cut, copy, delete, and retime selected ranges.",
        )
        self._container.addWidget(self._retiming_section)

        # ── Inserted frames (collapsible) ──────────────────────────
        frames_body = QWidget()
        frames_lay = QVBoxLayout(frames_body)
        frames_lay.setContentsMargins(T.SPACE_LG, T.SPACE_MD, T.SPACE_LG, T.SPACE_SM)
        frames_lay.setSpacing(T.SPACE_SM)

        insert_label = QLabel("Insert at")
        insert_label.setObjectName("Secondary")
        frames_lay.addWidget(insert_label)

        self._frame_timestamp_spin = QDoubleSpinBox()
        self._frame_timestamp_spin.setObjectName("DepthCombo")
        self._frame_timestamp_spin.setDecimals(2)
        self._frame_timestamp_spin.setRange(0.0, 36000.0)
        self._frame_timestamp_spin.setSuffix(" s")
        self._frame_timestamp_spin.setFixedHeight(32)
        self._frame_timestamp_spin.setSingleStep(0.25)
        self._frame_timestamp_spin.setButtonSymbols(QAbstractSpinBox.ButtonSymbols.PlusMinus)
        self._frame_timestamp_spin.valueChanged.connect(self._on_timeline_frame_edited)
        frames_lay.addWidget(self._frame_timestamp_spin)

        add_frame_row = QHBoxLayout()
        add_frame_row.setSpacing(T.SPACE_XS)
        self._frame_kind_combo = QComboBox()
        self._frame_kind_combo.setObjectName("DepthCombo")
        self._frame_kind_combo.setFixedHeight(32)
        self._frame_kind_combo.addItem("Text frame", "text")
        self._frame_kind_combo.addItem("Picture frame", "image")
        add_frame_row.addWidget(self._frame_kind_combo, 1)

        self._btn_add_frame = QPushButton("Add frame")
        self._btn_add_frame.setObjectName("CtrlBtn")
        self._btn_add_frame.setFixedHeight(32)
        self._btn_add_frame.clicked.connect(self._on_add_frame)
        add_frame_row.addWidget(self._btn_add_frame, 1)
        frames_lay.addLayout(add_frame_row)

        self._frame_status = QLabel("Choose an insert time, then add a text or picture frame.")
        self._frame_status.setObjectName("Secondary")
        self._frame_status.setWordWrap(True)
        frames_lay.addWidget(self._frame_status)

        self._frame_list_label = QLabel("Existing frames")
        self._frame_list_label.setObjectName("Secondary")
        frames_lay.addWidget(self._frame_list_label)

        self._frame_list_host = QWidget()
        self._frame_list_layout = QVBoxLayout(self._frame_list_host)
        self._frame_list_layout.setContentsMargins(0, 0, 0, 0)
        self._frame_list_layout.setSpacing(T.SPACE_XS)
        frames_lay.addWidget(self._frame_list_host)

        duration_label = QLabel("Duration")
        duration_label.setObjectName("Secondary")
        frames_lay.addWidget(duration_label)

        self._frame_duration_spin = QDoubleSpinBox()
        self._frame_duration_spin.setObjectName("DepthCombo")
        self._frame_duration_spin.setDecimals(2)
        self._frame_duration_spin.setRange(0.25, 600.0)
        self._frame_duration_spin.setSuffix(" s")
        self._frame_duration_spin.setFixedHeight(30)
        self._frame_duration_spin.setSingleStep(0.25)
        self._frame_duration_spin.setButtonSymbols(QAbstractSpinBox.ButtonSymbols.PlusMinus)
        self._frame_duration_spin.setEnabled(False)
        self._frame_duration_spin.valueChanged.connect(self._on_timeline_frame_edited)
        frames_lay.addWidget(self._frame_duration_spin)

        self._frame_text_edit = QPlainTextEdit()
        self._frame_text_edit.setObjectName("FrameTextEdit")
        self._frame_text_edit.setPlaceholderText("Text frame copy")
        self._frame_text_edit.setFixedHeight(92)
        self._frame_text_edit.setEnabled(False)
        self._frame_text_edit.setLayoutDirection(Qt.LayoutDirection.LeftToRight)
        text_option = QTextOption()
        text_option.setTextDirection(Qt.LayoutDirection.LeftToRight)
        text_option.setWrapMode(QTextOption.WrapMode.WordWrap)
        self._frame_text_edit.document().setDefaultTextOption(text_option)
        self._frame_text_edit.setTabChangesFocus(True)
        self._frame_text_edit.textChanged.connect(self._on_timeline_frame_edited)
        frames_lay.addWidget(self._frame_text_edit)

        style_row = QHBoxLayout()
        style_row.setSpacing(T.SPACE_XS)
        self._frame_bg_button = QPushButton("Background")
        self._frame_bg_button.setObjectName("ColorSwatch")
        self._frame_bg_button.setFixedHeight(30)
        self._frame_bg_button.setEnabled(False)
        self._frame_bg_button.clicked.connect(lambda: self._choose_frame_color("background"))
        style_row.addWidget(self._frame_bg_button, 1)

        self._frame_text_color_button = QPushButton("Text")
        self._frame_text_color_button.setObjectName("ColorSwatch")
        self._frame_text_color_button.setFixedHeight(30)
        self._frame_text_color_button.setEnabled(False)
        self._frame_text_color_button.clicked.connect(lambda: self._choose_frame_color("text"))
        style_row.addWidget(self._frame_text_color_button, 1)
        frames_lay.addLayout(style_row)

        font_row = QHBoxLayout()
        font_row.setSpacing(T.SPACE_SM)
        font_label = QLabel("Text size")
        font_label.setObjectName("Secondary")
        font_label.setFixedWidth(72)
        font_row.addWidget(font_label)
        self._frame_font_spin = QSpinBox()
        self._frame_font_spin.setObjectName("DepthCombo")
        self._frame_font_spin.setRange(12, 180)
        self._frame_font_spin.setSuffix(" px")
        self._frame_font_spin.setFixedHeight(30)
        self._frame_font_spin.setButtonSymbols(QAbstractSpinBox.ButtonSymbols.PlusMinus)
        self._frame_font_spin.setEnabled(False)
        self._frame_font_spin.valueChanged.connect(self._on_timeline_frame_edited)
        font_row.addWidget(self._frame_font_spin, 1)
        frames_lay.addLayout(font_row)

        self._btn_delete_frame = QPushButton("Delete selected frame")
        self._btn_delete_frame.setObjectName("CtrlBtn")
        self._btn_delete_frame.setFixedHeight(32)
        self._btn_delete_frame.setEnabled(False)
        self._btn_delete_frame.clicked.connect(self._on_delete_timeline_frame)
        frames_lay.addWidget(self._btn_delete_frame)

        self._selected_frame_id = ""
        self._selected_frame_kind = ""
        self._frame_background_color = "#111827"
        self._frame_text_color = "#f9fafb"
        self._normalizing_frame_text = False
        self._frame_section = _CollapsibleSection(
            "Frames",
            frames_body,
            collapsed=True,
            icon_name="add",
            subtitle="Insert editable text cards or picture cards between recording ranges.",
        )
        self._container.addWidget(self._frame_section)

        # ── Highlights (collapsible) ────────────────────────────────
        highlight_body = QWidget()
        highlight_lay = QVBoxLayout(highlight_body)
        highlight_lay.setContentsMargins(T.SPACE_LG, T.SPACE_MD, T.SPACE_LG, T.SPACE_SM)
        highlight_lay.setSpacing(T.SPACE_SM)

        highlight_row = QHBoxLayout()
        highlight_row.setSpacing(T.SPACE_SM)
        highlight_label = QLabel("Shape")
        highlight_label.setObjectName("Secondary")
        highlight_label.setFixedWidth(48)
        highlight_row.addWidget(highlight_label)

        self._highlight_shape_combo = QComboBox()
        self._highlight_shape_combo.setObjectName("DepthCombo")
        self._highlight_shape_combo.setFixedHeight(30)
        self._highlight_shape_combo.addItem("Rectangle", "rect")
        self._highlight_shape_combo.addItem("Circle", "circle")
        highlight_row.addWidget(self._highlight_shape_combo, 1)
        highlight_lay.addLayout(highlight_row)

        self._btn_add_highlight = QPushButton("Place highlight on preview")
        self._btn_add_highlight.setObjectName("CtrlBtn")
        self._btn_add_highlight.setFixedHeight(34)
        self._btn_add_highlight.clicked.connect(self._on_add_highlight)
        highlight_lay.addWidget(self._btn_add_highlight)

        timing_label = QLabel("Selected highlight timing")
        timing_label.setObjectName("Secondary")
        highlight_lay.addWidget(timing_label)

        timing_row = QHBoxLayout()
        timing_row.setSpacing(T.SPACE_XS)
        self._highlight_start_spin = QDoubleSpinBox()
        self._highlight_start_spin.setObjectName("DepthCombo")
        self._highlight_start_spin.setDecimals(2)
        self._highlight_start_spin.setRange(0.0, 36000.0)
        self._highlight_start_spin.setSuffix(" s")
        self._highlight_start_spin.setFixedHeight(30)
        self._highlight_start_spin.setEnabled(False)
        self._highlight_start_spin.valueChanged.connect(self._on_highlight_timing_changed)
        timing_row.addWidget(self._highlight_start_spin, 1)

        self._highlight_end_spin = QDoubleSpinBox()
        self._highlight_end_spin.setObjectName("DepthCombo")
        self._highlight_end_spin.setDecimals(2)
        self._highlight_end_spin.setRange(0.0, 36000.0)
        self._highlight_end_spin.setSuffix(" s")
        self._highlight_end_spin.setFixedHeight(30)
        self._highlight_end_spin.setEnabled(False)
        self._highlight_end_spin.valueChanged.connect(self._on_highlight_timing_changed)
        timing_row.addWidget(self._highlight_end_spin, 1)
        highlight_lay.addLayout(timing_row)

        self._btn_delete_highlight = QPushButton("Delete selected highlight")
        self._btn_delete_highlight.setObjectName("CtrlBtn")
        self._btn_delete_highlight.setFixedHeight(32)
        self._btn_delete_highlight.setEnabled(False)
        self._btn_delete_highlight.clicked.connect(self.highlight_delete_requested.emit)
        highlight_lay.addWidget(self._btn_delete_highlight)

        self._highlight_status = QLabel("Click the preview after choosing Place highlight.")
        self._highlight_status.setObjectName("Secondary")
        self._highlight_status.setWordWrap(True)
        highlight_lay.addWidget(self._highlight_status)

        self._highlight_section = _CollapsibleSection(
            "Highlights",
            highlight_body,
            collapsed=True,
            icon_name="brightness_high",
            subtitle="Dim the screen around a focused rectangular or circular area.",
        )
        self._container.addWidget(self._highlight_section)

        self._container.addWidget(_panel_group_label("Export"))

        # ── Output dimensions (collapsible) ──────────────────────────
        dim_body = QWidget()
        dim_lay = QVBoxLayout(dim_body)
        dim_lay.setContentsMargins(T.SPACE_LG, T.SPACE_MD, T.SPACE_LG, T.SPACE_SM)
        dim_lay.setSpacing(T.SPACE_SM)

        self._dim_combo = QComboBox()
        self._dim_combo.setObjectName("DepthCombo")
        self._dim_combo.setFixedHeight(30)
        for name in OUTPUT_DIMENSIONS:
            self._dim_combo.addItem(name)
        self._dim_combo.setCurrentText("Auto (source)")
        self._dim_combo.currentTextChanged.connect(self._on_dim_changed)
        self._dim_combo.setToolTip(
            "Choose the aspect ratio and resolution for the exported video.\n"
            "Auto = same dimensions as the recorded source."
        )
        dim_lay.addWidget(self._dim_combo)

        self._current_output_dim = "auto"
        self._output_section = _CollapsibleSection(
            "Output Size",
            dim_body,
            collapsed=True,
            icon_name="desktop",
            subtitle="Choose the exported video dimensions.",
        )
        self._container.addWidget(self._output_section)

        # End of scrollable content
        self._container.addStretch()

        # ── Fixed bottom bar (outside scroll area) ──────────────────
        bottom_bar = QFrame()
        bottom_bar.setObjectName("PanelFooter")
        bottom_bar.setStyleSheet(
            f"QFrame#PanelFooter {{"
            f"  background: qlineargradient(x1:0, y1:0, x2:0, y2:1,"
            f"    stop:0 #24242b, stop:1 {T.BG_LAYER_2});"
            f"  border-top: 1px solid rgba(255, 255, 255, 0.10); }}"
            f"QPushButton#FooterBtn {{"
            f"  background: qlineargradient(x1:0, y1:0, x2:0, y2:1,"
            f"    stop:0 {T.BG_LAYER_4}, stop:1 {T.BG_LAYER_3});"
            f"  color: {T.FG_PRIMARY}; border: 1px solid rgba(255, 255, 255, 0.10);"
            f"  border-radius: {T.RADIUS_MEDIUM}px; padding: 0 {T.SPACE_SM}px;"
            f"  font-size: {T.FONT_SIZE_CAPTION}px; font-weight: {T.FONT_WEIGHT_MEDIUM}; }}"
            f"QPushButton#FooterBtn:hover {{ background-color: {T.BG_LAYER_4};"
            f"  border-color: rgba(139, 92, 246, 0.58); }}"
            f"QPushButton#FooterBtn:pressed {{ background-color: {T.BG_LAYER_1}; }}"
        )
        bottom_layout = QVBoxLayout(bottom_bar)
        bottom_layout.setContentsMargins(T.SPACE_MD, T.SPACE_SM, T.SPACE_MD, T.SPACE_MD)
        bottom_layout.setSpacing(T.SPACE_SM)

        # Undo / Redo row
        undo_redo_row = QHBoxLayout()
        undo_redo_row.setSpacing(T.SPACE_SM)
        self._btn_undo = QPushButton("Undo")
        self._btn_undo.setObjectName("FooterBtn")
        self._btn_undo.setFixedHeight(32)
        self._btn_undo.setToolTip("Undo last zoom change (Ctrl+Z)")
        self._btn_undo.clicked.connect(self.undo_requested.emit)
        undo_redo_row.addWidget(self._btn_undo, 1)

        self._btn_redo = QPushButton("Redo")
        self._btn_redo.setObjectName("FooterBtn")
        self._btn_redo.setFixedHeight(32)
        self._btn_redo.setToolTip("Redo last undone change (Ctrl+Y)")
        self._btn_redo.clicked.connect(self.redo_requested.emit)
        undo_redo_row.addWidget(self._btn_redo, 1)
        bottom_layout.addLayout(undo_redo_row)

        # Info + settings row
        info_row = QHBoxLayout()
        info_row.setSpacing(T.SPACE_SM)

        self._info_label = QPushButton("Info")
        self._info_label.setObjectName("FooterBtn")
        self._info_label.setFixedHeight(32)
        self._info_label.setToolTip("Duration: 0:00\nMouse samples: 0\nKeyframes: 0")
        self._info_label.setCursor(Qt.CursorShape.WhatsThisCursor)
        self._info_label.clicked.connect(self._show_session_info)
        info_row.addWidget(self._info_label, 1)

        self._btn_settings = QPushButton("Settings")
        self._btn_settings.setObjectName("FooterBtn")
        self._btn_settings.setFixedHeight(32)
        self._btn_settings.setToolTip("Settings")
        self._btn_settings.setCursor(Qt.CursorShape.PointingHandCursor)
        self._btn_settings.clicked.connect(self._show_settings_menu)
        info_row.addWidget(self._btn_settings, 1)

        bottom_layout.addLayout(info_row)
        outer.addWidget(bottom_bar)

        # Debug overlay state (managed via settings menu) — on by default
        self._debug_overlay_enabled = False

        # Encoder preference — deferred detection for faster startup.
        # Actual detection happens lazily on first settings menu open or export.
        self._encoder_id: str = "libx264"
        self._encoder_detected: bool = False

        self._mouse_track: List[MousePosition] = []
        self._key_events: List[KeyEvent] = []
        self._click_events: List[ClickEvent] = []
        self._monitor_rect: dict = {}
        self.set_undo_redo_enabled(False, False)

        # Fluent 2 — focus ring glow on all interactive controls
        for child in self.findChildren(QPushButton):
            install_focus_ring(child)
        # Avoid QGraphicsEffect on combo boxes; on Windows/PySide it can make
        # native popup lists appear to close or vanish between clicks.
        self._install_option_scroll_guards()

    # ── position / depth controls ───────────────────────────────────

    def _install_option_scroll_guards(self) -> None:
        """Prevent passive panel scrolling from mutating combo/spin values."""
        self._wheel_scroll_filter = _WheelToScrollFilter(self._settings_scroll, self)
        for combo in self.findChildren(QComboBox):
            combo.installEventFilter(self._wheel_scroll_filter)
            combo.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        for spin in self.findChildren(QAbstractSpinBox):
            spin.installEventFilter(self._wheel_scroll_filter)
            spin.setFocusPolicy(Qt.FocusPolicy.StrongFocus)

    def _show_settings_menu(self) -> None:
        """Show settings popup menu from the cog button."""
        from PySide6.QtWidgets import QMenu
        menu = QMenu(self)
        menu.setStyleSheet(
            f"QMenu {{ background: {T.BG_INTERACTIVE}; color: {T.FG_PRIMARY};"
            f"  border: 1px solid {T.CARD_BORDER}; padding: {T.SPACE_XXS}px; }}"
            f"QMenu::item {{ padding: 6px 20px; }}"
            f"QMenu::item:selected {{ background: {T.BRAND}; }}"
        )

        # Debug overlay toggle
        check_text = "✓ " if self._debug_overlay_enabled else "  "
        debug_act = menu.addAction(f"{check_text}Show zoom debug overlay")
        debug_act.setToolTip(
            "Overlay colored markers on the preview showing\n"
            "where activity was detected and why zoom\n"
            "keyframes were placed."
        )
        debug_act.triggered.connect(self._toggle_debug_overlay)

        # AI settings
        menu.addSeparator()
        ai_act = menu.addAction("\U0001f916 AI Settings\u2026")
        ai_act.triggered.connect(self._show_ai_settings)

        # Encoder submenu
        encoder_menu = menu.addMenu("Video encoder")
        encoder_menu.setStyleSheet(menu.styleSheet())
        self._ensure_encoder_detected()
        available = _detect_encoders()
        for enc_id in available:
            label = _encoder_name(enc_id)
            tick = "✓ " if enc_id == self._encoder_id else "  "
            act = encoder_menu.addAction(f"{tick}{label}")
            act.setData(enc_id)
            act.triggered.connect(lambda checked=False, eid=enc_id: self._set_encoder(eid))

        # About
        menu.addSeparator()
        about_act = menu.addAction("About zumly\u2026")
        about_act.triggered.connect(self._show_about)

        menu.exec(self._btn_settings.mapToGlobal(self._btn_settings.rect().topRight()))

    def _set_encoder(self, enc_id: str) -> None:
        """Update the selected encoder and emit signal."""
        self._encoder_id = enc_id
        self.encoder_changed.emit(enc_id)
        logger.info("Encoder set to: %s", _encoder_name(enc_id))

    @property
    def encoder_id(self) -> str:
        """The currently selected ffmpeg encoder ID."""
        self._ensure_encoder_detected()
        return self._encoder_id

    def _ensure_encoder_detected(self) -> None:
        """Lazily detect the best available encoder on first access."""
        if self._encoder_detected:
            return
        self._encoder_detected = True
        best = _best_encoder()
        if self._encoder_id == "libx264" and best != "libx264":
            self._encoder_id = best
            self.encoder_changed.emit(best)
            logger.info("Auto-detected encoder: %s", _encoder_name(best))

    def set_encoder_by_id(self, enc_id: str) -> None:
        """Programmatically set the encoder (e.g. from QSettings)."""
        self._encoder_id = enc_id

    def _toggle_debug_overlay(self) -> None:
        """Toggle the debug overlay state and emit the signal."""
        self._debug_overlay_enabled = not self._debug_overlay_enabled
        self.debug_overlay_changed.emit(self._debug_overlay_enabled)

    def _on_dim_changed(self, text: str) -> None:
        self._output_section.expand()
        dim = OUTPUT_DIMENSIONS.get(text, "auto")
        self._current_output_dim = dim
        self.output_dimensions_changed.emit(dim)

    def _on_manual_zoom_in(self) -> None:
        self.add_keyframe_at.emit(-1.0, self._current_zoom_level)

    @property
    def zoom_level(self) -> float:
        return self._current_zoom_level

    @property
    def follow_cursor(self) -> bool:
        return True

    @property
    def bg_preset(self) -> BackgroundPreset:
        return self._current_bg_preset

    @property
    def frame_preset(self) -> FramePreset:
        return self._current_frame_preset

    @property
    def output_dim(self):
        """Return the currently selected output dimensions: (w, h) tuple or 'auto'."""
        return self._current_output_dim

    def _on_frame_changed(self, text: str) -> None:
        self._frame_section.expand()
        fp = next((f for f in FRAME_PRESETS if f.name == text), DEFAULT_FRAME)
        self._current_frame_preset = fp
        self.frame_changed.emit(fp)

    def set_background_by_name(self, name: str) -> None:
        """Programmatically select a background preset by name."""
        preset = next((p for p in PRESETS if p.name == name), None)
        if preset is None:
            return
        self._current_bg_preset = preset
        # Switch combo to the correct category page
        cat_index = {CAT_SOLID: 0, CAT_GRADIENT: 1, CAT_PATTERN: 2}.get(
            preset.category, 0
        )
        self._bg_category_combo.setCurrentIndex(cat_index)
        # Highlight the matching swatch button
        for btn in self._bg_buttons:
            if btn.toolTip() == name:
                self._highlight_bg_button(btn)
                break

    def set_frame_by_name(self, name: str) -> None:
        """Programmatically select a frame preset by name."""
        self._frame_combo.setCurrentText(name)

    # ── background picker ───────────────────────────────────────────

    def _on_bg_category_changed(self, index: int) -> None:
        """Switch the visible swatch grid when the category combo changes."""
        self._background_section.expand()
        self._bg_stack.setCurrentIndex(index)

    def _build_bg_grid(self, presets: list, category: str):
        """Build a grid of colour-swatch buttons for one category."""
        from PySide6.QtWidgets import QGridLayout
        grid = QGridLayout()
        grid.setSpacing(T.SPACE_XS)
        grid.setContentsMargins(0, T.SPACE_XS, 0, T.SPACE_XS)

        # Patterns get larger, fewer-per-row swatches so the pattern is visible
        if category == CAT_PATTERN:
            size, cols = 44, 4
        elif category == CAT_GRADIENT:
            size, cols = 36, 6
        else:
            size, cols = 28, 8

        for idx, preset in enumerate(presets):
            btn = QPushButton()
            btn.setFixedSize(size, size)
            btn.setToolTip(preset.name)
            btn.setStyleSheet(self._bg_swatch_css(preset, "transparent"))
            btn.clicked.connect(
                lambda checked, p=preset, b=btn: self._on_bg_selected(p, b)
            )
            grid.addWidget(btn, idx // cols, idx % cols)
            self._bg_buttons.append(btn)
        return grid

    def _on_bg_selected(self, preset: BackgroundPreset, btn: QPushButton) -> None:
        self._background_section.expand()
        self._current_bg_preset = preset
        self._highlight_bg_button(btn)
        self.background_changed.emit(preset)

    def _highlight_bg_button(self, active_btn: QPushButton) -> None:
        """Update border highlight on the selected swatch."""
        for btn in self._bg_buttons:
            tip = btn.toolTip()
            preset = next((p for p in PRESETS if p.name == tip), None)
            if preset is None:
                continue
            is_active = btn is active_btn
            border = T.BRAND_ACTIVE if is_active else "transparent"
            btn.setStyleSheet(self._bg_swatch_css(preset, border))

    @staticmethod
    def _bg_swatch_css(preset: BackgroundPreset, border: str) -> str:
        """Return QSS for a background swatch button."""
        r1, g1, b1 = preset.color_top
        r2, g2, b2 = preset.color_bottom
        kind = preset.kind
        hover = T.BRAND
        rad = T.RADIUS_SMALL

        if kind == "wavy":
            mr, mg, mb = (r1+r2)//2, (g1+g2)//2, (b1+b2)//2
            return (
                f"QPushButton {{ background: qlineargradient("
                f"x1:0, y1:0, x2:1, y2:1, "
                f"stop:0 rgb({r1},{g1},{b1}), "
                f"stop:0.5 rgb({mr},{mg},{mb}), "
                f"stop:1 rgb({r2},{g2},{b2})); "
                f"border: 2px solid {border}; border-radius: {rad}px; }}"
                f"QPushButton:hover {{ border-color: {hover}; }}"
            )
        elif kind == "radial":
            return (
                f"QPushButton {{ background: qradialgradient("
                f"cx:0.5, cy:0.5, radius:0.7, fx:0.5, fy:0.5, "
                f"stop:0 rgb({r1},{g1},{b1}), "
                f"stop:1 rgb({r2},{g2},{b2})); "
                f"border: 2px solid {border}; border-radius: {rad}px; }}"
                f"QPushButton:hover {{ border-color: {hover}; }}"
            )
        elif kind == "spotlight":
            return (
                f"QPushButton {{ background: qradialgradient("
                f"cx:0.8, cy:0.2, radius:0.9, fx:0.8, fy:0.2, "
                f"stop:0 rgb({r1},{g1},{b1}), "
                f"stop:1 rgb({r2},{g2},{b2})); "
                f"border: 2px solid {border}; border-radius: {rad}px; }}"
                f"QPushButton:hover {{ border-color: {hover}; }}"
            )
        elif kind == "diagonal":
            return (
                f"QPushButton {{ background: qlineargradient("
                f"x1:0, y1:0, x2:1, y2:1, "
                f"stop:0 rgb({r1},{g1},{b1}), "
                f"stop:0.25 rgb({r2},{g2},{b2}), "
                f"stop:0.5 rgb({r1},{g1},{b1}), "
                f"stop:0.75 rgb({r2},{g2},{b2}), "
                f"stop:1 rgb({r1},{g1},{b1})); "
                f"border: 2px solid {border}; border-radius: {rad}px; }}"
                f"QPushButton:hover {{ border-color: {hover}; }}"
            )
        elif kind == "dots":
            return (
                f"QPushButton {{ background: qradialgradient("
                f"cx:0.3, cy:0.3, radius:0.4, fx:0.3, fy:0.3, "
                f"stop:0 rgb({r1},{g1},{b1}), "
                f"stop:1 rgb({r2},{g2},{b2})); "
                f"border: 2px solid {border}; border-radius: {rad}px; }}"
                f"QPushButton:hover {{ border-color: {hover}; }}"
            )
        elif kind == "chevron":
            mr, mg, mb = (r1+r2)//2, (g1+g2)//2, (b1+b2)//2
            return (
                f"QPushButton {{ background: qlineargradient("
                f"x1:0, y1:0, x2:1, y2:1, "
                f"stop:0 rgb({r2},{g2},{b2}), "
                f"stop:0.3 rgb({r1},{g1},{b1}), "
                f"stop:0.5 rgb({r2},{g2},{b2}), "
                f"stop:0.7 rgb({r1},{g1},{b1}), "
                f"stop:1 rgb({r2},{g2},{b2})); "
                f"border: 2px solid {border}; border-radius: {rad}px; }}"
                f"QPushButton:hover {{ border-color: {hover}; }}"
            )
        elif kind == "rings":
            return (
                f"QPushButton {{ background: qradialgradient("
                f"cx:0.5, cy:0.5, radius:0.5, fx:0.5, fy:0.5, "
                f"stop:0 rgb({r2},{g2},{b2}), "
                f"stop:0.4 rgb({r1},{g1},{b1}), "
                f"stop:0.6 rgb({r2},{g2},{b2}), "
                f"stop:0.8 rgb({r1},{g1},{b1}), "
                f"stop:1 rgb({r2},{g2},{b2})); "
                f"border: 2px solid {border}; border-radius: {rad}px; }}"
                f"QPushButton:hover {{ border-color: {hover}; }}"
            )
        elif kind == "gradient":
            return (
                f"QPushButton {{ background: qlineargradient("
                f"x1:0, y1:0, x2:0, y2:1, "
                f"stop:0 rgb({r1},{g1},{b1}), "
                f"stop:1 rgb({r2},{g2},{b2})); "
                f"border: 2px solid {border}; border-radius: {rad}px; }}"
                f"QPushButton:hover {{ border-color: {hover}; }}"
            )
        else:  # solid
            return (
                f"QPushButton {{ background: rgb({r1},{g1},{b1}); "
                f"border: 2px solid {border}; border-radius: {rad}px; }}"
                f"QPushButton:hover {{ border-color: {hover}; }}"
            )

    # ── public ──────────────────────────────────────────────────────

    def refresh(
        self,
        keyframes: List[ZoomKeyframe],
        mouse_track: List[MousePosition],
        duration: float,
        monitor_rect: dict | None = None,
        key_events: List[KeyEvent] | None = None,
        click_events: List[ClickEvent] | None = None,
        trim_start_ms: float = 0.0,
        trim_end_ms: float = 0.0,
        output_duration: float | None = None,
    ) -> None:
        """Update cached session data used by auto-zoom and the info tooltip."""
        self._mouse_track = mouse_track
        self._key_events = key_events or []
        self._click_events = click_events or []
        self._duration = duration
        self._trim_start_ms = trim_start_ms
        self._trim_end_ms = trim_end_ms
        if monitor_rect is not None:
            self._monitor_rect = monitor_rect

        out_dur = output_duration if output_duration is not None else duration
        tooltip = (
            f"Duration: {_fmt(duration)}\n"
            f"Output duration: {_fmt(out_dur)}\n"
            f"Mouse samples: {len(mouse_track):,}\n"
            f"Keyframes: {len(keyframes)}"
        )
        self._info_label.setToolTip(tooltip)

    def set_undo_redo_enabled(self, can_undo: bool, can_redo: bool) -> None:
        """Reflect undo/redo stack availability in the footer buttons."""
        self._btn_undo.setEnabled(can_undo)
        self._btn_redo.setEnabled(can_redo)

    def _show_session_info(self) -> None:
        """Show the cached recording summary in a readable popup."""
        from PySide6.QtWidgets import QMessageBox

        dlg = QMessageBox(self)
        dlg.setWindowTitle("Recording Info")
        dlg.setIcon(QMessageBox.Icon.NoIcon)
        dlg.setTextFormat(Qt.TextFormat.PlainText)
        dlg.setText(self._info_label.toolTip())
        dlg.setStyleSheet(
            f"QMessageBox {{ background: {T.BG_SURFACE}; }}"
            f"QMessageBox QLabel {{ color: {T.FG_PRIMARY}; font-size: {T.FONT_SIZE_BODY}px; }}"
            f"QPushButton {{ min-width: 80px; min-height: 28px;"
            f"  background: {T.BG_INTERACTIVE}; color: {T.FG_PRIMARY};"
            f"  border: 1px solid {T.CARD_BORDER};"
            f"  border-radius: {T.RADIUS_SMALL}px; padding: {T.SPACE_XXS}px {T.SPACE_MD}px; }}"
            f"QPushButton:hover {{ background: {T.BRAND}; }}"
        )
        dlg.exec()

    def _auto_keyframe(self) -> None:
        track = self._mouse_track

        # Apply trim range: only analyze data within the trimmed window
        t_start = self._trim_start_ms
        t_end = self._trim_end_ms if self._trim_end_ms > 0 else self._duration
        if t_start > 0 or (self._trim_end_ms > 0 and t_end < self._duration):
            track = [m for m in track if t_start <= m.timestamp <= t_end]
            filtered_keys: list = [
                KeyEvent(timestamp=k.timestamp)
                for k in self._key_events if t_start <= k.timestamp <= t_end
            ]
            filtered_clicks: list = [
                ClickEvent(timestamp=c.timestamp, x=c.x, y=c.y)
                for c in self._click_events if t_start <= c.timestamp <= t_end
            ]
        else:
            filtered_keys = list(self._key_events)
            filtered_clicks = list(self._click_events)

        if len(track) < 10:
            self._auto_status.setText("Not enough mouse data to analyze.")
            self._auto_status.setVisible(True)
            return
        if not self._monitor_rect:
            self._auto_status.setText("No monitor info available.")
            self._auto_status.setVisible(True)
            return

        # Get sensitivity settings
        sens_name = self._sensitivity_combo.currentText()
        max_clusters, min_gap = SENSITIVITY_PRESETS.get(sens_name, (6, 4000))

        try:
            keyframes = analyze_activity(
                track, self._monitor_rect,
                key_events=filtered_keys or None,
                click_events=filtered_clicks or None,
                zoom_level=self._current_zoom_level,
                follow_cursor=self.follow_cursor,
                max_clusters=max_clusters,
                min_gap_ms=min_gap,
            )
        except Exception as exc:
            self._auto_status.setText(f"Analysis error: {exc}")
            self._auto_status.setVisible(True)
            return

        if not keyframes:
            self._auto_status.setText("No significant activity clusters detected.")
            self._auto_status.setVisible(True)
            return

        # Count actual zoom-in keyframes (zoom > 1.0) as the cluster count
        n_clusters = sum(1 for kf in keyframes if kf.zoom > 1.0 and not kf.reason.startswith("Pan to:"))
        self._auto_status.setText(
            f"Generated {len(keyframes)} keyframes from {n_clusters} activity cluster{'s' if n_clusters != 1 else ''}."
        )
        self._auto_status.setVisible(True)
        self.auto_keyframes_generated.emit(keyframes)

    # ── AI features ─────────────────────────────────────────────────

    def _on_ai_zoom(self) -> None:
        """Request AI-powered zoom analysis."""
        sens_name = self._sensitivity_combo.currentText()
        max_clusters, min_gap = SENSITIVITY_PRESETS.get(sens_name, (6, 4000))
        self._ai_zoom_status.setText("Requesting AI analysis\u2026")
        self._ai_zoom_status.setVisible(True)
        self._btn_ai_zoom.setEnabled(False)
        self.ai_zoom_requested.emit(max_clusters, self._current_zoom_level, min_gap)

    def set_ai_zoom_status(self, text: str) -> None:
        """Update the AI zoom status label from outside."""
        self._ai_zoom_status.setText(text)
        self._ai_zoom_status.setVisible(bool(text))
        self._btn_ai_zoom.setEnabled(True)

    def _on_add_voiceover(self) -> None:
        """Request adding a voiceover segment at the current playback position."""
        self.add_voiceover_requested.emit(-1.0, "")  # voice selected in dialog

    def _on_generate_narration(self) -> None:
        """Request automated narration for the full recording."""
        guidance = self._narration_guidance.toPlainText().strip()
        self.generate_narration_requested.emit(self.selected_voice, guidance)

    def set_narration_status(self, text: str) -> None:
        """Update the narration status label from outside."""
        self._narration_status.setText(text)
        self._narration_status.setVisible(bool(text))

    def set_voiceover_status(self, text: str) -> None:
        """Update the voiceover status label from outside."""
        self._vo_status.setText(text)
        self._vo_status.setVisible(bool(text))
        self._btn_add_voiceover.setEnabled(True)

    def set_ai_busy(self, busy: bool) -> None:
        """Disable/enable AI buttons while an operation is in progress."""
        self._btn_ai_zoom.setEnabled(not busy)
        self._btn_generate_chapters.setEnabled(not busy)
        self._btn_generate_narration.setEnabled(not busy)
        self._btn_add_voiceover.setEnabled(not busy)

    @property
    def selected_voice(self) -> str:
        """Return the default TTS voice from settings."""
        from PySide6.QtCore import QSettings
        return QSettings("zumly", "zumly").value(
            "ai/ttsVoice", "en-US-Ava:DragonHDLatestNeural"
        )

    def _show_ai_settings(self) -> None:
        """Open the AI settings dialog."""
        from ..ai_service import AISettings
        from ..credentials import protect, unprotect
        from PySide6.QtCore import QSettings

        settings = QSettings("zumly", "zumly")
        current = AISettings(
            endpoint=settings.value("ai/endpoint", "") or "",
            api_key=unprotect(settings.value("ai/apiKey", "") or ""),
            chat_model=settings.value("ai/chatModel", "") or "",
            narration_model=settings.value("ai/narrationModel", "gpt-5.4") or "gpt-5.4",
            tts_voice=settings.value("ai/ttsVoice", "en-US-Ava:DragonHDLatestNeural") or "en-US-Ava:DragonHDLatestNeural",
        )

        dlg = _AISettingsDialog(current, self)
        if dlg.exec() == QDialog.DialogCode.Accepted:
            result = dlg.get_settings()
            settings.setValue("ai/endpoint", result.endpoint)
            settings.setValue("ai/apiKey", protect(result.api_key))
            settings.setValue("ai/chatModel", result.chat_model)
            settings.setValue("ai/narrationModel", result.narration_model)
            settings.setValue("ai/ttsVoice", result.tts_voice)
            self.ai_settings_changed.emit()
            logger.info("AI settings updated")
            # Load available TTS voices in the background
            self._load_tts_voices(result.endpoint, result.api_key)

    def _load_tts_voices(self, endpoint: str, api_key: str) -> None:
        """Fetch en-US voices from Azure Speech Service on a background thread."""
        global _cached_voices
        if not endpoint or not api_key:
            return
        import threading

        def _fetch() -> None:
            global _cached_voices
            try:
                import azure.cognitiveservices.speech as speechsdk
                speech_config = speechsdk.SpeechConfig(
                    subscription=api_key,
                    endpoint=endpoint.rstrip("/"),
                )
                synthesizer = speechsdk.SpeechSynthesizer(
                    speech_config=speech_config, audio_config=None,
                )
                result = synthesizer.get_voices_async().get()
                if result.reason == speechsdk.ResultReason.VoicesListRetrieved:
                    _cached_voices = sorted(
                        v.short_name for v in result.voices
                        if v.locale == "en-US" and "Neural" in v.short_name
                    )
                    logger.info("Loaded %d en-US voices", len(_cached_voices))
            except Exception as exc:
                logger.warning("Failed to load TTS voices: %s", exc)

        threading.Thread(target=_fetch, daemon=True).start()

    def _on_generate_chapters(self) -> None:
        """Request AI-generated chapter markers."""
        self.generate_chapters_requested.emit()

    def _on_add_chapter(self) -> None:
        """Add a chapter marker at the current playback position."""
        from ..models import Chapter
        chapter = Chapter(
            timestamp_ms=int(self._current_time_ms),
            name=f"Chapter",  # Will be auto-numbered by main_window
            auto_detected=False,
        )
        self.chapter_added.emit(chapter)

    def set_chapters_status(self, text: str) -> None:
        """Update the chapters status label from outside."""
        self._chapters_status.setText(text)
        self._chapters_status.setVisible(bool(text))

    def _show_about(self) -> None:
        """Show the About dialog with links to GitHub."""
        from PySide6.QtWidgets import QMessageBox
        from ..version import __version__
        dlg = QMessageBox(self)
        dlg.setWindowTitle("About zumly")
        dlg.setIcon(QMessageBox.Icon.NoIcon)
        dlg.setTextFormat(Qt.TextFormat.RichText)
        dlg.setText(
            f"<h3>zumly v{__version__}</h3>"
            "<p>A Windows screen recorder with cinematic<br>"
            "cursor-following zoom and AI features.</p>"
            f'<p><a href="https://github.com/sabbour/zumly" '
            f'style="color: {T.BRAND_ACTIVE};">GitHub Repository</a></p>'
            f'<p><a href="https://github.com/sabbour/zumly/issues" '
            f'style="color: {T.BRAND_ACTIVE};">Report a Bug / Request a Feature</a></p>'
            f'<p style="color: {T.FG_MUTED}; font-size: {T.FONT_SIZE_CAPTION}px; margin-top: {T.SPACE_XS}px;">'
            "MIT License<br>"
            "Copyright \u00a9 2026 Ahmed Sabbour</p>"
        )
        dlg.setStyleSheet(
            f"QMessageBox {{ background: {T.BG_SURFACE}; }}"
            f"QMessageBox QLabel {{ color: {T.FG_PRIMARY}; font-size: {T.FONT_SIZE_BODY}px; }}"
            f"QPushButton {{ min-width: 80px; min-height: 28px;"
            f"  background: {T.BG_INTERACTIVE}; color: {T.FG_PRIMARY};"
            f"  border: 1px solid {T.CARD_BORDER};"
            f"  border-radius: {T.RADIUS_SMALL}px; padding: {T.SPACE_XXS}px {T.SPACE_MD}px; }}"
            f"QPushButton:hover {{ background: {T.BRAND}; }}"
        )
        dlg.exec()

    def _on_click_changed(self, name: str) -> None:
        """User picked a new click effect preset."""
        self._click_section.expand()
        preset = next((p for p in CLICK_EFFECT_PRESETS if p.name == name), DEFAULT_CLICK_EFFECT)
        self._current_click_preset = preset
        self.click_effect_changed.emit(preset)

    def current_click_preset(self) -> ClickEffectPreset:
        """Return the currently selected click effect preset."""
        return self._current_click_preset

    def set_click_preset(self, preset: ClickEffectPreset) -> None:
        """Set the click effect preset from external code (e.g., project load)."""
        self._current_click_preset = preset
        self._click_combo.setCurrentText(preset.name)

    def set_click_effect_by_name(self, name: str) -> None:
        """Programmatically select a click effect preset by name."""
        preset = next((p for p in CLICK_EFFECT_PRESETS if p.name == name), DEFAULT_CLICK_EFFECT)
        self.set_click_preset(preset)

    def _on_segment_speed_changed(self, index: int) -> None:
        if index < 0 or not self._speed_combo.isEnabled():
            return
        speed = self._speed_combo.itemData(index)
        try:
            self.segment_speed_changed.emit(float(speed))
        except (TypeError, ValueError):
            self.segment_speed_changed.emit(1.0)

    def set_selected_segment_speed(self, speed: float | None, index: int = -1) -> None:
        """Reflect the selected timeline segment in the retiming controls."""
        self._speed_combo.blockSignals(True)
        self._speed_combo.setEnabled(speed is not None)
        if speed is None:
            self._retime_status.setText("Select a range in the timeline")
            self._speed_combo.setCurrentIndex(0)
            self.set_range_actions_enabled(False, self._btn_range_paste.isEnabled())
        else:
            best_index = 0
            best_delta = float("inf")
            for i in range(self._speed_combo.count()):
                item_speed = float(self._speed_combo.itemData(i))
                delta = abs(item_speed - float(speed))
                if delta < best_delta:
                    best_delta = delta
                    best_index = i
            self._speed_combo.setCurrentIndex(best_index)
            label = self._speed_combo.itemText(best_index)
            if index >= 0:
                self._retime_status.setText(f"Range {index + 1} speed: {label}")
            else:
                self._retime_status.setText(f"Selected range speed: {label}")
            self._retiming_section.expand()
            self.set_range_actions_enabled(True, self._btn_range_paste.isEnabled())
        self._speed_combo.blockSignals(False)

    def _on_add_highlight(self) -> None:
        shape = self._highlight_shape_combo.currentData() or "rect"
        self._highlight_section.expand()
        self._highlight_status.setText("Click the preview where the highlight should appear.")
        self.highlight_add_requested.emit(str(shape))

    def _on_highlight_timing_changed(self) -> None:
        start_ms = float(self._highlight_start_spin.value()) * 1000.0
        end_ms = float(self._highlight_end_spin.value()) * 1000.0
        if end_ms <= start_ms:
            end_ms = start_ms + 250.0
        self.highlight_timing_changed.emit(start_ms, end_ms)

    def set_selected_highlight_timing(
        self,
        start_ms: float | None,
        end_ms: float | None = None,
        duration_ms: float = 0.0,
    ) -> None:
        """Reflect selected highlight timing in the Highlights section."""
        has_selection = start_ms is not None and end_ms is not None
        for spin in (self._highlight_start_spin, self._highlight_end_spin):
            spin.blockSignals(True)
            spin.setEnabled(has_selection)
            spin.setMaximum(max(1.0, float(duration_ms or 0.0) / 1000.0))
        self._btn_delete_highlight.setEnabled(has_selection)
        if has_selection:
            self._highlight_start_spin.setValue(max(0.0, float(start_ms) / 1000.0))
            self._highlight_end_spin.setValue(max(0.0, float(end_ms) / 1000.0))
            self._highlight_status.setText("Drag the highlight to move it, or drag a handle to resize it.")
            self._highlight_section.expand()
        else:
            self._highlight_start_spin.setValue(0.0)
            self._highlight_end_spin.setValue(0.0)
            self._highlight_status.setText("Click the preview after choosing Place highlight.")
        for spin in (self._highlight_start_spin, self._highlight_end_spin):
            spin.blockSignals(False)

    def _on_timeline_frame_edited(self) -> None:
        if not getattr(self, "_selected_frame_id", ""):
            return
        if not getattr(self, "_normalizing_frame_text", False):
            self._normalize_frame_text_document()
        timestamp_ms = float(self._frame_timestamp_spin.value()) * 1000.0
        duration_ms = float(self._frame_duration_spin.value()) * 1000.0
        text = self._clean_frame_text(self._frame_text_edit.toPlainText())
        self.timeline_frame_changed.emit(
            self._selected_frame_id,
            timestamp_ms,
            duration_ms,
            text,
            self._frame_background_color,
            self._frame_text_color,
            int(self._frame_font_spin.value()),
        )

    def _on_add_frame(self) -> None:
        if self._frame_kind_combo.currentData() == "image":
            self._on_add_image_frame()
        else:
            self._on_add_text_frame()

    def _on_add_text_frame(self) -> None:
        self.text_frame_add_requested.emit(float(self._frame_timestamp_spin.value()) * 1000.0)

    def _on_add_image_frame(self) -> None:
        self.image_frame_add_requested.emit(float(self._frame_timestamp_spin.value()) * 1000.0)

    def _clean_frame_text(self, text: str) -> str:
        bidi_controls = {
            "\u202a", "\u202b", "\u202c", "\u202d", "\u202e",
            "\u2066", "\u2067", "\u2068", "\u2069",
            "\u200e", "\u200f",
        }
        return "".join(ch for ch in text if ch not in bidi_controls)

    def _normalize_frame_text_document(self) -> None:
        if getattr(self, "_normalizing_frame_text", False):
            return
        self._normalizing_frame_text = True
        try:
            self._normalize_frame_text_document_unchecked()
        finally:
            self._normalizing_frame_text = False

    def _normalize_frame_text_document_unchecked(self) -> None:
        doc = self._frame_text_edit.document()
        option = doc.defaultTextOption()
        option.setTextDirection(Qt.LayoutDirection.LeftToRight)
        option.setWrapMode(QTextOption.WrapMode.WordWrap)
        option.setAlignment(Qt.AlignmentFlag.AlignLeft)
        doc.setDefaultTextOption(option)

        cursor = QTextCursor(doc)
        cursor.select(QTextCursor.SelectionType.Document)
        block_format = QTextBlockFormat()
        block_format.setLayoutDirection(Qt.LayoutDirection.LeftToRight)
        block_format.setAlignment(Qt.AlignmentFlag.AlignLeft)
        cursor.mergeBlockFormat(block_format)
        self._frame_text_edit.setLayoutDirection(Qt.LayoutDirection.LeftToRight)

    def _move_frame_text_cursor_to_end(self) -> None:
        cursor = self._frame_text_edit.textCursor()
        cursor.movePosition(QTextCursor.MoveOperation.End)
        self._frame_text_edit.setTextCursor(cursor)

    def _set_color_button(self, button: QPushButton, color: str, label: str) -> None:
        button.setText(label)
        button.setStyleSheet(
            f"QPushButton#ColorSwatch {{ background-color: {color};"
            f" color: {T.TEXT_PRIMARY}; border: 1px solid {T.STROKE_2};"
            f" border-radius: {T.RADIUS_SMALL}px; padding: 0 8px; text-align: left; }}"
        )

    def _choose_frame_color(self, target: str) -> None:
        current = self._frame_background_color if target == "background" else self._frame_text_color
        color = QColorDialog.getColor(QColor(current), self, "Choose frame color")
        if not color.isValid():
            return
        if target == "background":
            self._frame_background_color = color.name()
            self._set_color_button(self._frame_bg_button, self._frame_background_color, "Background")
        else:
            self._frame_text_color = color.name()
            self._set_color_button(self._frame_text_color_button, self._frame_text_color, "Text")
        self._on_timeline_frame_edited()

    def _on_delete_timeline_frame(self) -> None:
        if self._selected_frame_id:
            self.timeline_frame_delete_requested.emit(self._selected_frame_id)

    def _clear_frame_pills(self) -> None:
        while self._frame_list_layout.count():
            item = self._frame_list_layout.takeAt(0)
            widget = item.widget()
            if widget is not None:
                widget.deleteLater()

    def set_timeline_frames(self, frames: list[TimelineFrame], selected_id: str = "") -> None:
        """Render selectable pills for inserted text/picture frames."""
        self._clear_frame_pills()
        ordered = sorted(frames or [], key=lambda item: (float(item.timestamp_ms), item.id))
        if not ordered:
            empty = QLabel("No frames yet")
            empty.setObjectName("Secondary")
            self._frame_list_layout.addWidget(empty)
            return

        for index, frame in enumerate(ordered, start=1):
            kind = "Picture" if frame.kind == "image" else "Text"
            title = frame.image_path.split("\\")[-1].split("/")[-1] if frame.kind == "image" and frame.image_path else frame.text
            title = (title or kind).strip().replace("\n", " ")
            if len(title) > 26:
                title = title[:23].rstrip() + "..."
            button = QPushButton(f"{index}. {kind}  {_fmt(float(frame.timestamp_ms))}  {title}")
            button.setObjectName("FramePill")
            button.setProperty("active", frame.id == selected_id)
            button.setFixedHeight(30)
            button.setCursor(Qt.CursorShape.PointingHandCursor)
            button.clicked.connect(lambda checked=False, fid=frame.id: self.timeline_frame_select_requested.emit(fid))
            self._frame_list_layout.addWidget(button)

    def set_selected_timeline_frame(
        self,
        frame_id: str,
        kind: str = "",
        timestamp_ms: float = 0.0,
        duration_ms: float = 0.0,
        text: str = "",
        image_path: str = "",
        background_color: str = "#111827",
        text_color: str = "#f9fafb",
        font_size: int = 54,
    ) -> None:
        """Reflect selected inserted frame state in the Frames section."""
        has_selection = bool(frame_id)
        self._selected_frame_id = frame_id
        self._selected_frame_kind = kind if has_selection else ""
        self._frame_background_color = background_color or "#111827"
        self._frame_text_color = text_color or "#f9fafb"
        self._frame_timestamp_spin.blockSignals(True)
        self._frame_duration_spin.blockSignals(True)
        self._frame_text_edit.blockSignals(True)
        self._frame_font_spin.blockSignals(True)
        self._frame_timestamp_spin.setEnabled(True)
        self._frame_duration_spin.setEnabled(has_selection)
        text_enabled = has_selection and kind == "text"
        self._frame_text_edit.setEnabled(text_enabled)
        self._frame_bg_button.setEnabled(has_selection)
        self._frame_text_color_button.setEnabled(text_enabled)
        self._frame_font_spin.setEnabled(text_enabled)
        self._btn_delete_frame.setEnabled(has_selection)
        if has_selection:
            self._frame_timestamp_spin.setValue(max(0.0, float(timestamp_ms or 0.0) / 1000.0))
            self._frame_duration_spin.setValue(max(0.25, float(duration_ms or 2500.0) / 1000.0))
            self._frame_text_edit.setPlainText(self._clean_frame_text(text or ""))
            self._normalize_frame_text_document()
            self._move_frame_text_cursor_to_end()
            self._frame_font_spin.setValue(max(12, min(int(font_size or 54), 180)))
            self._set_color_button(self._frame_bg_button, self._frame_background_color, "Background")
            self._set_color_button(self._frame_text_color_button, self._frame_text_color, "Text")
            if kind == "image":
                name = image_path.split("\\")[-1].split("/")[-1] if image_path else "picture"
                self._frame_status.setText(f"Selected picture frame: {name}")
            else:
                self._frame_status.setText("Selected text frame. Edit copy, timing, and style here.")
            self._frame_section.expand()
        else:
            self._frame_duration_spin.setValue(2.5)
            self._frame_text_edit.setPlainText("")
            self._normalize_frame_text_document()
            self._frame_font_spin.setValue(54)
            self._set_color_button(self._frame_bg_button, self._frame_background_color, "Background")
            self._set_color_button(self._frame_text_color_button, self._frame_text_color, "Text")
            self._frame_status.setText("Choose an insert time, then add a text or picture frame.")
        self._frame_timestamp_spin.blockSignals(False)
        self._frame_text_edit.blockSignals(False)
        self._frame_font_spin.blockSignals(False)
        self._frame_duration_spin.blockSignals(False)

    def set_frame_insert_timestamp(self, timestamp_ms: float, duration_ms: float = 0.0) -> None:
        """Update the insert timestamp when no frame is actively selected."""
        maximum = max(0.0, float(duration_ms or self._duration or 0.0)) / 1000.0
        self._frame_timestamp_spin.setMaximum(max(1.0, maximum))
        if getattr(self, "_selected_frame_id", ""):
            return
        self._frame_timestamp_spin.blockSignals(True)
        self._frame_timestamp_spin.setValue(max(0.0, min(float(timestamp_ms or 0.0) / 1000.0, self._frame_timestamp_spin.maximum())))
        self._frame_timestamp_spin.blockSignals(False)

    def set_range_actions_enabled(self, has_selection: bool, can_paste: bool = False) -> None:
        """Enable selected-range editing buttons from the editor window."""
        self._btn_range_cut.setEnabled(has_selection)
        self._btn_range_copy.setEnabled(has_selection)
        self._btn_range_delete.setEnabled(has_selection)
        self._btn_range_paste.setEnabled(can_paste)

    def set_edited_preview_summary(self, kept_ms: float, source_ms: float) -> None:
        """Show whether timeline playback is previewing the edited output."""
        if not hasattr(self, "_edited_preview_status"):
            return
        kept = max(0.0, float(kept_ms or 0.0))
        source = max(0.0, float(source_ms or 0.0))
        if source <= 0 or abs(kept - source) < 1.0:
            self._edited_preview_status.setText("Preview uses the full recording.")
            return
        self._edited_preview_status.setText(
            f"Edited preview: {_fmt(kept)} kept from {_fmt(source)}. Deleted gaps are skipped during playback."
        )

