"""`mode: boss` — fight one dungeon boss over and over, e.g. for a pet drop.

The dungeon and boss are learned while questing (see dungeons.py). Each run:
heal up, travel to the dungeon's zone, enter by its sigil, fight the boss,
check the backpack for the wanted item, leave the dungeon, repeat.
"""

from __future__ import annotations

import asyncio
import math

from loguru import logger
from wizwalker import XYZ, Keycode

from .config import BossFarmConfig
from .dungeons import DungeonEntry, DungeonMemory
from .names import lang_name
from .upkeep import is_free, recover, wait_for_loading

EXIT_WALK_SECONDS = 2.5


async def backpack_has(client, item: str) -> bool:
    """True if an item whose name contains `item` is in the backpack or equipped."""
    from wizwalker.memory.memory_objects.game_object_template import WizGameObjectTemplate

    want = item.strip().lower()
    co = client.client_object
    for behavior in (await co.try_get_inventory_behavior(), await co.try_get_equipment_behavior()):
        if behavior is None:
            continue
        for obj in await behavior.item_list():
            try:
                core = await obj.object_template()
                t = WizGameObjectTemplate(client.hook_handler, await core.read_base_address())
                names = [await t.object_name() or ""]
                code = await t.display_name()
                if code:
                    names.append(await lang_name(client, code))
                if any(want in n.lower() for n in names if n):
                    return True
            except Exception:
                continue
    return False


async def find_entity_named(client, name: str):
    """Position of an entity whose display (or object) name matches `name`."""
    want = "".join(c for c in name.lower() if c.isalpha())
    for e in await client.get_base_entity_list():
        try:
            t = await e.object_template()
            if not t:
                continue
            names = [await t.object_name() or ""]
            code = await t.display_name()
            if code:
                names.append(await lang_name(client, code))
            if any(want and want in "".join(c for c in n.lower() if c.isalpha()) for n in names):
                return await e.location()
        except Exception:
            continue
    return None


class BossFarmer:
    def __init__(self, quester, cfg: BossFarmConfig, upkeep_cfg, controller):
        self.q = quester  # reuses its travel, sigil entry and gate routing
        self.client = quester.client
        self.cfg = cfg
        self.upkeep = upkeep_cfg
        self.controller = controller
        self.runs = 0
        self._fought_this_visit = False

    async def run(self):
        goal = f" until {self.cfg.until_item!r}" if self.cfg.until_item else ""
        logger.info(f"boss farming {self.cfg.boss!r}{goal}")
        while not self.controller.stopped.is_set():
            await self.controller.checkpoint()
            if await is_free(self.client):
                try:
                    await self.step()
                except Exception as exc:
                    logger.opt(exception=exc).warning("boss farm step failed; retrying")
                    await asyncio.sleep(2.0)
            await asyncio.sleep(1.0)

    async def step(self):
        if self.cfg.until_item and await backpack_has(self.client, self.cfg.until_item):
            self.controller.stop(f"got {self.cfg.until_item} after {self.runs} run(s)")
            return
        if self.cfg.max_runs and self.runs >= self.cfg.max_runs:
            self.controller.stop(f"finished {self.runs} boss run(s)")
            return
        found = DungeonMemory.load().find_for_boss(self.cfg.boss)
        if found is None:
            self.controller.stop(
                f"don't know where {self.cfg.boss!r} is yet: beat it once in quest mode "
                "(entering its dungeon by the sigil) so the bot learns the dungeon"
            )
            return
        interior, entry = found
        zone = await self.client.zone_name() or ""

        if zone == interior:
            boss = await find_entity_named(self.client, self.cfg.boss)
            if boss is None:
                if self._fought_this_visit:
                    self.runs += 1
                    logger.success(f"boss run {self.runs} done")
                self._fought_this_visit = False
                await self.leave(interior, entry)
                return
            if self.upkeep.needs_recovery(*await self._health_mana()):
                logger.info("too hurt to fight the boss; leaving to heal")
                await self.leave(interior, entry)
                return
            logger.info(f"run {self.runs + 1}: engaging {self.cfg.boss}")
            self._fought_this_visit = True
            from .safe_teleport import allow_engage

            allow_engage(self.client)
            await self.client.teleport(boss)
            await asyncio.sleep(2.0)
            return

        if not await recover(self.client, self.upkeep, self.controller, self.q.go_to_zone):
            return
        if zone != entry.outside:
            if not await self.q.go_to_zone(entry.outside):
                logger.warning(f"could not route to {entry.outside}")
                await asyncio.sleep(5.0)
            return
        await self.q._enter_by_sigil(XYZ(*entry.sigil), zone)

    async def _health_mana(self) -> tuple[float, float]:
        from .upkeep import health_mana

        return await health_mana(self.client)

    async def leave(self, interior: str, entry: DungeonEntry) -> bool:
        """The exit is normally right behind where the wizard appears: go back
        there, face the arrival direction and walk backwards; else try each side."""
        spawn = XYZ(*entry.spawn)
        await self.client.teleport(spawn)
        await asyncio.sleep(1.0)
        try:
            await self.client.body.write_yaw(entry.yaw)
        except Exception:
            pass
        await self.client.send_key(Keycode.S, EXIT_WALK_SECONDS)
        await wait_for_loading(self.client)
        if await self.client.zone_name() != interior:
            logger.info("left the dungeon")
            return True
        for i in range(4):
            ang = i * math.pi / 2
            await self.client.teleport(spawn)
            await asyncio.sleep(0.8)
            await self.client.goto(spawn.x + 700 * math.cos(ang), spawn.y + 700 * math.sin(ang))
            await wait_for_loading(self.client)
            if await self.client.zone_name() != interior:
                logger.info("left the dungeon")
                return True
        logger.warning("could not find the dungeon exit")
        return False
