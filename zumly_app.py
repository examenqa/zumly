import sys
import os

if getattr(sys, 'frozen', False):
    # Pyinstaller will resolve `main` and `app` as top-level modules, so no sys.path changes needed.
    pass
else:
    # Running from source. Add 'zumly' to sys.path so we can import 'main' and 'app' directly.
    sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "zumly"))

def main():
    """Unified entry point for Zumly."""
    if "--headless-engine" in sys.argv:
        sys.argv.remove("--headless-engine")
        from main import main as headless_main
        headless_main()
    else:
        from tray_app import main as tray_main
        tray_main()

if __name__ == "__main__":
    main()
