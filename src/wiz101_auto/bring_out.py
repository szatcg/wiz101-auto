"""A "Talk to X" whose X isn't anywhere yet: work the room until X shows up.

Katzenstein's Lab ("Weird Science"): Clockwork only appears after Dr. Von
Katzenstein is beaten, the Crates around the lab are collected and handed to
Grunk, and the levers Grunk names are pulled. With no quest marker to follow,
the bot works through what the room offers, one thing per quest step, and
stops as soon as X appears or the objective moves on:

  1. a boss still standing in the zone: beat it;
  2. pick-ups (crates, boxes, parts): collect each once;
  3. named NPCs: talk to each once (Grunk takes the crates, names the levers);
  4. switches (levers...): every combination (puzzles.solve_by_trying).

`next_kind` is pure (unit tested); `BringOut.step` plays it.
"""

from __future__ import annotations

import asyncio
import math
import re
import time

from loguru import logger
from wizwalker import XYZ, Keycode

from . import ui
from .givers import is_named_npc
from .puzzles import is_switch

BOSS_MARK_DISTANCE = 700.0  # land (and mark) this far short of a remembered boss
PICKUP_WORDS = ("crate", "box", "part", "gear", "cog", "spring", "bolt", "piece",
                # (Malistaire's door: "The door is locked. Find the crystal.":
                # Firebender's Chest, the Crystal Stand)
                "chest", "crystal", "stand", "key", "gem", "orb")
_PICKUP = re.compile(r"\b(" + "|".join(PICKUP_WORDS) + r")s?\b", re.IGNORECASE)


def is_pickup(display: str) -> bool:
    """"Crate", "Planks"? (Crates yes; scenery like Planks no.)"""
    return bool(_PICKUP.search(display or ""))


def next_kind(boss_here: bool, pickups_left: int, npcs_left: int, switches_tried: bool) -> str | None:
    """What to do next to bring the missing NPC out."""
    if boss_here:
        return "boss"
    if pickups_left:
        return "pickup"
    if npcs_left:
        return "npc"
    if not switches_tried:
        return "switches"
    return None


SUMMON_AGAIN = 300.0  # a summoner used again after this (a fresh copy of the room)


