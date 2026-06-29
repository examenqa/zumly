import sys
import argparse
import logging
from PySide6.QtWidgets import QApplication
from zumly.app.widgets.editor_window import EditorWindow
from zumly.app.theme import get_base_palette

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger(__name__)

def main():
    parser = argparse.ArgumentParser(description="Zumly Standalone Editor")
    parser.add_argument("--project", type=str, help="Path to the recording session .json file", default="")
    args = parser.parse_args()

    app = QApplication(sys.argv)
    app.setApplicationName("Zumly Editor")
    app.setPalette(get_base_palette(dark=True))
    
    # We can set an icon here if needed
    # app.setWindowIcon(QIcon("zumly/app/icons/logo.svg"))

    logger.info(f"Starting Editor with project: {args.project}")
    
    window = EditorWindow(project_path=args.project)
    window.show()

    sys.exit(app.exec())

if __name__ == "__main__":
    main()
