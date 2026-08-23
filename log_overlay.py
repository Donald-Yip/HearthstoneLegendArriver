# -*- coding: utf-8 -*-
"""Right-bottom live log overlay (topmost translucent window).

start() launches a background thread that shows a small always-on-top
window at the bottom-right, streaming the latest automation log lines.
Lines that mark "own turn starts" are highlighted green.

Defensive: any window/render failure disables the overlay; it never
raises into the caller.
"""
from __future__ import annotations

import ctypes
import threading
import time
from collections import deque
from ctypes import wintypes

import cv2
import numpy as np
from PIL import Image, ImageDraw, ImageFont

USER32 = ctypes.windll.user32
GDI32 = ctypes.windll.gdi32
KERNEL32 = ctypes.windll.kernel32

WS_POPUP = 0x80000000
WS_VISIBLE = 0x10000000
WS_EX_LAYERED = 0x00080000
WS_EX_TOPMOST = 0x00000008
WS_EX_TOOLWINDOW = 0x00000080
WS_EX_NOACTIVATE = 0x08000000
ULW_ALPHA = 0x00000002
CLASS_NAME = "HSLegendArriverLogOverlay"

PANEL_W = 460
PANEL_H = 342
MAX_LINES = 22
REFRESH = 0.35
FONT_PATH = r"C:\Windows\Fonts\msyh.ttc"

GREEN = (96, 226, 96)
WHITE = (240, 240, 240)
DIM = (168, 172, 180)
PANEL_BG = (24, 28, 34)
BORDER = (118, 124, 132)


class _WNDCLASSW(ctypes.Structure):
    _fields_ = [("style", wintypes.UINT), ("lpfnWndProc", ctypes.c_void_p),
                ("cbClsExtra", ctypes.c_int), ("cbWndExtra", ctypes.c_int),
                ("hInstance", wintypes.HINSTANCE), ("hIcon", wintypes.HICON),
                ("hCursor", wintypes.HANDLE), ("hbrBackground", wintypes.HANDLE),
                ("lpszMenuName", wintypes.LPCWSTR),
                ("lpszClassName", wintypes.LPCWSTR)]


class _BLENDFUNCTION(ctypes.Structure):
    _fields_ = [("BlendOp", ctypes.c_ubyte), ("BlendFlags", ctypes.c_ubyte),
                ("SourceConstantAlpha", ctypes.c_ubyte),
                ("AlphaFormat", ctypes.c_ubyte)]


class _BITMAPINFOHEADER(ctypes.Structure):
    _fields_ = [("biSize", wintypes.DWORD), ("biWidth", wintypes.LONG),
                ("biHeight", wintypes.LONG), ("biPlanes", wintypes.WORD),
                ("biBitCount", wintypes.WORD), ("biCompression", wintypes.DWORD),
                ("biSizeImage", wintypes.DWORD), ("biXPelsPerMeter", wintypes.LONG),
                ("biYPelsPerMeter", wintypes.LONG), ("biClrUsed", wintypes.DWORD),
                ("biClrImportant", wintypes.DWORD)]


class _BITMAPINFO(ctypes.Structure):
    _fields_ = [("bmiHeader", _BITMAPINFOHEADER),
                ("bmiColors", wintypes.DWORD * 3)]


_LOCK = threading.Lock()
_LINES: deque = deque(maxlen=MAX_LINES)
_STARTED = [False]


def _turn_start(line: str) -> bool:
    return ("回合" in line and "延时" in line) or ("轮到己方" in line)


def push(line: str, _level: str = "INFO") -> None:
    if not _STARTED[0]:
        return
    line = str(line).rstrip()
    if not line.strip():
        return
    with _LOCK:
        _LINES.append((line, _turn_start(line)))


def _font(size: int):
    for path in (r"C:\Windows\Fonts\msyh.ttc", r"C:\Windows\Fonts\msyh.ttf",
                 r"C:\Windows\Fonts\simhei.ttf", r"C:\Windows\Fonts\simsun.ttc"):
        try:
            return ImageFont.truetype(path, size)
        except OSError:
            continue
    return None


def _draw_text(img, text, font, xy, color):
    """Draw text on a 3-channel BGR image."""
    if not text or font is None:
        return
    h, w = img.shape[:2]
    pil = Image.fromarray(cv2.cvtColor(img, cv2.COLOR_BGR2RGB))
    ImageDraw.Draw(pil).text(xy, text, fill=(color[2], color[1], color[0]),
                             font=font)
    out = cv2.cvtColor(np.asarray(pil), cv2.COLOR_RGB2BGR)
    img[:h, :w] = out[:h, :w]


def _to_bgra(img3):
    out = cv2.cvtColor(img3, cv2.COLOR_BGR2BGRA)
    out[:, :, 3] = 215
    return out


def _render():
    with _LOCK:
        lines = list(_LINES)[-MAX_LINES:]
    img = np.full((PANEL_H, PANEL_W, 3), PANEL_BG, dtype=np.uint8)
    font = _font(13)
    if font is None:
        return _to_bgra(img)
    _draw_text(img, "自动化日志  (回合开始=绿)", _font(13), (8, 4), WHITE)
    y = 26
    for text, turn in lines[-MAX_LINES:]:
        color = GREEN if turn else (
            WHITE if text.startswith(("[推荐]", "[执行]")) else DIM)
        _draw_text(img, text[:40], font, (8, y), color)
        y += 14
        if y > PANEL_H - 12:
            break
    img[0, :, :] = BORDER
    img[-1, :, :] = BORDER
    return _to_bgra(img)


