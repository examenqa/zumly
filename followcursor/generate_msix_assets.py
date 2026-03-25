"""Generate MSIX visual assets from the FollowCursor app icon.

Produces the PNG files required by AppxManifest.xml:
  - Square44x44Logo.png   (44×44)
  - Square150x150Logo.png (150×150)
  - Square310x310Logo.png (310×310)
  - Wide310x150Logo.png   (310×150)
  - StoreLogo.png          (50×50)
  - SplashScreen.png       (620×300)

Run from the followcursor/ directory (same level as main.py).
Output goes to msix/Assets/.
"""

import os
import sys

sys.path.insert(0, os.path.dirname(__file__))

from PySide6.QtWidgets import QApplication
from PySide6.QtGui import QImage, QPainter, QColor
from PySide6.QtCore import Qt, QRect
from app.icon import create_app_icon

ASSETS_DIR = os.path.join(os.path.dirname(__file__), "msix", "Assets")
BG_COLOR = QColor("#1b1a2e")

SIZES = {
    "Square44x44Logo.png": (44, 44),
    "Square150x150Logo.png": (150, 150),
    "Square310x310Logo.png": (310, 310),
    "Wide310x150Logo.png": (310, 150),
    "StoreLogo.png": (50, 50),
    "SplashScreen.png": (620, 300),
}


def main() -> None:
    app = QApplication.instance() or QApplication(sys.argv)
    icon = create_app_icon()
    os.makedirs(ASSETS_DIR, exist_ok=True)

    for filename, (w, h) in SIZES.items():
        img = QImage(w, h, QImage.Format.Format_ARGB32_Premultiplied)
        img.fill(BG_COLOR)
        painter = QPainter(img)
        painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
        # Centre the icon in the tile (use the smaller dimension as icon size
        # with 25 % padding so it doesn't touch the edges)
        icon_size = int(min(w, h) * 0.75)
        x = (w - icon_size) // 2
        y = (h - icon_size) // 2
        pixmap = icon.pixmap(icon_size, icon_size)
        painter.drawPixmap(x, y, pixmap)
        painter.end()
        out_path = os.path.join(ASSETS_DIR, filename)
        img.save(out_path, "PNG")
        print(f"  {filename} ({w}×{h})")

    print(f"Assets written to {ASSETS_DIR}")


if __name__ == "__main__":
    main()
