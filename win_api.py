"""
Typsichere Windows-API-Aufrufe.

Ohne argtypes/restype behandelt ctypes jeden Rückgabewert als 32-Bit-int. Handles und
Device Contexts sind auf 64-Bit-Windows aber Zeiger und würden abgeschnitten. Deshalb
eigene DLL-Instanzen mit vollständigen Signaturen (eigene Instanzen, damit die
Einstellungen von pyautogui & Co. auf ctypes.windll unberührt bleiben).
"""

import ctypes
from ctypes import wintypes
from typing import Optional, Tuple

_user32 = ctypes.WinDLL("user32", use_last_error=True)
_dwmapi = ctypes.WinDLL("dwmapi")
_kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
_gdi32 = ctypes.WinDLL("gdi32", use_last_error=True)


def _sig(func, argtypes, restype):
    func.argtypes = argtypes
    func.restype = restype
    return func


_FindWindowW = _sig(_user32.FindWindowW, [wintypes.LPCWSTR, wintypes.LPCWSTR], wintypes.HWND)
_GetWindowRect = _sig(_user32.GetWindowRect, [wintypes.HWND, ctypes.POINTER(wintypes.RECT)], wintypes.BOOL)
_IsWindow = _sig(_user32.IsWindow, [wintypes.HWND], wintypes.BOOL)
_GetForegroundWindow = _sig(_user32.GetForegroundWindow, [], wintypes.HWND)
_DwmSetWindowAttribute = _sig(_dwmapi.DwmSetWindowAttribute,
                              [wintypes.HWND, wintypes.DWORD, ctypes.c_void_p, wintypes.DWORD], ctypes.c_long)

# DWM-Fensterattribute (ab Windows 11)
DWMWA_WINDOW_CORNER_PREFERENCE = 33
DWMWA_BORDER_COLOR = 34
DWMWCP_ROUND = 2
_SetForegroundWindow = _sig(_user32.SetForegroundWindow, [wintypes.HWND], wintypes.BOOL)
_GetCursorPos = _sig(_user32.GetCursorPos, [ctypes.POINTER(wintypes.POINT)], wintypes.BOOL)
_SetCursorPos = _sig(_user32.SetCursorPos, [ctypes.c_int, ctypes.c_int], wintypes.BOOL)
_mouse_event = _sig(_user32.mouse_event,
                    [wintypes.DWORD, wintypes.DWORD, wintypes.DWORD, wintypes.DWORD, ctypes.c_size_t], None)
_GetSystemMetrics = _sig(_user32.GetSystemMetrics, [ctypes.c_int], ctypes.c_int)
_GetDC = _sig(_user32.GetDC, [wintypes.HWND], wintypes.HDC)
_ReleaseDC = _sig(_user32.ReleaseDC, [wintypes.HWND, wintypes.HDC], ctypes.c_int)
_GetPixel = _sig(_gdi32.GetPixel, [wintypes.HDC, ctypes.c_int, ctypes.c_int], wintypes.DWORD)
_OpenProcess = _sig(_kernel32.OpenProcess, [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD], wintypes.HANDLE)
_CloseHandle = _sig(_kernel32.CloseHandle, [wintypes.HANDLE], wintypes.BOOL)
_QueryFullProcessImageNameW = _sig(
    _kernel32.QueryFullProcessImageNameW,
    [wintypes.HANDLE, wintypes.DWORD, wintypes.LPWSTR, ctypes.POINTER(wintypes.DWORD)], wintypes.BOOL)

# mouse_event-Flags
MOUSE_LEFT_DOWN, MOUSE_LEFT_UP = 0x0002, 0x0004
MOUSE_RIGHT_DOWN, MOUSE_RIGHT_UP = 0x0008, 0x0010
_MOUSE_WHEEL = 0x0800
WHEEL_DELTA = 120  # Eine Raste des Mausrads

_PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
_CLR_INVALID = 0xFFFFFFFF


def find_window(title: str) -> Optional[int]:
    return _FindWindowW(None, title) or None


def get_window_rect(hwnd: int) -> Optional[Tuple[int, int, int, int]]:
    rect = wintypes.RECT()
    if not _GetWindowRect(hwnd, ctypes.byref(rect)):
        return None
    return rect.left, rect.top, rect.right, rect.bottom


def set_foreground_window(hwnd: int) -> bool:
    return bool(_SetForegroundWindow(hwnd))


def get_cursor_pos() -> Tuple[int, int]:
    point = wintypes.POINT()
    _GetCursorPos(ctypes.byref(point))
    return point.x, point.y


def set_cursor_pos(x: int, y: int) -> None:
    _SetCursorPos(int(x), int(y))


def mouse_event(flags: int) -> None:
    _mouse_event(flags, 0, 0, 0, 0)


def mouse_wheel(delta: int) -> None:
    """Mausrad drehen: positiv = nach oben, negativ = nach unten (WHEEL_DELTA pro Raste)."""
    _mouse_event(_MOUSE_WHEEL, 0, 0, delta & 0xFFFFFFFF, 0)  # DWORD: negative Werte als Zweierkomplement


def is_window(hwnd: int) -> bool:
    return bool(_IsWindow(hwnd))


def get_foreground_window() -> Optional[int]:
    return _GetForegroundWindow() or None


def set_dwm_attribute(hwnd: int, attribute: int, value: int) -> bool:
    """Setzt ein DWM-Fensterattribut (DWORD). False, wenn Windows es nicht kennt (z.B. Windows 10)."""
    data = wintypes.DWORD(value)
    return _DwmSetWindowAttribute(hwnd, attribute, ctypes.byref(data), ctypes.sizeof(data)) == 0


def virtual_screen_rect() -> Tuple[int, int, int, int]:
    """(x, y, Breite, Höhe) des gesamten Desktops über alle Monitore."""
    return (_GetSystemMetrics(76), _GetSystemMetrics(77),   # SM_XVIRTUALSCREEN, SM_YVIRTUALSCREEN
            _GetSystemMetrics(78), _GetSystemMetrics(79))   # SM_CXVIRTUALSCREEN, SM_CYVIRTUALSCREEN


def get_pixel(x: int, y: int) -> Optional[Tuple[int, int, int]]:
    """RGB-Farbe eines Bildschirmpixels, None bei Fehler."""
    hdc = _GetDC(None)
    if not hdc:
        return None
    try:
        color = _GetPixel(hdc, int(x), int(y))
    finally:
        _ReleaseDC(None, hdc)
    if color == _CLR_INVALID:
        return None
    return color & 0xFF, (color >> 8) & 0xFF, (color >> 16) & 0xFF


def process_image_name(pid: int) -> Optional[str]:
    """Vollständiger Pfad der .exe eines Prozesses, None falls nicht abfragbar."""
    handle = _OpenProcess(_PROCESS_QUERY_LIMITED_INFORMATION, False, pid)
    if not handle:
        return None
    try:
        buffer = ctypes.create_unicode_buffer(260)
        size = wintypes.DWORD(260)
        if _QueryFullProcessImageNameW(handle, 0, buffer, ctypes.byref(size)):
            return buffer.value
        return None
    finally:
        _CloseHandle(handle)  # genau einmal schließen
