"""Startup splash screen helpers for FollowCursor."""

from __future__ import annotations

from PySide6.QtCore import QSettings, QRectF, QSize, Qt
from PySide6.QtGui import QColor, QFont, QGuiApplication, QIcon, QLinearGradient, QPainter, QPainterPath, QPen, QPixmap
from PySide6.QtWidgets import QApplication, QSplashScreen, QWidget

from . import tokens as T


SPLASH_SIZE = QSize(480, 280)


def _scaled_pixmap(size: QSize) -> QPixmap:
    """Create a pixmap sized for the current screen scale factor."""
    dpr = 1.0
    screen = QGuiApplication.primaryScreen()
    if screen is not None:
        try:
            dpr = max(1.0, float(screen.devicePixelRatio()))
        except Exception:
            dpr = 1.0

    pixmap = QPixmap(int(size.width() * dpr), int(size.height() * dpr))
    pixmap.setDevicePixelRatio(dpr)
    return pixmap


def create_splash_pixmap(icon: QIcon, dark_mode: bool, version: str) -> QPixmap:
    """Render the startup splash artwork for the active theme."""
    pixmap = _scaled_pixmap(SPLASH_SIZE)
    pixmap.fill(QColor(T.bg_canvas(dark=dark_mode)))

    panel_color = QColor(T.bg_track(dark=dark_mode))
    panel_color = panel_color.lighter(108) if dark_mode else panel_color
    panel_border = QColor(T.bg_track_border(dark=dark_mode))
    title_color = QColor(T.fg_primary(dark=dark_mode))
    body_color = QColor(T.fg_muted(dark=dark_mode))
    accent = QColor(T.BRAND if dark_mode else T.LIGHT_BRAND_BG)
    accent_alt = QColor(T.BRAND_ACTIVE if dark_mode else T.LIGHT_BRAND_BG_HOVER)
    pill_fill = QColor(T.BRAND if dark_mode else T.LIGHT_BRAND_BG)
    pill_fill.setAlpha(46 if dark_mode else 28)

    painter = QPainter(pixmap)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing)
    painter.setRenderHint(QPainter.RenderHint.TextAntialiasing)
    painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)

    card_rect = QRectF(20, 20, SPLASH_SIZE.width() - 40, SPLASH_SIZE.height() - 40)
    card_path = QPainterPath()
    card_path.addRoundedRect(card_rect, T.RADIUS_XLARGE, T.RADIUS_XLARGE)

    card_gradient = QLinearGradient(card_rect.topLeft(), card_rect.bottomRight())
    card_gradient.setColorAt(0.0, panel_color)
    card_gradient.setColorAt(1.0, QColor(T.bg_canvas(dark=dark_mode)))
    painter.fillPath(card_path, card_gradient)
    painter.strokePath(card_path, QPen(panel_border, 1))

    painter.save()
    painter.setClipPath(card_path)
    accent_bar = QRectF(card_rect.left(), card_rect.top(), card_rect.width(), 6)
    accent_gradient = QLinearGradient(accent_bar.topLeft(), accent_bar.topRight())
    accent_gradient.setColorAt(0.0, accent_alt)
    accent_gradient.setColorAt(0.5, accent)
    accent_gradient.setColorAt(1.0, accent_alt)
    painter.fillRect(accent_bar, accent_gradient)
    painter.restore()

    icon_size = QSize(84, 84)
    icon_x = int(card_rect.left()) + 26
    icon_y = int(card_rect.top()) + 34
    painter.drawPixmap(icon_x, icon_y, icon.pixmap(icon_size))

    title_font = QFont("Segoe UI Variable")
    title_font.setPixelSize(T.FONT_SIZE_TITLE_2)
    title_font.setWeight(QFont.Weight(T.FONT_WEIGHT_SEMIBOLD))
    painter.setFont(title_font)
    painter.setPen(title_color)
    painter.drawText(
        QRectF(icon_x + 108, icon_y + 2, card_rect.width() - 166, 42),
        Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter,
        "FollowCursor",
    )

    subtitle_font = QFont("Segoe UI Variable")
    subtitle_font.setPixelSize(T.FONT_SIZE_BODY_2)
    subtitle_font.setWeight(QFont.Weight(T.FONT_WEIGHT_REGULAR))
    painter.setFont(subtitle_font)
    painter.setPen(body_color)
    painter.drawText(
        QRectF(icon_x + 108, icon_y + 42, card_rect.width() - 166, 32),
        Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter,
        "Cinematic screen recording with cursor-following zoom",
    )

    helper_font = QFont("Segoe UI Variable")
    helper_font.setPixelSize(T.FONT_SIZE_BODY_1)
    helper_font.setWeight(QFont.Weight(T.FONT_WEIGHT_REGULAR))
    painter.setFont(helper_font)
    painter.drawText(
        QRectF(icon_x, icon_y + 116, card_rect.width() - 52, 40),
        Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter,
        "Preparing the recorder, editor, and tray controls…",
    )

    pill_rect = QRectF(icon_x, card_rect.bottom() - 72, 132, 34)
    painter.setPen(Qt.PenStyle.NoPen)
    painter.setBrush(pill_fill)
    painter.drawRoundedRect(pill_rect, T.RADIUS_CIRCULAR, T.RADIUS_CIRCULAR)

    pill_font = QFont("Segoe UI Variable")
    pill_font.setPixelSize(T.FONT_SIZE_CAPTION_1)
    pill_font.setWeight(QFont.Weight(T.FONT_WEIGHT_SEMIBOLD))
    painter.setFont(pill_font)
    painter.setPen(title_color)
    painter.drawText(
        pill_rect,
        Qt.AlignmentFlag.AlignCenter,
        "Starting up…",
    )

    version_font = QFont("Segoe UI Variable")
    version_font.setPixelSize(T.FONT_SIZE_CAPTION_1)
    version_font.setWeight(QFont.Weight(T.FONT_WEIGHT_MEDIUM))
    painter.setFont(version_font)
    painter.setPen(body_color)
    painter.drawText(
        QRectF(card_rect.left(), card_rect.bottom() - 70, card_rect.width() - 26, 32),
        Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter,
        f"Version {version}",
    )

    painter.end()
    return pixmap


