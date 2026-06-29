import ctypes
from ctypes import wintypes
import threading

user32 = ctypes.windll.user32
gdi32 = ctypes.windll.gdi32
kernel32 = ctypes.windll.kernel32

WNDPROCTYPE = ctypes.WINFUNCTYPE(wintypes.LPARAM, wintypes.HWND, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM)

WS_EX_LAYERED = 0x00080000
WS_EX_TRANSPARENT = 0x00000020
WS_EX_TOPMOST = 0x00000008
WS_EX_TOOLWINDOW = 0x00000080
WS_POPUP = 0x80000000

LWA_COLORKEY = 1
WDA_EXCLUDEFROMCAPTURE = 0x00000011

WM_PAINT = 0x000F
WM_DESTROY = 0x0002
WM_QUIT = 0x0012
TRANSPARENT = 1
DT_LEFT = 0x00000000
DT_VCENTER = 0x00000004
DT_SINGLELINE = 0x00000020
DT_END_ELLIPSIS = 0x00008000

class WNDCLASSEX(ctypes.Structure):
    _fields_ = [("cbSize", wintypes.UINT),
                ("style", wintypes.UINT),
                ("lpfnWndProc", WNDPROCTYPE),
                ("cbClsExtra", ctypes.c_int),
                ("cbWndExtra", ctypes.c_int),
                ("hInstance", wintypes.HINSTANCE),
                ("hIcon", wintypes.HICON),
                ("hCursor", wintypes.HANDLE),
                ("hbrBackground", wintypes.HBRUSH),
                ("lpszMenuName", wintypes.LPCWSTR),
                ("lpszClassName", wintypes.LPCWSTR),
                ("hIconSm", wintypes.HICON)]

class PAINTSTRUCT(ctypes.Structure):
    _fields_ = [("hdc", wintypes.HDC),
                ("fErase", wintypes.BOOL),
                ("rcPaint", wintypes.RECT),
                ("fRestore", wintypes.BOOL),
                ("fIncUpdate", wintypes.BOOL),
                ("rgbReserved", ctypes.c_byte * 32)]

# Define argtypes for safety on 64-bit platforms
user32.CreateWindowExW.argtypes = [
    wintypes.DWORD, wintypes.LPCWSTR, wintypes.LPCWSTR,
    wintypes.DWORD, ctypes.c_int, ctypes.c_int,
    ctypes.c_int, ctypes.c_int, wintypes.HWND,
    wintypes.HMENU, wintypes.HINSTANCE, wintypes.LPVOID
]
user32.CreateWindowExW.restype = wintypes.HWND

user32.RegisterClassExW.argtypes = [ctypes.POINTER(WNDCLASSEX)]
user32.RegisterClassExW.restype = wintypes.ATOM

user32.DefWindowProcW.argtypes = [wintypes.HWND, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM]
user32.DefWindowProcW.restype = wintypes.LPARAM

user32.PostThreadMessageW.argtypes = [wintypes.DWORD, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM]
user32.PostThreadMessageW.restype = wintypes.BOOL


# Define GDI argtypes
gdi32.CreateFontW.argtypes = [
    ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_int,
    wintypes.DWORD, wintypes.DWORD, wintypes.DWORD, wintypes.DWORD,
    wintypes.DWORD, wintypes.DWORD, wintypes.DWORD, wintypes.DWORD,
    wintypes.LPCWSTR
]
gdi32.CreateFontW.restype = wintypes.HANDLE
gdi32.RoundRect.argtypes = [wintypes.HDC, ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_int]
gdi32.Ellipse.argtypes = [wintypes.HDC, ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_int]
gdi32.SetTextColor.argtypes = [wintypes.HDC, wintypes.COLORREF]
gdi32.SetBkMode.argtypes = [wintypes.HDC, ctypes.c_int]
user32.DrawTextW.argtypes = [
    wintypes.HDC,
    wintypes.LPCWSTR,
    ctypes.c_int,
    ctypes.POINTER(wintypes.RECT),
    wintypes.UINT,
]
user32.DrawTextW.restype = ctypes.c_int


