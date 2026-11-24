"""Optional desktop system tray icon for monitoring and controlling the local Jamly server."""
from __future__ import annotations

import ctypes
from pathlib import Path
import sys
import threading
import webbrowser

from .runtime import share_path

_STAGES = {
    "metadata": "Checking media",
    "download": "Downloading audio",
    "separate": "Separating audio",
    "mix": "Preparing exports",
    "publish": "Saving files",
    "running": "Working",
}


def find_icon_path() -> Path | None:
    """Return the first existing jamly.ico across frozen, wheel, --user, and checkout layouts."""
    path = share_path("jamly.ico", checkout_relative=("tools", "jamly.ico"))
    return path if path is not None and path.is_file() else None


def tray_status(app) -> tuple[str, str]:
    """Return (tooltip, menu_status_line) summarizing the server's live state."""
    with app._status_lock:
        current = dict(app._current) if app._current else None
        items = app.queue._items
        pending = sum(1 for item in items if item["status"] == "pending")
        done = sum(1 for item in items if item["status"] in ("done", "skipped"))
    if current:
        if current.get("cancelling"):
            detail = "Cancelling..."
        else:
            stage = current.get("stage") or "running"
            detail = _STAGES.get(stage, "Working")
            progress = current.get("progress")
            if stage == "separate" and isinstance(progress, int):
                detail = f"{detail} ({progress}%)"
        if pending > 0:
            detail = f"{detail} · {pending} queued"
    elif pending > 0:
        detail = f"{pending} queued"
    elif done > 0:
        detail = f"Idle ({done} ready)"
    else:
        detail = "Idle"
    tooltip = f"Jamly - {detail}"[:127]
    return tooltip, f"Status: {detail}"


def _run_background(name: str, fn) -> None:
    def runner():
        try:
            fn()
        except Exception as exc:
            print(f"jamly-tray ({name}): {exc}", file=sys.stderr)

    threading.Thread(target=runner, name=f"jamly-tray-{name}", daemon=True).start()


def open_companion(app) -> None:
    launch = app.ui.launch(app.base_url)
    webbrowser.open(launch.as_uri())


def open_exports(app) -> None:
    app.show_folder(None, target="output")


class NullTray:
    """No-op tray used on unsupported platforms, headless sessions, or --no-tray."""

    def start(self) -> None:
        return None

    def stop(self) -> None:
        return None