def start() -> None:
    if _STARTED[0]:
        return
    _STARTED[0] = True
    threading.Thread(target=_run, name="hs-log-overlay", daemon=True).start()


def _create_window():
    try:
        USER32.SetProcessDpiAwareness(2)
    except Exception:
        pass
    inst = KERNEL32.GetModuleHandleW(None)
    wc = _WNDCLASSW()
    wc.lpfnWndProc = ctypes.cast(_WNDPROC_IMPL, ctypes.c_void_p).value
    wc.hInstance = inst
    wc.lpszClassName = CLASS_NAME
    USER32.RegisterClassW(ctypes.byref(wc))
    sw = USER32.GetSystemMetrics(0)
    sh = USER32.GetSystemMetrics(1)
    x = sw - PANEL_W - 12
    y = sh - PANEL_H - 12
    return USER32.CreateWindowExW(
        WS_EX_LAYERED | WS_EX_TOPMOST | WS_EX_TOOLWINDOW | WS_EX_NOACTIVATE,
        CLASS_NAME, "HSLegendArriver 日志", WS_POPUP | WS_VISIBLE,
        x, y, PANEL_W, PANEL_H, None, None, inst, None)


def _run() -> None:
    try:
        hwnd = _create_window()
        if not hwnd:
            print("[overlay] 日志浮窗创建失败(hwnd=0)")
            _STARTED[0] = False
            return
        hdc = USER32.GetDC(None)
        mem_dc = GDI32.CreateCompatibleDC(hdc)
        bmi = _BITMAPINFO()
        bmi.bmiHeader.biSize = ctypes.sizeof(_BITMAPINFOHEADER)
        bmi.bmiHeader.biWidth = PANEL_W
        bmi.bmiHeader.biHeight = -PANEL_H
        bmi.bmiHeader.biPlanes = 1
        bmi.bmiHeader.biBitCount = 32
        bmi.bmiHeader.biCompression = 0
        bits = ctypes.c_void_p()
        dib = GDI32.CreateDIBSection(hdc, ctypes.byref(bmi), 0,
                                     ctypes.byref(bits), None, 0)
        GDI32.SelectObject(mem_dc, dib)
        USER32.ReleaseDC(None, hdc)
        blend = _BLENDFUNCTION(0, 0, 255, 1)
        pt_dst = wintypes.POINT(0, 0)
        size = wintypes.SIZE(PANEL_W, PANEL_H)
        pt_src = wintypes.POINT(0, 0)
        while True:
            layer = _render()
            if layer is not None and bits.value:
                ctypes.memmove(bits.value, layer.tobytes(), layer.nbytes)
                USER32.UpdateLayeredWindow(
                    hwnd, None, ctypes.byref(pt_dst), ctypes.byref(size),
                    mem_dc, ctypes.byref(pt_src), 0, ctypes.byref(blend),
                    ULW_ALPHA)
            time.sleep(REFRESH)
    except Exception as exc:
        print(f"[overlay] 日志浮窗禁用: {type(exc).__name__}: {exc}")
        _STARTED[0] = False


def _passive_wndproc(hwnd, msg, wparam, lparam):
    return USER32.DefWindowProcW(hwnd, msg, wparam, lparam)


_WNDPROC_IMPL = ctypes.WINFUNCTYPE(
    ctypes.c_ssize_t, wintypes.HWND, wintypes.UINT,
    wintypes.WPARAM, wintypes.LPARAM)(_passive_wndproc)

# --- argtypes/restype so 64-bit handles/params are not truncated ---
USER32.RegisterClassW.argtypes = [ctypes.c_void_p]
USER32.RegisterClassW.restype = ctypes.c_ushort
USER32.CreateWindowExW.argtypes = [wintypes.DWORD, wintypes.LPCWSTR,
                                   wintypes.LPCWSTR, wintypes.DWORD,
                                   ctypes.c_int, ctypes.c_int, ctypes.c_int,
                                   ctypes.c_int, wintypes.HWND, wintypes.HMENU,
                                   wintypes.HINSTANCE, wintypes.LPVOID]
USER32.CreateWindowExW.restype = wintypes.HWND
USER32.DefWindowProcW.argtypes = [wintypes.HWND, wintypes.UINT,
                                  wintypes.WPARAM, wintypes.LPARAM]
USER32.DefWindowProcW.restype = ctypes.c_ssize_t
USER32.UpdateLayeredWindow.argtypes = [wintypes.HWND, wintypes.HDC,
                                       ctypes.c_void_p, ctypes.c_void_p,
                                       wintypes.HDC, ctypes.c_void_p,
                                       wintypes.DWORD, ctypes.c_void_p,
                                       wintypes.DWORD]
USER32.UpdateLayeredWindow.restype = wintypes.BOOL
GDI32.CreateDIBSection.argtypes = [wintypes.HDC, ctypes.c_void_p, wintypes.UINT,
                                   ctypes.c_void_p, wintypes.HANDLE,
                                   wintypes.DWORD]
GDI32.CreateDIBSection.restype = wintypes.HBITMAP
GDI32.CreateCompatibleDC.argtypes = [wintypes.HDC]
GDI32.CreateCompatibleDC.restype = wintypes.HDC
GDI32.SelectObject.argtypes = [wintypes.HDC, wintypes.HANDLE]
GDI32.DeleteDC.argtypes = [wintypes.HDC]
GDI32.DeleteObject.argtypes = [wintypes.HANDLE]
KERNEL32.GetModuleHandleW.argtypes = [wintypes.LPCWSTR]
KERNEL32.GetModuleHandleW.restype = wintypes.HINSTANCE