class RecordingOverlay:
    """A pure Win32 frameless, transparent window excluded from capture."""
    
    def __init__(self, monitor_rect: dict):
        self.monitor_rect = monitor_rect
        self.hwnd = None
        self.thread_id = None
        self._thread = threading.Thread(target=self._run, daemon=True)
        # Prevent garbage collection of the wndproc callback
        self._wndproc_c = WNDPROCTYPE(self._wndproc)

    def start(self):
        self._thread.start()

    def stop(self):
        if self.thread_id:
            user32.PostThreadMessageW(self.thread_id, WM_QUIT, 0, 0)
            self._thread.join(timeout=2.0)

    def _wndproc(self, hwnd, msg, wparam, lparam):
        if msg == WM_PAINT:
            ps = PAINTSTRUCT()
            hdc = user32.BeginPaint(hwnd, ctypes.byref(ps))

            # Remove black borders globally
            hpen_null = gdi32.GetStockObject(5) # NULL_PEN
            old_pen = gdi32.SelectObject(hdc, hpen_null)

            # Draw dark gray pill background
            hbrush_bg = gdi32.CreateSolidBrush(0x001C1C1C) # RGB(28,28,28) -> BGR(0x1c1c1c)
            old_brush = gdi32.SelectObject(hdc, hbrush_bg)
            gdi32.RoundRect(hdc, 0, 0, 150, 32, 16, 16)
            gdi32.SelectObject(hdc, old_brush)
            gdi32.DeleteObject(hbrush_bg)

            # Draw crimson red circle indicator
            hbrush_red = gdi32.CreateSolidBrush(0x002311E8) # RGB(232,17,35) -> BGR(0x2311e8)
            old_brush = gdi32.SelectObject(hdc, hbrush_red)
            gdi32.Ellipse(hdc, 12, 8, 26, 22)
            gdi32.SelectObject(hdc, old_brush)
            gdi32.DeleteObject(hbrush_red)

            # Draw high-contrast recording text.
            text = "Recording"
            gdi32.SetBkMode(hdc, TRANSPARENT)
            
            # Create Segoe UI 10pt font (-13 or -14 pixels for 10pt)
            # FW_NORMAL = 400, DEFAULT_CHARSET = 1, CLEARTYPE_QUALITY = 5
            hfont = gdi32.CreateFontW(-14, 0, 0, 0, 400, 0, 0, 0, 1, 0, 0, 5, 0, "Segoe UI")
            old_font = gdi32.SelectObject(hdc, hfont)

            flags = DT_LEFT | DT_VCENTER | DT_SINGLELINE | DT_END_ELLIPSIS
            shadow_rect = wintypes.RECT(35, 1, 148, 32)
            gdi32.SetTextColor(hdc, 0x00000000)
            user32.DrawTextW(hdc, text, -1, ctypes.byref(shadow_rect), flags)

            rect = wintypes.RECT(34, 0, 148, 32)
            gdi32.SetTextColor(hdc, 0x00FFFFFF)
            user32.DrawTextW(hdc, text, -1, ctypes.byref(rect), flags)

            gdi32.SelectObject(hdc, old_font)
            gdi32.DeleteObject(hfont)
            gdi32.SelectObject(hdc, old_pen)

            user32.EndPaint(hwnd, ctypes.byref(ps))
            return 0
        elif msg == WM_DESTROY:
            user32.PostQuitMessage(0)
            return 0
        
        return user32.DefWindowProcW(hwnd, msg, wparam, lparam)

    def _run(self):
        self.thread_id = kernel32.GetCurrentThreadId()

        class_name = "ZumlyRecordingOverlay"
        wndclass = WNDCLASSEX()
        wndclass.cbSize = ctypes.sizeof(WNDCLASSEX)
        wndclass.lpfnWndProc = self._wndproc_c
        wndclass.hInstance = kernel32.GetModuleHandleW(None)
        wndclass.lpszClassName = class_name
        
        # Transparent colorkey background (Magenta)
        magenta = 0x00FF00FF
        hbrush = gdi32.CreateSolidBrush(magenta)
        wndclass.hbrBackground = hbrush

        user32.RegisterClassExW(ctypes.byref(wndclass))

        # Position at the top-center of the recording monitor
        width = 150
        height = 32
        x = self.monitor_rect.get("left", 0) + (self.monitor_rect.get("width", 1920) - width) // 2
        y = self.monitor_rect.get("top", 0) + 20

        self.hwnd = user32.CreateWindowExW(
            WS_EX_LAYERED | WS_EX_TRANSPARENT | WS_EX_TOPMOST | WS_EX_TOOLWINDOW,
            class_name,
            "ZumlyOverlay",
            WS_POPUP,
            x, y, width, height,
            0, 0, wndclass.hInstance, 0
        )

        user32.SetLayeredWindowAttributes(self.hwnd, magenta, 0, LWA_COLORKEY)
        user32.SetWindowDisplayAffinity(self.hwnd, WDA_EXCLUDEFROMCAPTURE)

        user32.ShowWindow(self.hwnd, 5) # SW_SHOW
        user32.UpdateWindow(self.hwnd)

        msg = wintypes.MSG()
        while user32.GetMessageW(ctypes.byref(msg), 0, 0, 0) > 0:
            user32.TranslateMessage(ctypes.byref(msg))
            user32.DispatchMessageW(ctypes.byref(msg))

        if self.hwnd:
            user32.DestroyWindow(self.hwnd)
            self.hwnd = None
