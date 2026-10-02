"""`wiz101-auto screenshot`: save the game window as a PNG (for debugging).

Uses PrintWindow, which renders the window itself, so it works regardless of
monitor layout or display scaling (a screen grab of the region comes out
cropped/zoomed on scaled monitors). Standard library only. Read-only: sends
no input to the game.
"""

from __future__ import annotations

import ctypes
import struct
import zlib
from ctypes import wintypes
from pathlib import Path

PW_RENDERFULLCONTENT = 2


class _BITMAPINFOHEADER(ctypes.Structure):
    _fields_ = [
        ("biSize", wintypes.DWORD),
        ("biWidth", wintypes.LONG),
        ("biHeight", wintypes.LONG),
        ("biPlanes", wintypes.WORD),
        ("biBitCount", wintypes.WORD),
        ("biCompression", wintypes.DWORD),
        ("biSizeImage", wintypes.DWORD),
        ("biXPelsPerMeter", wintypes.LONG),
        ("biYPelsPerMeter", wintypes.LONG),
        ("biClrUsed", wintypes.DWORD),
        ("biClrImportant", wintypes.DWORD),
    ]


def find_game_window() -> int:
    """The bot's own game window (the player may run another copy)."""
    from .gamerestart import bot_game_pid, game_windows

    if bot_game_pid():
        mine = game_windows()
        if mine:
            return mine[0]
    user32 = ctypes.windll.user32
    for title in ("Wizard101",):
        hwnd = user32.FindWindowW(None, title)
        if hwnd:
            return hwnd
    raise SystemExit("No Wizard101 window found.")


def capture(hwnd: int) -> tuple[int, int, bytes]:
    """Client area of `hwnd`; falls back to copying it off the screen when
    PrintWindow comes back blank (it does during battles)."""
    w, h, data = _capture(hwnd, use_print=True)
    if _blank(data):
        w, h, data = _capture(hwnd, use_print=False)
    return w, h, data


def _blank(bgra: bytes) -> bool:
    """One flat colour (sampled)? PrintWindow gives all white in battles."""
    sample = bgra[:: 4 * 997][:2000]
    return len(set(sample)) <= 2


def _capture(hwnd: int, use_print: bool) -> tuple[int, int, bytes]:
    """Client area of `hwnd` as (width, height, top-down BGRA bytes)."""
    # Stay DPI-unaware like the game: then the client rect matches the game's own
    # render size (a DPI-aware capture of a scaled monitor is mostly black border).
    user32, gdi32 = ctypes.windll.user32, ctypes.windll.gdi32
    rect = wintypes.RECT()
    user32.GetClientRect(hwnd, ctypes.byref(rect))
    w, h = rect.right, rect.bottom
    hdc = user32.GetDC(hwnd)
    mem = gdi32.CreateCompatibleDC(hdc)
    bmp = gdi32.CreateCompatibleBitmap(hdc, w, h)
    gdi32.SelectObject(mem, bmp)
    if use_print:
        # PW_CLIENTONLY (1) | PW_RENDERFULLCONTENT (2)
        user32.PrintWindow(hwnd, mem, 1 | PW_RENDERFULLCONTENT)
    else:
        gdi32.BitBlt(mem, 0, 0, w, h, hdc, 0, 0, 0x00CC0020)  # SRCCOPY from the window as shown
    info = _BITMAPINFOHEADER(ctypes.sizeof(_BITMAPINFOHEADER), w, -h, 1, 32, 0, 0, 0, 0, 0, 0)
    buf = ctypes.create_string_buffer(w * h * 4)
    gdi32.GetDIBits(mem, bmp, 0, h, buf, ctypes.byref(info), 0)
    gdi32.DeleteObject(bmp)
    gdi32.DeleteDC(mem)
    user32.ReleaseDC(hwnd, hdc)
    return w, h, buf.raw


def to_png(w: int, h: int, bgra: bytes) -> bytes:
    rows = bytearray()
    for y in range(h):
        row = bgra[y * w * 4 : (y + 1) * w * 4]
        rgb = bytearray(w * 3)
        rgb[0::3], rgb[1::3], rgb[2::3] = row[2::4], row[1::4], row[0::4]
        rows += b"\x00" + rgb

    def chunk(kind: bytes, data: bytes) -> bytes:
        return struct.pack(">I", len(data)) + kind + data + struct.pack(">I", zlib.crc32(kind + data))

    header = struct.pack(">IIBBBBB", w, h, 8, 2, 0, 0, 0)
    body = chunk(b"IHDR", header) + chunk(b"IDAT", zlib.compress(bytes(rows), 6)) + chunk(b"IEND", b"")
    return b"\x89PNG\r\n\x1a\n" + body


def save_screenshot(path: str | Path = "state/screenshot.png") -> Path:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    w, h, bgra = capture(find_game_window())
    path.write_bytes(to_png(w, h, bgra))
    return path
