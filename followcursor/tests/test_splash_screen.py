"""Qt-level tests for the runtime startup splash screen."""

import pytest
from PySide6.QtCore import QEvent, QPointF, Qt
from PySide6.QtGui import QIcon, QMouseEvent, QPixmap
from PySide6.QtWidgets import QApplication, QWidget

from app.splash_screen import (
    SPLASH_SIZE,
    FollowCursorSplashScreen,
    create_splash_pixmap,
    finish_startup_splash,
    show_startup_splash,
)


@pytest.fixture(scope="module")
def qapp():
    """Create QApplication instance for widget tests."""
    app = QApplication.instance()
    if app is None:
        app = QApplication([])
    yield app


def _test_icon() -> QIcon:
    """Create a simple icon for splash rendering tests."""
    pixmap = QPixmap(64, 64)
    pixmap.fill(Qt.GlobalColor.white)
    return QIcon(pixmap)


class TestStartupSplashScreen:
    """Verify the startup splash renders and dismisses predictably."""

    def test_create_splash_pixmap_returns_expected_size(self, qapp) -> None:
        pixmap = create_splash_pixmap(_test_icon(), dark_mode=True, version="1.2.3")

        assert not pixmap.isNull()
        assert int(pixmap.width() / pixmap.devicePixelRatio()) == SPLASH_SIZE.width()
        assert int(pixmap.height() / pixmap.devicePixelRatio()) == SPLASH_SIZE.height()

    def test_mouse_click_does_not_dismiss_splash(self, qapp) -> None:
        splash = FollowCursorSplashScreen(_test_icon(), dark_mode=True, version="1.2.3")
        splash.show()
        qapp.processEvents()

        event = QMouseEvent(
            QEvent.Type.MouseButtonPress,
            QPointF(12.0, 12.0),
            QPointF(12.0, 12.0),
            Qt.MouseButton.LeftButton,
            Qt.MouseButton.LeftButton,
            Qt.KeyboardModifier.NoModifier,
        )
        splash.mousePressEvent(event)

        assert splash.isVisible()
        splash.close()

    def test_finish_startup_splash_hides_splash(self, qapp) -> None:
        splash = show_startup_splash(qapp, _test_icon(), "1.2.3")
        window = QWidget()
        window.show()
        qapp.processEvents()

        finish_startup_splash(splash, window)
        qapp.processEvents()

        assert not splash.isVisible()
        window.close()
