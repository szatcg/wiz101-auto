"""Looking for quests to pick up: talk once to each named NPC nearby.

A yellow "!" over an NPC means a quest on offer, but nothing in the game's
memory tells a quest giver from any other NPC (same behaviors). So in the main
quest's world the bot walks up to each named NPC near it once (ambient
townsfolk, "MB-AmbLady-I", are skipped) and talks: the dialogue loop accepts
whatever is offered. More quests in the book means kills and turn-ins made on
the way count twice. Who was asked is kept in state/npc_talked.json and asked
again after a few hours (new quests open up as others are done).
"""

from __future__ import annotations

import asyncio
import json
import math
import re
import time
from pathlib import Path

from loguru import logger
from wizwalker import XYZ, Keycode

from . import ui
from .upkeep import is_free, mob_positions, wait_until_free

TALKED_PATH = Path("state") / "npc_talked.json"
ASK_AGAIN_HOURS = 1.0
ZONE_CHECKS_PATH = Path("state") / "npc_zone_checks.json"
ZONE_RECHECK_SECONDS = 3600.0  # entering a zone not swept this long: ask every named NPC in it
GIVER_RANGE = 2500.0  # NPCs this close are worth a quick word
CHECK_SECONDS = 20.0  # how often to look for someone new to ask
MOB_CLEARANCE = 700.0  # never walk up to an NPC standing by enemies


def is_named_npc(object_name: str, display: str, behaviors: list[str]) -> bool:
    """A named NPC who may hand out quests: has NPC behavior and a name, and
    isn't ambient scenery ("MB-AmbWalker10") or a trigger."""
    obj = (object_name or "").lower()
    parts = re.split(r"[-_ ]+", obj)
    ambient = any(_AMBIENT.match(p) for p in parts)
    return (
        "NPCBehavior" in behaviors
        and bool((display or "").strip())
        and not ambient
        and not obj.startswith("dynatrigger")
    )


_AMBIENT = re.compile(r"^amb(?!rose)")  # "AmbLady", "AmbWalker10"; not Headmaster Ambrose


class QuestGivers:
    def __init__(self, quester):
        self.q = quester
        self.client = quester.client
        self._last_check = 0.0
        self._talked: dict[str, float] = {}
        self._zone = ""
        self._sweeping = False  # asking every named NPC in this zone, one after another
        try:
            self._talked = json.loads(TALKED_PATH.read_text(encoding="utf-8"))
        except Exception:
            pass
        self._zone_checks: dict[str, float] = {}
        try:
            self._zone_checks = json.loads(ZONE_CHECKS_PATH.read_text(encoding="utf-8"))
        except Exception:
            pass

    def _swept(self, zone: str):
        self._zone_checks[zone] = time.time()
        try:
            ZONE_CHECKS_PATH.parent.mkdir(exist_ok=True)
            ZONE_CHECKS_PATH.write_text(json.dumps(self._zone_checks, indent=1), encoding="utf-8")
        except Exception:
            pass

    def _key(self, zone: str, name: str) -> str:
        return f"{zone}|{name}"

    def _asked_recently(self, zone: str, name: str) -> bool:
        when = self._talked.get(self._key(zone, name))
        return when is not None and time.time() - when < ASK_AGAIN_HOURS * 3600

    def _remember(self, zone: str, name: str):
        self._talked[self._key(zone, name)] = time.time()
        try:
            TALKED_PATH.parent.mkdir(exist_ok=True)
            TALKED_PATH.write_text(json.dumps(self._talked, indent=1), encoding="utf-8")
        except Exception:
            pass

    async def _candidates(self, zone: str, reach: float = GIVER_RANGE) -> list[tuple[float, str, XYZ]]:
        from .names import lang_name

        me = await self.client.body.position()
        mobs = await mob_positions(self.client)
        out = []
        for e in await self.client.get_base_entity_list():
            try:
                template = await e.object_template()
                if not template:
                    continue
                code = await template.display_name()
                display = await lang_name(self.client, code) if code else ""
                if not display or self._asked_recently(zone, display):
                    continue
                pos = await e.location()
                d = math.dist((pos.x, pos.y), (me.x, me.y))
                if d > reach:
                    continue
                if not is_named_npc(await template.object_name(), display, await e.list_behavior_names()):
                    continue
                if any(math.dist((pos.x, pos.y), m[:2]) < MOB_CLEARANCE for m in mobs):
                    continue  # an enemy (or beside one)
                out.append((d, display, pos))
            except Exception:
                continue
        return sorted(out, key=lambda c: c[0])

    async def ask_nearby(self) -> bool:
        """Talk to the nearest named NPC not asked yet (accepting any quest
        offered), then step back. True if it went to one."""
        if not self._sweeping and time.monotonic() - self._last_check < CHECK_SECONDS:
            return False
        self._last_check = time.monotonic()
        zone = await self.client.zone_name() or ""
        world = self.q._main_world
        if not zone or not world or zone.split("/", 1)[0] != world or await self.q._in_dungeon(zone):
            return False
        if zone != self._zone:
            # A new zone: not swept for an hour, ask everyone in it (quests
            # unlock as the story moves on; the 2500 range alone missed them).
            self._zone = zone
            self._sweeping = time.time() - self._zone_checks.get(zone, 0.0) > ZONE_RECHECK_SECONDS
            if self._sweeping:
                logger.info(f"checking the NPCs of {zone.split('/')[-1]} for new quests")
        if not await is_free(self.client):
            return False
        found = await self._candidates(zone, float("inf") if self._sweeping else GIVER_RANGE)
        if not found:
            if self._sweeping:
                self._sweeping = False
                self._swept(zone)
                logger.info(f"asked every NPC in {zone.split('/')[-1]} for quests")
            return False
        _d, name, pos = found[0]
        self._remember(zone, name)  # once, whatever happens
        me = await self.client.body.position()
        back = XYZ(me.x, me.y, me.z)
        logger.info(f"asking {name} for quests ({_d:.0f} away)")
        # The quester's approach (teleport near, inch in on foot until the talk
        # prompt shows): landing 200 short left the Marleybone hub's quest
        # givers out of range ("no talk prompt").
        await self.q.travel(pos, npc=True)
        await asyncio.sleep(0.3)
        for nudge in (None, (Keycode.S, 0.2), (Keycode.W, 0.3), (Keycode.W, 0.3)):
            if nudge:
                await self.client.send_key(*nudge)
                await asyncio.sleep(0.2)
            if await ui.is_visible(self.client, ui.NPC_RANGE):
                break
        prompt = (await ui.text_at(self.client, ui.NPC_RANGE_TEXT)).lower()
        if "talk" in prompt:
            if self.q.dialogue:
                self.q.dialogue.accept_offers_for(20)
            accepted = self.q.dialogue.accepted if self.q.dialogue else 0
            await self.client.send_key(Keycode.X, 0.1)
            await asyncio.sleep(1.5)
            if await self.q.services.is_open():
                # A menu of services (shops, several quests): nothing to pick blindly.
                await self.q.services.close()
            await wait_until_free(self.client, timeout=20)
            await ui.close_menus(self.client)
            if self.q.dialogue and self.q.dialogue.accepted > accepted:
                logger.success(f"picked up a quest from {name}")
                self.q._ranked_for = None
                self.q._last_rank = -1e9  # re-rank: maybe it's a quick errand
        else:
            logger.debug(f"no talk prompt at {name} ({prompt!r})")
        if await is_free(self.client):
            await self.client.teleport(back)
        return True
