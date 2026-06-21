"""Mouse position tracker — polls cursor position at 60 Hz via Win32.

Uses ``GetCursorPos`` for **physical pixel** coordinates (not DPI-scaled)
so they match the capture APIs (mss, WGC, PrintWindow).  Runs on a
native background thread.
"""

import sys
import time
import threading
import logging
from typing import List

from .models import MousePosition

logger = logging.getLogger(__name__)

# Use Win32 GetCursorPos for physical pixel coordinates.
if sys.platform == "win32":
    import ctypes
    import ctypes.wintypes as wintypes
    try:
        _winmm = ctypes.windll.winmm
    except AttributeError:
        _winmm = None
else:
    _winmm = None


def _get_physical_cursor_pos() -> tuple[int, int]:
    """Return the cursor position in physical screen pixels via Win32."""
    if sys.platform != "win32":
        return 0, 0
    pt = wintypes.POINT()
    ctypes.windll.user32.GetCursorPos(ctypes.byref(pt))
    return pt.x, pt.y


class MouseTracker:
    """Thread-based cursor poller that records :class:`MousePosition` samples.

    The polling interval defaults to 16 ms (~60 Hz).  All timestamps
    are relative to a shared epoch so they align with keyboard and
    click trackers.
    """

    def __init__(self, interval_ms: int = 16) -> None:
        self._interval_sec = interval_ms / 1000.0
        self._start_time: float = 0.0
        self._positions: List[MousePosition] = []
        self._thread: threading.Thread | None = None
        self._stop_event = threading.Event()
        self._lock = threading.Lock()

    def start(self, start_ms: float = 0.0) -> None:
        """Begin polling cursor position.

        *start_ms* — shared epoch (``time.time() * 1000``) so all trackers
        use the same time base.
        """
        self._start_time = start_ms if start_ms > 0 else time.time() * 1000
        with self._lock:
            self._positions.clear()
        self._stop_event.clear()
        self._thread = threading.Thread(target=self._run_loop, daemon=True)
        self._thread.start()

    def stop(self) -> List[MousePosition]:
        """Stop polling and return the collected position samples."""
        self._stop_event.set()
        if self._thread is not None:
            self._thread.join(timeout=2.0)
            if self._thread.is_alive():
                logger.warning("Mouse tracker thread did not stop within timeout")
            self._thread = None
        
        with self._lock:
            return list(self._positions)

    def _run_loop(self) -> None:
        """Polling loop running in background thread."""
        if _winmm:
            _winmm.timeBeginPeriod(1)
        try:
            while not self._stop_event.is_set():
                t0 = time.perf_counter()
                
                px, py = _get_physical_cursor_pos()
                mp = MousePosition(
                    x=px,
                    y=py,
                    timestamp=time.time() * 1000 - self._start_time,
                )
                with self._lock:
                    self._positions.append(mp)
                
                elapsed = time.perf_counter() - t0
                sleep_time = max(0.0, self._interval_sec - elapsed)
                if sleep_time > 0:
                    time.sleep(sleep_time)
        finally:
            if _winmm:
                _winmm.timeEndPeriod(1)
