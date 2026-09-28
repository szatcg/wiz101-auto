"""Heal between fights in a dungeon by leaving and Recalling back.

Inside a dungeon there are no wisps to walk to, resting takes minutes, and a
defeat resets the whole dungeon. So when the wizard is too hurt (or low on
mana) to take the next fight, it marks the spot, goes Home, walks out to
Ravenwood and heals the usual way (wisps, resting away from enemies), then
Recalls to the mark. The game keeps a dungeon's progress for 30 minutes
after leaving.
"""

from __future__ import annotations

import time

from loguru import logger

from .upkeep import health_mana, is_free, recover

TRIP_MINUTES = 20  # the watchdog allowance for a trip; well within the game's 30 minutes


class DungeonHealer:
    def __init__(self, quester, cfg):
        self.q = quester
        self.client = quester.client
        self.cfg = cfg  # UpkeepConfig

    async def between_fights(self, zone: str) -> bool:
        """Inside a dungeon and in need of recovery: heal outside and come
        back. True if it acted this step."""
        hp, mana = await health_mana(self.client)
        if not self.cfg.needs_recovery(hp, mana) or not await is_free(self.client):
            return False
        logger.info(f"health {hp:.0%}, mana {mana:.0%} in {zone}: marking this spot and going out to heal")
        if not await self.q._mark_here("room"):
            logger.warning("could not mark the spot; healing here instead")
            return False
        from .trainer import home_to_ravenwood

        self.q.controller.allow_idle(TRIP_MINUTES * 60)
        started = time.monotonic()
        try:
            if not await home_to_ravenwood(self.q):
                logger.warning("Go Home didn't get us to Ravenwood; healing where we are")
            await recover(self.client, self.cfg, self.q.controller, self.q.go_to_zone)
            hp, mana = await health_mana(self.client)
            took = (time.monotonic() - started) / 60
            logger.info(f"healed to {hp:.0%} health, {mana:.0%} mana in {took:.0f} min; recalling back")
            if not await self.q._recall(zone, "the marked spot in the dungeon"):
                logger.warning("could not recall into the dungeon; the quest marker leads back")
        finally:
            self.q.controller.end_idle()
        return True