class FollowCursorSplashScreen(QSplashScreen):
    """Frameless splash screen shown while the main window initializes."""

    def __init__(self, icon: QIcon, dark_mode: bool, version: str, parent: QWidget | None = None) -> None:
        flags = (
            Qt.WindowType.SplashScreen
            | Qt.WindowType.FramelessWindowHint
            | Qt.WindowType.WindowStaysOnTopHint
        )
        super().__init__(create_splash_pixmap(icon, dark_mode=dark_mode, version=version), flags)
        self.setParent(parent)

    def mousePressEvent(self, event) -> None:  # type: ignore[override]
        """Ignore clicks so the splash cannot be dismissed accidentally."""
        event.ignore()

    def mouseReleaseEvent(self, event) -> None:  # type: ignore[override]
        """Ignore clicks so the splash stays up until startup completes."""
        event.ignore()


def show_startup_splash(app: QApplication, icon: QIcon, version: str) -> FollowCursorSplashScreen:
    """Create, show, and flush the startup splash before heavy initialization."""
    settings = QSettings("FollowCursor", "FollowCursor")
    dark_mode = settings.value("appearance/darkMode", True, type=bool)
    splash = FollowCursorSplashScreen(icon=icon, dark_mode=dark_mode, version=version)
    splash.show()
    app.processEvents()
    return splash


def finish_startup_splash(splash: QSplashScreen | None, window: QWidget) -> None:
    """Dismiss the splash once the main window is ready."""
    if splash is None:
        return
    splash.finish(window)
