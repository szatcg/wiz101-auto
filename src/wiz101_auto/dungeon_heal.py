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
from .dungeons import DungeonMemory
from .marks import RETURN_KINDS
from .upkeep import health_mana, is_free, recover, wait_for_loading, wait_until_free

DUNGEON_MANA_TRIP = 0.3  # inside a dungeon, leave to refill mana only below this
DEFEAT_SETTLE_SECONDS = 5.0  # after a fight, before deciding on a heal trip
HEAL_TRIES = 4  # recover() rounds on a heal trip (a fight can cut one short)
TRIP_MINUTES = 20  # the watchdog allowance for a trip; well within the game's 30 minutes
# The compass's teleport buttons: "GoHomeButton" goes to the current world's
# hub (the Oasis in Krokotopia); "GotoDormButton" goes to the dorm.
HUB_BUTTON = "GoHomeButton"
DORM_BUTTON = "GotoDormButton"


async def _press(client, button: str) -> bool:
    from .travel_data import note_zone_jump

    before = await client.zone_name()
    note_zone_jump()
    if not await ui.click_named(client, button):
        return False
    await asyncio.sleep(1.0)
    await ui.confirm_modal(client)
    await wait_for_loading(client, appear_timeout=6.0)
    return await client.zone_name() != before


async def go_to_hub(client) -> bool:
    """Teleport to the current world's hub. True if the zone changed. The
    button doesn't always take the first time (in Counterweight East it
    didn't): press it again, else go by the dorm and on to the hub."""
    from .travel_data import learn_hub

    for _ in range(2):
        if await _press(client, HUB_BUTTON):
            learn_hub(await client.zone_name() or "")
            return True
        await asyncio.sleep(2.0)
    if await _press(client, DORM_BUTTON):
        logger.info("the hub button didn't work; went by the dorm")
        await asyncio.sleep(2.0)
        await _press(client, HUB_BUTTON)
        return True
    return False


def dungeon_wisp_zone(here: str, first_room: str, count) -> str | None:
    """Another zone of the dungeon we're in with remembered wisps of the kind
    needed (`count(zone)` > 0): healing there and Recalling back beats leaving
    the dungeon (the Labyrinth: its main hall from a Detention room)."""
    from .dungeons import INSTANCE_ZONES

    rooms = [first_room, *(z for z, first in INSTANCE_ZONES.items() if first == first_room)]
    return next((z for z in rooms if z and z != here and count(z) > 0), None)


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
        # The dungeon's own wisps first (Mount Olympus has them in its first
        # room): no trip out if they do the job.
        from .upkeep import collect_wisps, needed_wisps, visit_known_spot

        took = await collect_wisps(self.client, self.cfg)
        if not took:
            took = await visit_known_spot(self.client, self.cfg, zone, needed_wisps(self.cfg, hp, mana))
        if took:
            return True
        # (Marked beside the boss: the trip keeps that mark and Recalls to it.
        # Healing in place with no wisps left the wizard at 2% in the Emperor's
        # Palace, retrying every 3 seconds.)
        return await self.trip(zone, f"health {hp:.0%}, mana {mana:.0%} in the dungeon")

    async def trip(self, zone: str, why: str, mark: bool = True) -> bool:
        """Mark here (unless marked already), heal from the hub, Recall back.
        True if it went."""
        if self._busy:
            return False  # recover() inside the trip must not start another
        from .travel_data import is_world_hub

        if is_world_hub(zone) or is_world_hub(await self.client.zone_name() or ""):
            return False  # already at the hub (a defeat respawns us here): heal the usual way
        from .dungeons import no_return

        if no_return(await self.client.zone_name() or ""):
            # Recall can't bring us back here (the Death Realm): fight on.
            return False
        if self.q._active_quest:
            # Back from the trip, the quest we left stays the one (its
            # 'mid-way' time starts again): a ranking after the Recall picked
            # the main quest over the tower's own.
            self.q._momentum = (self.q._active_quest, time.monotonic())
        m = self.q._mark
        here = await self.client.zone_name() or ""
        inside = bool(m and m.zone == here)
        if inside and m.kind in RETURN_KINDS and self.q._keep_dungeon_mark(await self.q.objective()):
            mark = False  # marked beside this dungeon's boss already: Recall to it
        elif not inside and here in DungeonMemory.load().dungeons:
            mark = True
        # (A mark outside, on the entrance sigil, would bring us back to the
        # start of the dungeon: mark this spot inside instead.)
        if self.q._recall_pending:
            mark = False  # after a defeat: the mark waits for its Recall
        # The dungeon's own wisps first (another of its rooms): faster, and
        # the dungeon isn't left (the player's rule).
        from .upkeep import needed_wisps, wisp_memory

        hp, mana = await health_mana(self.client)
        need = needed_wisps(self.cfg, hp, mana)
        heal_zone = None
        if await self.q._in_dungeon(here) and self.q._dungeon:
            heal_zone = dungeon_wisp_zone(here, self.q._dungeon[1], lambda z: wisp_memory().count(z, need))
        where = f"{heal_zone.split('/')[-1]} (in the dungeon)" if heal_zone else "the hub"
        logger.info(f"{why}: going to {where} to heal, then back by Recall")
        if mark and not await self.q._mark_here("room"):
            logger.warning("could not mark the spot; healing here instead")
            return False
        zone = self.q._mark.zone if self.q._mark else zone
        # Kept on disk: back from healing, the Recall comes before anything
        # else, even after a restart (one lost it and the bot walked to the
        # Labyrinth's sigil: a fresh copy, progress gone).
        self.q._recall_pending = True
        self._busy = True
        self.q.controller.allow_idle(TRIP_MINUTES * 60)
        started = time.monotonic()
        try:
            if heal_zone and not await self.q.go_to_zone(heal_zone):
                logger.warning(f"could not walk to {heal_zone}; healing from the hub instead")
                heal_zone = None
            if heal_zone is None and not await go_to_hub(self.client):
                logger.warning("the hub button didn't move us; healing where we are")
            for _ in range(HEAL_TRIES):
                # A fight that starts while healing (Hyde Park's patrols) ends
                # recover() early: wait it out and go on healing, then Recall.
                await wait_until_free(self.client)
                if await recover(self.client, self.cfg, self.q.controller, self.q.go_to_zone):
                    break
            hp, mana = await health_mana(self.client)
            took = (time.monotonic() - started) / 60
            logger.info(f"healed to {hp:.0%} health, {mana:.0%} mana in {took:.0f} min; recalling back")
            await wait_until_free(self.client)
            if await self.client.zone_name() == zone or await self.q._recall(zone, "the marked spot"):
                self.q._recall_pending = False
            else:
                # (A fight started while leaving: the step's Recall to the
                # mark tries again first thing.)
                logger.warning("could not recall back; will try again when free")
                self.q._recall_blocked_until = 0.0
                self.q._recalled_for = ""
        finally:
            self._busy = False
            self.q.controller.end_idle()
        return True
