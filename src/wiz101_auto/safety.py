"""Kill switch, pause key and run limits.

Keys are polled with GetAsyncKeyState, so they work while the game window
has focus without installing a global keyboard hook. Hotkeys are written like
"ctrl+shift+q"; any combination of ctrl/shift/alt plus one key works, which
matters on compact keyboards without an F-row.
"""

from __future__ import annotations

import asyncio
import ctypes
import sys
import time

from loguru import logger


class BotStopped(Exception):
    pass


MODIFIERS = {"ctrl": 0x11, "control": 0x11, "shift": 0x10, "alt": 0x12}
NAMED_KEYS = {
    **{f"f{i}": 0x6F + i for i in range(1, 25)},
    "space": 0x20,
    "backspace": 0x08,
    "tab": 0x09,
    "enter": 0x0D,
    "capslock": 0x14,
    "pause": 0x13,
    "end": 0x23,
    "home": 0x24,
    "insert": 0x2D,
    "delete": 0x2E,
    "pageup": 0x21,
    "pagedown": 0x22,
    "`": 0xC0,
    "backtick": 0xC0,
    "-": 0xBD,
    "=": 0xBB,
    "[": 0xDB,
    "]": 0xDD,
    "\\": 0xDC,
    ";": 0xBA,
    "'": 0xDE,
    ",": 0xBC,
    ".": 0xBE,
    "/": 0xBF,
}


def parse_hotkey(text: str) -> tuple[int, ...]:
    """'ctrl+shift+q' -> virtual-key codes that must all be held."""
    parts = [p.strip().lower() for p in text.replace(" ", "").split("+") if p.strip()]
    if not parts:
        raise ValueError("empty hotkey")
    *mods, key = parts
    codes = []
    for m in mods:
        if m not in MODIFIERS:
            raise ValueError(f"unknown modifier {m!r} in hotkey {text!r} (use ctrl, shift, alt)")
        codes.append(MODIFIERS[m])
    if len(key) == 1 and key.isalnum():
        codes.append(ord(key.upper()))
    elif key in NAMED_KEYS:
        codes.append(NAMED_KEYS[key])
    elif key in MODIFIERS:
        raise ValueError(f"hotkey {text!r} needs a non-modifier key at the end")
    else:
        raise ValueError(f"unknown key {key!r} in hotkey {text!r}")
    return tuple(codes)


def _key_down(vk: int) -> bool:
    if sys.platform != "win32":
        return False
    return bool(ctypes.windll.user32.GetAsyncKeyState(vk) & 0x8000)


def _combo_down(codes: tuple[int, ...]) -> bool:
    return all(_key_down(c) for c in codes)


class Controller:
    def __init__(self, stop_key: str, pause_key: str, max_hours: float):
        self.stop_keys = parse_hotkey(stop_key)
        self.pause_keys = parse_hotkey(pause_key)
        self.deadline = time.monotonic() + max_hours * 3600 if max_hours > 0 else None
        self.deaths = 0
        self.last_death: tuple[float, str] | None = None  # (monotonic time, zone)
        self.stopped = asyncio.Event()
        self._resume = asyncio.Event()
        self._resume.set()
        self.stop_reason = ""
        self.idle_until = 0.0  # the stall watchdog ignores quiet periods before this

    def allow_idle(self, seconds: float):
        """Declare an intentional quiet period (resting, waiting for respawns...)."""
        self.idle_until = max(self.idle_until, time.monotonic() + seconds)

    def end_idle(self):
        self.idle_until = 0.0

    def stop(self, reason: str):
        if not self.stopped.is_set():
            self.stop_reason = reason
            logger.warning(f"stopping: {reason}")
            self.stopped.set()
            self._resume.set()

    @property
    def paused(self) -> bool:
        return not self._resume.is_set()

    def record_death(self, zone: str = ""):
        from . import lifetime
        from .questlist import world_of_zone

        lifetime.load()  # seed the totals from the log before this death is logged
        self.deaths += 1
        self.last_death = (time.monotonic(), zone)
        total = lifetime.add_death(world_of_zone(zone))
        # Deaths never stop the bot: losing fights is handled per objective
        # (setbacks puts a quest aside and the bot levels up elsewhere).
        logger.warning(f"wizard defeated ({self.deaths} this session, {total} in all)")

    async def checkpoint(self):
        """Await between actions: blocks while paused, raises when stopped."""
        await self._resume.wait()
        if self.stopped.is_set():
            raise BotStopped(self.stop_reason)

    async def watch(self):
        from .service import STOP_FILE

        was_pause_down = False
        ticks = 0
        while not self.stopped.is_set():
            ticks += 1
            if ticks % 10 == 0 and STOP_FILE.exists():
                self.stop("stop requested from the terminal")
                break
            if ticks % 10 == 0:
                from .control import PAUSE_REQUEST

                if PAUSE_REQUEST.exists():  # the control page's pause / resume button
                    PAUSE_REQUEST.unlink(missing_ok=True)
                    if self.paused:
                        logger.info("resumed (control page)")
                        self._resume.set()
                    else:
                        logger.info("paused from the control page (press again to resume)")
                        self._resume.clear()
            if _combo_down(self.stop_keys):
                # Also the terminal's stop request: the supervisor then doesn't
                # restart it (the player's: stopped means stopped until told).
                try:
                    STOP_FILE.parent.mkdir(exist_ok=True)
                    STOP_FILE.touch()
                except OSError:
                    pass
                self.stop("stop key pressed")
                break
            pause_down = _combo_down(self.pause_keys)
            if pause_down and not was_pause_down:
                if self.paused:
                    logger.info("resumed")
                    self._resume.set()
                else:
                    logger.info("paused (press again to resume)")
                    self._resume.clear()
            was_pause_down = pause_down
            if self.deadline and time.monotonic() > self.deadline:
                self.stop("max run time reached")
                break
            await asyncio.sleep(0.05)
