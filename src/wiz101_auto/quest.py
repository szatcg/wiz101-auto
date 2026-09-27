"""Follows the in-game quest helper (the quest arrow) until stopped.

Each `step()`:
  1. reads the quest objective text and the quest marker position,
  2. travels to the marker (teleport with bounce detection, or walking),
  3. interacts with whatever is there (NPC, door, dungeon sigil, object),
  4. for "Defeat ..." objectives, pulls the nearest mob if no fight started.

Combat itself is handled concurrently by the Fighter task.
"""

from __future__ import annotations

import asyncio
import math
import time

from loguru import logger
from wizwalker import XYZ, Keycode

from . import ui
from .config import QuestConfig
from .upkeep import clear_popups, is_free, wait_for_loading, wait_until_free

INTERACT_RANGE = 750.0
BOUNCE_DISTANCE = 20.0


def distance(a: XYZ, b: XYZ) -> float:
    return math.dist((a.x, a.y, a.z), (b.x, b.y, b.z))


class Quester:
    def __init__(self, client, cfg: QuestConfig, controller, progression=None):
        self.client = client
        self.progression = progression
        self.cfg = cfg
        self.controller = controller
        self.sprinter = client  # SprintyClient (bot.new_handler)
        self._last_progress = (None, None)
        self._last_progress_time = time.monotonic()
        self.objectives_completed = 0

    async def objective(self) -> str:
        return await ui.text_at(self.client, ui.QUEST_GOAL_TEXT)

    async def _note_progress(self, objective: str, zone: str | None):
        key = (objective, zone)
        if key != self._last_progress:
            if self._last_progress[0] and objective != self._last_progress[0]:
                self.objectives_completed += 1
                logger.success(f"objective done -> now: {objective!r}")
            self._last_progress = key
            self._last_progress_time = time.monotonic()
        elif time.monotonic() - self._last_progress_time > self.cfg.stuck_minutes * 60:
            self.controller.stop(f"no quest progress for {self.cfg.stuck_minutes} min on {objective!r}")

    # --- movement ------------------------------------------------------------

    async def travel(self, target: XYZ) -> bool:
        """Get within interact range of `target`. Returns True on success."""
        start = await self.client.body.position()
        if distance(start, target) <= 5:
            return True

        if not self.cfg.teleport:
            await self.client.goto(target.x, target.y)
            return distance(await self.client.body.position(), target) < INTERACT_RANGE

        zone = await self.client.zone_name()
        await self.client.teleport(target)
        await asyncio.sleep(0.8)
        await wait_for_loading(self.client, appear_timeout=0.5)
        if await self.client.zone_name() != zone:
            return True  # walked into a zone transition; that's progress

        pos = await self.client.body.position()
        if distance(pos, start) > BOUNCE_DISTANCE:
            return True

        # The server rejected the teleport (collision). Try points around the
        # target, then fall back to walking in a straight line.
        logger.debug("teleport bounced; trying nearby points")
        for radius in (120, 250, 400):
            for i in range(8):
                ang = i * math.pi / 4
                p = XYZ(target.x + radius * math.cos(ang), target.y + radius * math.sin(ang), target.z)
                await self.client.teleport(p)
                await asyncio.sleep(0.5)
                if distance(await self.client.body.position(), start) > BOUNCE_DISTANCE:
                    return True
        logger.debug("walking toward objective")
        await self.client.goto(target.x, target.y)
        return distance(await self.client.body.position(), target) < INTERACT_RANGE

    # --- interaction ---------------------------------------------------------

    async def interact(self) -> bool:
        """Press X on whatever prompt is showing. Returns True if something happened."""
        if not await ui.is_visible(self.client, ui.NPC_RANGE):
            return False
        prompt = (await ui.text_at(self.client, ui.NPC_RANGE_TEXT)).lower()
        logger.info(f"interacting: {prompt or '(no text)'}")

        await self.client.send_key(Keycode.X, 0.1)
        await asyncio.sleep(1.0)

        if "to enter" in prompt:
            # Dungeon warning ("you can't leave once you enter...")
            for _ in range(10):
                if await ui.click(self.client, ui.MODAL_CENTER_BUTTON):
                    break
                if await self.client.is_loading():
                    break
                await asyncio.sleep(0.3)
        elif "to talk" in prompt:
            # The dialogue loop advances the conversation; wait for it to end,
            # including follow-up dialogues that open straight after.
            quiet_since = time.monotonic()
            while time.monotonic() - quiet_since < 3.0:
                await self.controller.checkpoint()
                if not await is_free(self.client):
                    quiet_since = time.monotonic()
                await asyncio.sleep(0.2)

        await wait_for_loading(self.client)
        await asyncio.sleep(0.5)

        if await ui.is_visible(self.client, ui.SPIRAL_DOOR_TELEPORT):
            # World gate: the quest destination is preselected, just go.
            while await ui.click(self.client, ui.SPIRAL_DOOR_TELEPORT):
                await asyncio.sleep(0.3)
            await wait_for_loading(self.client)

        if self.progression:
            await self.progression.handle_trainer()
        closed = await ui.close_menus(self.client)
        if closed:
            logger.debug(f"closed {closed} menu(s)")
        return True

    async def pull_mob(self):
        """For defeat objectives: teleport onto the closest mob to start a fight."""
        for _ in range(3):
            if await self.client.in_battle():
                return
            try:
                await self.sprinter.tp_to_closest_mob()
            except Exception as exc:
                logger.debug(f"no mob to pull: {exc}")
                return
            await asyncio.sleep(3.0)

    # --- main step -----------------------------------------------------------

    async def step(self):
        if not await is_free(self.client):
            return
        await clear_popups(self.client)
        if self.progression:
            await self.progression.tick()
            if not await is_free(self.client):
                return

        objective = await self.objective()
        zone = await self.client.zone_name()
        await self._note_progress(objective, zone)

        target = await self.client.quest_position.position()
        if distance(target, XYZ(0, 0, 0)) < 1:
            # No marker. Usually a zone change is in progress, or the objective
            # is a photomancy / collect task handled below.
            await asyncio.sleep(2.0)
            target = await self.client.quest_position.position()

        if self.cfg.photomancy and "photomance" in objective.lower():
            await self.client.send_key(Keycode.Z, 0.1)
            await asyncio.sleep(0.3)
            await self.client.send_key(Keycode.Z, 0.1)

        if distance(target, XYZ(0, 0, 0)) < 1:
            logger.debug(f"no quest marker for {objective!r}; waiting")
            await asyncio.sleep(2.0)
            return

        logger.info(f"[{zone}] {objective}")
        await self.controller.checkpoint()
        await self.travel(target)
        if not await wait_until_free(self.client, timeout=5):
            return  # a fight or dialogue started on arrival

        near = distance(await self.client.body.position(), target) < INTERACT_RANGE
        if near and await self.interact():
            return

        if "defeat" in objective.lower() and not await self.client.in_battle():
            await self.pull_mob()
