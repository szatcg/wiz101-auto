"""Kill switch, pause key and run limits.

Keys are polled with GetAsyncKeyState, so they work while the game window
has focus without installing a global keyboard hook.
"""

from __future__ import annotations

import asyncio
import ctypes
import sys
import time

from loguru import logger


class BotStopped(Exception):
    pass


VK = {f"F{i}": 0x6F + i for i in range(1, 13)} | {"PAUSE": 0x13, "END": 0x23, "INSERT": 0x2D}


def _key_down(vk: int) -> bool:
    if sys.platform != "win32":
        return False
    return bool(ctypes.windll.user32.GetAsyncKeyState(vk) & 0x8000)


class Controller:
    def __init__(self, stop_key: str, pause_key: str, max_hours: float, max_deaths: int):
        self.stop_vk = VK[stop_key.upper()]
        self.pause_vk = VK[pause_key.upper()]
        self.deadline = time.monotonic() + max_hours * 3600 if max_hours > 0 else None
        self.max_deaths = max_deaths
        self.deaths = 0
        self.stopped = asyncio.Event()
        self._resume = asyncio.Event()
        self._resume.set()
        self.stop_reason = ""

    def stop(self, reason: str):
        if not self.stopped.is_set():
            self.stop_reason = reason
            logger.warning(f"stopping: {reason}")
            self.stopped.set()
            self._resume.set()

    @property
    def paused(self) -> bool:
        return not self._resume.is_set()

    def record_death(self):
        self.deaths += 1
        logger.warning(f"wizard defeated ({self.deaths}/{self.max_deaths})")
        if self.max_deaths and self.deaths >= self.max_deaths:
            self.stop("too many deaths; the bot is probably out of its depth here")

    async def checkpoint(self):
        """Await between actions: blocks while paused, raises when stopped."""
        await self._resume.wait()
        if self.stopped.is_set():
            raise BotStopped(self.stop_reason)

    async def watch(self):
        was_pause_down = False
        while not self.stopped.is_set():
            if _key_down(self.stop_vk):
                self.stop("stop key pressed")
                break
            pause_down = _key_down(self.pause_vk)
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