if sys.platform == "win32":
    from ctypes import wintypes

    LRESULT = ctypes.c_ssize_t
    WNDPROC = ctypes.WINFUNCTYPE(LRESULT, wintypes.HWND, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM)

    WM_NULL = 0x0000
    WM_DESTROY = 0x0002
    WM_CLOSE = 0x0010
    WM_TIMER = 0x0113
    WM_COMMAND = 0x0111
    WM_LBUTTONUP = 0x0202
    WM_LBUTTONDBLCLK = 0x0203
    WM_RBUTTONUP = 0x0205
    WM_CONTEXTMENU = 0x007B
    WM_APP = 0x8000
    WM_TRAYICON = WM_APP + 1

    NIM_ADD = 0x00000000
    NIM_MODIFY = 0x00000001
    NIM_DELETE = 0x00000002
    NIF_MESSAGE = 0x00000001
    NIF_ICON = 0x00000002
    NIF_TIP = 0x00000004

    IMAGE_ICON = 1
    LR_LOADFROMFILE = 0x00000010
    IDI_APPLICATION = 32512
    SM_CXSMICON = 49
    SM_CYSMICON = 50

    MF_STRING = 0x00000000
    MF_GRAYED = 0x00000001
    MF_SEPARATOR = 0x00000800
    TPM_LEFTALIGN = 0x0000
    TPM_BOTTOMALIGN = 0x0020
    TPM_RIGHTBUTTON = 0x0002
    TPM_RETURNCMD = 0x0100

    ID_STATUS = 1000
    ID_OPEN = 1001
    ID_FOLDER = 1002
    ID_QUIT = 1003
    TIMER_ID = 1

    class WNDCLASSEXW(ctypes.Structure):
        _fields_ = [
            ("cbSize", wintypes.UINT),
            ("style", wintypes.UINT),
            ("lpfnWndProc", WNDPROC),
            ("cbClsExtra", ctypes.c_int),
            ("cbWndExtra", ctypes.c_int),
            ("hInstance", wintypes.HINSTANCE),
            ("hIcon", wintypes.HICON),
            ("hCursor", wintypes.HANDLE),
            ("hbrBackground", wintypes.HBRUSH),
            ("lpszMenuName", wintypes.LPCWSTR),
            ("lpszClassName", wintypes.LPCWSTR),
            ("hIconSm", wintypes.HICON),
        ]

    class NOTIFYICONDATAW(ctypes.Structure):
        _fields_ = [
            ("cbSize", wintypes.DWORD),
            ("hWnd", wintypes.HWND),
            ("uID", wintypes.UINT),
            ("uFlags", wintypes.UINT),
            ("uCallbackMessage", wintypes.UINT),
            ("hIcon", wintypes.HICON),
            ("szTip", ctypes.c_wchar * 128),
            ("dwState", wintypes.DWORD),
            ("dwStateMask", wintypes.DWORD),
            ("szInfo", ctypes.c_wchar * 256),
            ("uTimeoutOrVersion", wintypes.UINT),
            ("szInfoTitle", ctypes.c_wchar * 64),
            ("dwInfoFlags", wintypes.DWORD),
            ("guidItem", ctypes.c_byte * 16),
            ("hBalloonIcon", wintypes.HICON),
        ]

    _kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    _user32 = ctypes.WinDLL("user32", use_last_error=True)
    _shell32 = ctypes.WinDLL("shell32", use_last_error=True)

    _kernel32.GetModuleHandleW.argtypes = [wintypes.LPCWSTR]
    _kernel32.GetModuleHandleW.restype = wintypes.HMODULE

    _user32.GetShellWindow.argtypes = []
    _user32.GetShellWindow.restype = wintypes.HWND
    _user32.GetSystemMetrics.argtypes = [ctypes.c_int]
    _user32.GetSystemMetrics.restype = ctypes.c_int
    _user32.RegisterWindowMessageW.argtypes = [wintypes.LPCWSTR]
    _user32.RegisterWindowMessageW.restype = wintypes.UINT
    _user32.RegisterClassExW.argtypes = [ctypes.POINTER(WNDCLASSEXW)]
    _user32.RegisterClassExW.restype = wintypes.ATOM
    _user32.UnregisterClassW.argtypes = [wintypes.LPCWSTR, wintypes.HINSTANCE]
    _user32.UnregisterClassW.restype = wintypes.BOOL
    _user32.CreateWindowExW.argtypes = [
        wintypes.DWORD, wintypes.LPCWSTR, wintypes.LPCWSTR, wintypes.DWORD,
        ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_int,
        wintypes.HWND, wintypes.HMENU, wintypes.HINSTANCE, wintypes.LPVOID,
    ]
    _user32.CreateWindowExW.restype = wintypes.HWND
    _user32.DestroyWindow.argtypes = [wintypes.HWND]
    _user32.DestroyWindow.restype = wintypes.BOOL
    _user32.DefWindowProcW.argtypes = [wintypes.HWND, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM]
    _user32.DefWindowProcW.restype = LRESULT
    _user32.PostMessageW.argtypes = [wintypes.HWND, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM]
    _user32.PostMessageW.restype = wintypes.BOOL
    _user32.PostQuitMessage.argtypes = [ctypes.c_int]
    _user32.PostQuitMessage.restype = None
    _user32.GetMessageW.argtypes = [ctypes.POINTER(wintypes.MSG), wintypes.HWND, wintypes.UINT, wintypes.UINT]
    _user32.GetMessageW.restype = wintypes.BOOL
    _user32.TranslateMessage.argtypes = [ctypes.POINTER(wintypes.MSG)]
    _user32.TranslateMessage.restype = wintypes.BOOL
    _user32.DispatchMessageW.argtypes = [ctypes.POINTER(wintypes.MSG)]
    _user32.DispatchMessageW.restype = LRESULT
    _user32.SetTimer.argtypes = [wintypes.HWND, wintypes.WPARAM, wintypes.UINT, ctypes.c_void_p]
    _user32.SetTimer.restype = wintypes.WPARAM
    _user32.KillTimer.argtypes = [wintypes.HWND, wintypes.WPARAM]
    _user32.KillTimer.restype = wintypes.BOOL
    _user32.LoadImageW.argtypes = [
        wintypes.HINSTANCE, wintypes.LPCWSTR, wintypes.UINT, ctypes.c_int, ctypes.c_int, wintypes.UINT,
    ]
    _user32.LoadImageW.restype = wintypes.HANDLE
    _user32.LoadIconW.argtypes = [wintypes.HINSTANCE, wintypes.LPCWSTR]
    _user32.LoadIconW.restype = wintypes.HICON
    _user32.DestroyIcon.argtypes = [wintypes.HICON]
    _user32.DestroyIcon.restype = wintypes.BOOL
    _user32.CreatePopupMenu.argtypes = []
    _user32.CreatePopupMenu.restype = wintypes.HMENU
    _user32.AppendMenuW.argtypes = [wintypes.HMENU, wintypes.UINT, wintypes.WPARAM, wintypes.LPCWSTR]
    _user32.AppendMenuW.restype = wintypes.BOOL
    _user32.SetMenuDefaultItem.argtypes = [wintypes.HMENU, wintypes.UINT, wintypes.UINT]
    _user32.SetMenuDefaultItem.restype = wintypes.BOOL
    _user32.DestroyMenu.argtypes = [wintypes.HMENU]
    _user32.DestroyMenu.restype = wintypes.BOOL
    _user32.GetCursorPos.argtypes = [ctypes.POINTER(wintypes.POINT)]
    _user32.GetCursorPos.restype = wintypes.BOOL
    _user32.SetForegroundWindow.argtypes = [wintypes.HWND]
    _user32.SetForegroundWindow.restype = wintypes.BOOL
    _user32.TrackPopupMenu.argtypes = [
        wintypes.HMENU, wintypes.UINT, ctypes.c_int, ctypes.c_int,
        ctypes.c_int, wintypes.HWND, ctypes.c_void_p,
    ]
    _user32.TrackPopupMenu.restype = wintypes.BOOL

    _shell32.Shell_NotifyIconW.argtypes = [wintypes.DWORD, ctypes.POINTER(NOTIFYICONDATAW)]
    _shell32.Shell_NotifyIconW.restype = wintypes.BOOL
    _shell32.ExtractIconExW.argtypes = [
        wintypes.LPCWSTR, ctypes.c_int, ctypes.POINTER(wintypes.HICON),
        ctypes.POINTER(wintypes.HICON), wintypes.UINT,
    ]
    _shell32.ExtractIconExW.restype = wintypes.UINT

    def _load_win32_icon() -> tuple[wintypes.HICON, bool]:
        icon_path = find_icon_path()
        if icon_path is not None:
            cx = _user32.GetSystemMetrics(SM_CXSMICON) or 16
            cy = _user32.GetSystemMetrics(SM_CYSMICON) or 16
            handle = _user32.LoadImageW(None, str(icon_path), IMAGE_ICON, cx, cy, LR_LOADFROMFILE)
            if handle:
                return wintypes.HICON(handle), True
        small = wintypes.HICON()
        if _shell32.ExtractIconExW(sys.executable, 0, None, ctypes.byref(small), 1) > 0 and small:
            return small, True
        return _user32.LoadIconW(None, ctypes.cast(IDI_APPLICATION, wintypes.LPCWSTR)), False

    class WindowsTray:
        """Win32 Shell_NotifyIconW tray icon running its own hidden window message loop."""

        def __init__(self, app):
            self.app = app
            self._lock = threading.Lock()
            self._stopped = False
            self._ready = threading.Event()
            self._thread: threading.Thread | None = None
            self._hwnd: wintypes.HWND | None = None
            self._hicon: wintypes.HICON | None = None
            self._owns_icon = False
            self._last_tip = ""
            self._taskbar_created = _user32.RegisterWindowMessageW("TaskbarCreated")
            self._wndproc = WNDPROC(self._handle_message)
            self._class_name = f"JamlyTrayWindow_{id(self)}"

        def start(self) -> None:
            if not _user32.GetShellWindow():
                return
            with self._lock:
                if self._thread is not None or self._stopped:
                    return
                self._thread = threading.Thread(target=self._run, name="jamly-tray", daemon=True)
                self._thread.start()
            self._ready.wait(timeout=2)

        def stop(self) -> None:
            with self._lock:
                self._stopped = True
                hwnd = self._hwnd
                thread = self._thread
            if hwnd:
                _user32.PostMessageW(hwnd, WM_CLOSE, 0, 0)
            if thread is not None and threading.current_thread() is not thread:
                thread.join(timeout=3)

        def _notify_data(self, flags: int, tooltip: str = "") -> NOTIFYICONDATAW:
            data = NOTIFYICONDATAW()
            data.cbSize = ctypes.sizeof(NOTIFYICONDATAW)
            data.hWnd = self._hwnd
            data.uID = 1
            data.uFlags = flags
            data.uCallbackMessage = WM_TRAYICON
            if self._hicon:
                data.hIcon = self._hicon
            if tooltip:
                data.szTip = tooltip[:127]
            return data

        def _add_icon(self) -> bool:
            tooltip, _ = tray_status(self.app)
            self._last_tip = tooltip
            return bool(_shell32.Shell_NotifyIconW(NIM_ADD, ctypes.byref(
                self._notify_data(NIF_MESSAGE | NIF_ICON | NIF_TIP, tooltip)
            )))

        def _refresh_tooltip(self) -> None:
            tooltip, _ = tray_status(self.app)
            if tooltip == self._last_tip:
                return
            self._last_tip = tooltip
            _shell32.Shell_NotifyIconW(NIM_MODIFY, ctypes.byref(self._notify_data(NIF_TIP, tooltip)))

        def _show_menu(self) -> None:
            _, menu_status = tray_status(self.app)
            menu = _user32.CreatePopupMenu()
            if not menu:
                return
            try:
                _user32.AppendMenuW(menu, MF_STRING | MF_GRAYED, ID_STATUS, menu_status)
                _user32.AppendMenuW(menu, MF_SEPARATOR, 0, None)
                _user32.AppendMenuW(menu, MF_STRING, ID_OPEN, "Open Jamly")
                _user32.AppendMenuW(menu, MF_STRING, ID_FOLDER, "Open export folder")
                _user32.AppendMenuW(menu, MF_SEPARATOR, 0, None)
                _user32.AppendMenuW(menu, MF_STRING, ID_QUIT, "Quit Jamly")
                _user32.SetMenuDefaultItem(menu, ID_OPEN, 0)
                pt = wintypes.POINT()
                _user32.GetCursorPos(ctypes.byref(pt))
                _user32.SetForegroundWindow(self._hwnd)
                cmd = _user32.TrackPopupMenu(
                    menu, TPM_LEFTALIGN | TPM_BOTTOMALIGN | TPM_RIGHTBUTTON | TPM_RETURNCMD,
                    pt.x, pt.y, 0, self._hwnd, None,
                )
                _user32.PostMessageW(self._hwnd, WM_NULL, 0, 0)
            finally:
                _user32.DestroyMenu(menu)
            if cmd == ID_OPEN:
                _run_background("open", lambda: open_companion(self.app))
            elif cmd == ID_FOLDER:
                _run_background("folder", lambda: open_exports(self.app))
            elif cmd == ID_QUIT:
                _run_background("quit", self.app.stop)

        def _handle_message(self, hwnd, msg, wparam, lparam):
            if msg == self._taskbar_created and self._taskbar_created:
                self._add_icon()
                return 0
            if msg == WM_TIMER and wparam == TIMER_ID:
                self._refresh_tooltip()
                return 0
            if msg == WM_TRAYICON:
                event = lparam & 0xFFFF
                if event in (WM_LBUTTONUP, WM_LBUTTONDBLCLK):
                    _run_background("open", lambda: open_companion(self.app))
                elif event in (WM_RBUTTONUP, WM_CONTEXTMENU):
                    self._show_menu()
                return 0
            if msg == WM_CLOSE:
                _user32.DestroyWindow(hwnd)
                return 0
            if msg == WM_DESTROY:
                _user32.KillTimer(hwnd, TIMER_ID)
                _shell32.Shell_NotifyIconW(NIM_DELETE, ctypes.byref(self._notify_data(0)))
                if self._owns_icon and self._hicon:
                    _user32.DestroyIcon(self._hicon)
                self._hicon = None
                self._hwnd = None
                _user32.PostQuitMessage(0)
                return 0
            return _user32.DefWindowProcW(hwnd, msg, wparam, lparam)

        def _run(self) -> None:
            hinstance = _kernel32.GetModuleHandleW(None)
            wc = WNDCLASSEXW()
            wc.cbSize = ctypes.sizeof(WNDCLASSEXW)
            wc.lpfnWndProc = self._wndproc
            wc.hInstance = hinstance
            wc.lpszClassName = self._class_name
            atom = _user32.RegisterClassExW(ctypes.byref(wc))
            if not atom:
                self._ready.set()
                return
            try:
                hwnd = _user32.CreateWindowExW(
                    0, self._class_name, "Jamly Tray", 0,
                    0, 0, 0, 0, None, None, hinstance, None,
                )
                if not hwnd:
                    self._ready.set()
                    return
                self._hwnd = hwnd
                self._hicon, self._owns_icon = _load_win32_icon()
                if not self._add_icon() or self._stopped:
                    _user32.DestroyWindow(hwnd)
                    self._ready.set()
                    return
                _user32.SetTimer(hwnd, TIMER_ID, 1500, None)
                self._ready.set()
                msg = wintypes.MSG()
                while _user32.GetMessageW(ctypes.byref(msg), None, 0, 0) > 0:
                    _user32.TranslateMessage(ctypes.byref(msg))
                    _user32.DispatchMessageW(ctypes.byref(msg))
            finally:
                _user32.UnregisterClassW(self._class_name, hinstance)
                self._ready.set()


def create_tray(app, *, enabled: bool = True):
    """Create a desktop system tray controller when enabled and supported."""
    if not enabled or sys.platform != "win32":
        return NullTray()
    return WindowsTray(app)