class BringOut:
    def __init__(self, quester):
        self.q = quester
        self.client = quester.client
        self._done: dict[tuple[str, str], set] = {}  # (objective, zone) -> entity keys handled
        self._switched: set[tuple[str, str]] = set()
        self._boss_visited: set[tuple[str, str]] = set()

    async def _boss_here(self):
        """A boss among the loaded enemies, or None."""
        try:
            for mob in await self.client.get_mobs():
                t = await mob.fetch_npc_behavior_template()
                if t is not None and (await t.mob_title()).name == "boss":
                    return mob
        except Exception:
            pass
        return None

    async def _ready_for_boss(self) -> bool:
        """Full health before a boss: heal right here (wisps, rest) if not.
        True when ready. (It walked into two Clockwork Warriors at 49%.)"""
        from .upkeep import health_mana, recover

        cfg = self.q.upkeep
        if cfg is None:
            return True
        hp, mana = await health_mana(self.client)
        if hp >= cfg.min_health_to_fight:
            return True
        best = getattr(self, "_heal_best", -1.0)
        if hp > best + 0.01:
            self._heal_best, self._heal_tries = hp, 0
        self._heal_tries = getattr(self, "_heal_tries", 0) + 1
        if self._heal_tries > 4 and hp >= 0.5:
            # no wisps here and resting gains nothing: looping at 65% for 30 min
            # blocked the fight, so go in with what we have
            logger.info(f"healing here gains nothing (at {hp:.0%}); going to the boss anyway")
            return True
        logger.info(f"healing here to {cfg.min_health_to_fight:.0%} before the boss (at {hp:.0%})")
        await recover(self.client, cfg, self.q.controller)  # on the spot: no trips out of the dungeon
        return False

    async def _mark_for_boss(self, objective: str):
        """Mark next to the boss (once per objective), so a defeat is a Recall
        straight back here instead of the locked doors again."""
        m = self.q._mark
        zone = await self.client.zone_name() or ""
        if m and m.kind == "fight" and m.zone == zone and m.objective == objective:
            return
        await self.q._mark_here("fight", objective=objective)

    def _remembered_boss(self, zone: str):
        """(name, spot) of a boss known to be in this dungeon, from where it was seen."""
        from .dungeons import DungeonMemory

        names = [b for b, z in DungeonMemory.load().bosses.items() if z == zone]
        emap = getattr(self.q, "entity_map", None)
        for n in names:
            spots = emap.spots(zone, lambda s, n=n: s == n, (0.0, 0.0, 0.0)) if emap else []
            if spots:
                return n, spots[0]
        return None

    async def _scan(self, done: set):
        from .names import lang_name

        me = await self.client.body.position()
        pickups, npcs = [], []
        try:
            mobs = {await m.global_id_full() for m in await self.client.get_mobs()}
        except Exception:
            mobs = set()
        for e in await self.client.get_base_entity_list():
            try:
                t = await e.object_template()
                if not t:
                    continue
                code = await t.display_name()
                display = await lang_name(self.client, code) if code else ""
                obj = await t.object_name() or ""
                pos = await e.location()
                key = (obj, round(pos.x), round(pos.y))
                if key in done or not display:
                    continue
                d = math.dist((pos.x, pos.y), (me.x, me.y))
                enemy = await e.global_id_full() in mobs
                if is_pickup(display) and not is_switch(display) and not enemy:
                    pickups.append((d, key, display, pos))
                elif await e.global_id_full() not in mobs and is_named_npc(
                    obj, display, await e.list_behavior_names()
                ):
                    npcs.append((d, key, display, pos))
            except Exception:
                continue
        return sorted(pickups), sorted(npcs)

    async def _use(self, pos: XYZ) -> bool:
        """Walk up to something and press X at its prompt. True if a prompt came."""
        here = await self.client.body.position()
        dx, dy = here.x - pos.x, here.y - pos.y
        length = math.hypot(dx, dy) or 1.0
        await self.client.teleport(XYZ(pos.x + dx / length * 150, pos.y + dy / length * 150, pos.z))
        await asyncio.sleep(0.6)
        await self.client.goto(pos.x, pos.y)
        for nudge in (None, (Keycode.S, 0.2), (Keycode.W, 0.3), (Keycode.A, 0.2), (Keycode.D, 0.4)):
            if nudge:
                await self.client.send_key(*nudge)
                await asyncio.sleep(0.2)
            if await ui.is_visible(self.client, ui.NPC_RANGE):
                if self.q.dialogue:
                    self.q.dialogue.accept_offers_for(20)
                await self.client.send_key(Keycode.X, 0.1)
                await asyncio.sleep(1.5)
                from .upkeep import wait_until_free

                await wait_until_free(self.client, timeout=30)
                return True
        return False

    async def _fight_room(self, objective: str) -> bool:
        """Last thing to try in a room: its enemies (the Sea Chicken ship's
        "Press X to climb" into the cargo hold only came after its pirates were
        beaten; in a fresh copy the bot walked at the hatch for minutes)."""
        try:
            mobs = await self.client.get_mobs()
        except Exception:
            return False
        if not mobs:
            return False
        me = await self.client.body.position()
        spots = []
        for m in mobs:
            try:
                spots.append(await m.location())
            except Exception:
                continue
        if not spots or not await self._ready_for_boss():
            return bool(spots)
        spot = min(spots, key=lambda p: math.dist((p.x, p.y), (me.x, me.y)))
        logger.info(f"stuck on {objective!r}: beating this room's enemies first")
        from .safe_teleport import allow_engage

        allow_engage(self.client)
        await self.client.teleport(spot)
        await asyncio.sleep(3.0)
        return True

    async def step(self, objective: str, zone: str, name: str, fight: bool = False,
                   fight_room: bool = False) -> bool:
        """One thing toward bringing `name` out. True if it acted. `fight_room`:
        when nothing else is left, the room's enemies."""
        from .puzzles import find_switches, solve_by_trying

        key = (objective, zone)
        done = self._done.setdefault(key, set())
        boss = None if fight else await self._boss_here()  # bringing out a boss: none to beat first
        if not fight and boss is None and key not in self._boss_visited:
            spot = self._remembered_boss(zone)
            if spot is not None:
                # Enemies only load near the wizard: go where this dungeon's boss
                # was seen (Dr. Von Katzenstein, past two locked doors).
                if not await self._ready_for_boss():
                    return True
                self._boss_visited.add(key)
                name_, pos = spot
                logger.info(f"{name} isn't here yet; going to where {name_} was seen to beat them first")
                # Land short of the boss (landing on it starts the fight at
                # once), mark there, and the next step walks into the fight.
                me = await self.client.body.position()
                dx, dy = me.x - pos[0], me.y - pos[1]
                length = math.hypot(dx, dy) or 1.0
                back = BOSS_MARK_DISTANCE / length
                short = XYZ(pos[0] + dx * back, pos[1] + dy * back, pos[2])
                await self.client.teleport(short)
                await asyncio.sleep(1.5)
                if not await self.client.in_battle():
                    await self._mark_for_boss(objective)
                return True
        # A boss called up by using things around the room (Melanj in the
        # Cavern of the Wurm: six thumpers round the marker; the bot stood
        # there twice and set the main quest aside). Each used once.
        from .puzzles import _flip, is_summoner

        used = 0
        for label, pos in await find_switches(self.client, is_summoner):
            tkey = (zone, round(pos.x), round(pos.y))
            summoned = self.__dict__.setdefault("_summoned", {})
            # (Again after a while: a defeat or a heal trip out brings a fresh
            # copy of the cavern, its thumpers unused; kept as "done" for the
            # objective, the bot talked to Pol Atraydies and set the quest aside.)
            if time.monotonic() - summoned.get(tkey, -1e9) < SUMMON_AGAIN or await self.client.in_battle():
                continue
            summoned[tkey] = time.monotonic()
            used += 1
            # (All in one step: one a step, the quest was set aside before the
            # sixth thumper.)
            logger.info(f"{name} isn't here yet; using the {label} at ({pos.x:.0f}, {pos.y:.0f})")
            if not await _flip(self.q, pos):
                logger.info(f"no prompt at the {label}")
        if used:
            return True
        pickups, npcs = await self._scan(done)
        switches = await find_switches(self.client)
        # For a boss that won't spawn, no blind lever flipping: the dungeon's
        # own quest names each lever (Counterweight East), flips undo them.
        no_switching = key in self._switched or not switches or fight
        kind = next_kind(boss is not None, len(pickups), len(npcs), no_switching)
        if kind is None:
            return fight_room and await self._fight_room(objective)
        if kind == "boss":
            if not await self._ready_for_boss():
                return True
            await self._mark_for_boss(objective)
            logger.info(f"{name} isn't here yet; beating the boss in this room first")
            from .safe_teleport import allow_engage

            allow_engage(self.client)
            await self.client.teleport(await boss.location())
            await asyncio.sleep(3.0)
            return True
        if kind in ("pickup", "npc"):
            _d, ekey, display, pos = (pickups or npcs)[0]
            done.add(ekey)
            what = "picking up" if kind == "pickup" else "talking to"
            logger.info(f"{name} isn't here yet; {what} {display}")
            if not await self._use(pos):
                logger.debug(f"no prompt at {display}")
            return True
        self._switched.add(key)
        logger.info(f"{name} isn't here yet; trying the switches")
        await solve_by_trying(self.q, objective, target=name)
        return True
