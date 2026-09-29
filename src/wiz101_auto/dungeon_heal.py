"""Heal trips: mark the spot, teleport to the world's hub, heal nearby, Recall back.

Walking out through several gates to find wisps (and back) costs minutes.
When the wizard needs health or mana and will come back here (a dungeon
between fights, or a "Defeat X" objective in this zone), it marks the spot,
presses the compass's hub button (the Oasis in Krokotopia), heals the usual
way from there (wisps on a nearby street, resting away from enemies), and
Recalls to the mark. The game keeps a dungeon's progress for 30 minutes
after leaving.
"""

from __future__ import annotations

import asyncio
import time

from loguru import logger

from . import ui
from .marks import RETURN_KINDS
from .upkeep import health_mana, is_free, recover, wait_for_loading

DUNGEON_MANA_TRIP = 0.3  # inside a dungeon, leave to refill mana only below this
DEFEAT_SETTLE_SECONDS = 5.0  # after a fight, before deciding on a heal trip
TRIP_MINUTES = 20  # the watchdog allowance for a trip; well within the game's 30 minutes
# The compass's teleport buttons: "GoHomeButton" goes to the current world's
# hub (the Oasis in Krokotopia); "GotoDormButton" goes to the dorm.
HUB_BUTTON = "GoHomeButton"


async def go_to_hub(client) -> bool:
    """Teleport to the current world's hub. True if the zone changed."""
    before = await client.zone_name()
    if not await ui.click_named(client, HUB_BUTTON):
        logger.warning("no hub button to click")
        return False
    await asyncio.sleep(1.0)
    await ui.confirm_modal(client)
    await wait_for_loading(client, appear_timeout=6.0)
    return await client.zone_name() != before


class DungeonHealer:
    def __init__(self, quester, cfg):
        self.q = quester
        self.client = quester.client
        self.cfg = cfg  # UpkeepConfig
        self._busy = False

    @property
    def busy(self) -> bool:
        return self._busy

    async def between_fights(self, zone: str) -> bool:
        """Inside a dungeon and in need of recovery: heal outside and come
        back. True if it acted this step."""
        fighter = self.q.fighter
        if fighter and time.monotonic() - fighter.combat_ended_at < DEFEAT_SETTLE_SECONDS:
            return False  # a lost fight registers as a defeat a moment after it ends
        hp, mana = await health_mana(self.client)
        # Leaving a dungeon risks its progress: only for health, or mana nearly
        # gone (it went out at 62% mana in Katzenstein's Lab).
        low = hp < self.cfg.min_health_to_fight or mana < DUNGEON_MANA_TRIP
        if not low or not await is_free(self.client):
            return False
        m = self.q._mark
        if m and m.kind == "fight" and m.zone == zone:
            # Marked beside the boss: heal here (wisps, rest), don't leave.
            await recover(self.client, self.cfg, self.q.controller)
            return True
        return await self.trip(zone, f"health {hp:.0%}, mana {mana:.0%} in the dungeon")

    async def trip(self, zone: str, why: str, mark: bool = True) -> bool:
        """Mark here (unless marked already), heal from the hub, Recall back.
        True if it went."""
        if self._busy:
            return False  # recover() inside the trip must not start another
        if zone.split("/")[-1].endswith("_Hub"):
            return False  # already at the hub (a defeat respawns us here): heal the usual way
        m = self.q._mark
        if m and m.kind in RETURN_KINDS and self.q._keep_dungeon_mark(await self.q.objective()):
            mark = False  # a dungeon/fight mark for this objective waits: Recall to it
        logger.info(f"{why}: going to the hub to heal, then back by Recall")
        if mark and not await self.q._mark_here("room"):
            logger.warning("could not mark the spot; healing here instead")
            return False
        zone = self.q._mark.zone if self.q._mark else zone
        self._busy = True
        self.q.controller.allow_idle(TRIP_MINUTES * 60)
        started = time.monotonic()
        try:
            if not await go_to_hub(self.client):
                logger.warning("the hub button didn't move us; healing where we are")
            await recover(self.client, self.cfg, self.q.controller, self.q.go_to_zone)
            hp, mana = await health_mana(self.client)
            took = (time.monotonic() - started) / 60
            logger.info(f"healed to {hp:.0%} health, {mana:.0%} mana in {took:.0f} min; recalling back")
            if not await self.q._recall(zone, "the marked spot"):
                logger.warning("could not recall back; the quest marker leads there")
        finally:
            self._busy = False
            self.q.controller.end_idle()
        return True
