"""Stall watchdog: if nothing observable changes for a while, shake things loose.

"Something changed" means any of: zone, position, quest objective, health,
dialogue text, battle state/round. Intentional waits (resting, waiting for
respawns, deck rebuilds) call controller.allow_idle() so they don't count.

Escalation, one level per further stall period:
  1. close menus/popups, advance dialogue
  2. cancel the current quest step, forget per-objective assumptions, step forward
  3. switch the tracked quest
  4. walk a little in a random direction and restart the step
then back to 1. The quest-level stuck timer still stops the bot eventually.
"""

from __future__ import annotations

import asyncio
import random
import time

from loguru import logger
from wizwalker import Keycode

from . import ui
from .upkeep import clear_popups

LEVELS = 4


class Watchdog:
    def __init__(self, client, controller, quester=None, *, stall_seconds=15.0, battle_stall_seconds=120.0):
        self.client = client
        self.controller = controller
        self.quester = quester
        self.stall_seconds = stall_seconds
        self.battle_stall_seconds = battle_stall_seconds
        self.nudges = 0

    async def _fingerprint(self) -> tuple:
        c = self.client
        in_battle = await c.in_battle()
        pos = await c.body.position()
        round_no = 0
        if in_battle:
            try:
                round_no = await c.duel.round_num()
            except Exception:
                pass
        return (
            await c.zone_name(),
            round(pos.x / 25),
            round(pos.y / 25),
            await ui.text_at(c, ui.QUEST_GOAL_TEXT),
            await c.stats.current_hitpoints(),
            await ui.text_at(c, ui.DIALOG_TEXT),
            in_battle,
            round_no,
        )

    async def run(self):
        last = None
        changed = time.monotonic()
        level = 0
        while not self.controller.stopped.is_set():
            await asyncio.sleep(1.0)
            now = time.monotonic()
            if self.controller.paused or now < self.controller.idle_until:
                changed = now
                continue
            try:
                if await self.client.is_loading():
                    changed = now
                    continue
                fp = await self._fingerprint()
            except Exception:
                changed = now  # memory shifting (zone change); not a stall
                continue
            if fp != last:
                last, changed, level = fp, now, 0
                continue
            in_battle = fp[6]
            limit = self.battle_stall_seconds if in_battle else self.stall_seconds
            if now - changed < limit:
                continue
            if in_battle:
                logger.warning(f"battle hasn't progressed for {limit:.0f}s")
                changed = now
                continue
            level = level % LEVELS + 1
            self.nudges += 1
            logger.warning(f"nothing has happened for {limit:.0f}s; recovery step {level}/{LEVELS}")
            try:
                await self._nudge(level)
            except Exception as exc:
                logger.debug(f"watchdog nudge failed: {exc}")
            changed = time.monotonic()

    async def _nudge(self, level: int):
        c = self.client
        q = self.quester
        if level == 1:
            await clear_popups(c)
            closed = await ui.close_menus(c)
            if q:
                await q.services.close()
            if await ui.is_visible(c, ui.ADVANCE_DIALOG):
                await c.send_key(Keycode.SPACEBAR, 0.1)
            logger.info(f"  closed menus/popups ({closed} closed)")
        elif level == 2:
            if q:
                q.cancel_step()
                q.reset_objective_memory()
            await c.send_key(Keycode.W, 0.4)
            logger.info("  restarted the current step with a fresh start")
        elif level == 3:
            if q:
                q.cancel_step()
                async with q.lock:
                    await q.switch_quest()
        else:
            if q:
                q.cancel_step()
            await c.send_key(random.choice([Keycode.A, Keycode.D]), 0.6)
            await c.send_key(Keycode.W, 1.2)
            logger.info("  walked a few steps and restarted")
