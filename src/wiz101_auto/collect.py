"""Collect objectives ("Collect Cog in Triton Avenue (0 of 3)").

The quest helper often has no marker for these, so we look for entities in
the zone whose name matches the item, teleport to each (away from mobs) and
press X. Matching logic is pure and unit tested; approach follows Deimos'
auto-collect.
"""

from __future__ import annotations

import asyncio
import re
import time

from loguru import logger

from .names import lang_name

CANDIDATE_CACHE_SECONDS = 5.0
WALK_IN = 150.0  # distance to land from an item before walking onto it

_VERBS = r"(?:collect|find|gather|get|retrieve|recover|pick up)"
_OBJECTIVE = re.compile(rf"^\s*{_VERBS}\s+(.+?)(?:\s+(?:in|at|from|on)\s+.*)?\s*$", re.I)
_SKIP = ("wisp", "duelcircle", "player object", "basic positional", "basic ambient", "teleportpad", "sigil")


def collect_item_name(objective: str) -> str:
    """'Collect Cog in Triton Avenue (0 of 3)' -> 'Cog'. Empty if not a collect objective."""
    text = re.sub(r"\(\d+\s+of\s+\d+\)", "", objective).strip()
    m = _OBJECTIVE.match(text)
    return m.group(1).strip() if m else ""


def _norm(s: str) -> str:
    return re.sub(r"[^a-z]", "", s.lower())


def matches_item(item: str, *names: str) -> bool:
    """True if any of the entity's names refers to the item (e.g. 'Cog' vs 'WC_Cog_01')."""
    item = re.sub(r"^(the|a|an|some)\s+", "", item.strip(), flags=re.I)
    target = _norm(item.rstrip("s")) or _norm(item)
    if len(target) < 3:
        return False
    for name in names:
        n = _norm(name)
        if not n or any(s.replace(" ", "") in n for s in _SKIP):
            continue
        if target in n:
            return True
    return False


def spread_points(points: list[tuple[float, float, float]], start, spacing: float) -> list:
    """Points at least `spacing` apart, nearest to `start` first. Pickups only load
    near the wizard, so a zone-wide search teleports between these."""
    chosen: list = []
    for p in sorted(points, key=lambda p: (p[0] - start[0]) ** 2 + (p[1] - start[1]) ** 2):
        if all((p[0] - c[0]) ** 2 + (p[1] - c[1]) ** 2 >= spacing**2 for c in chosen):
            chosen.append(p)
    return chosen


LANDMARK_NAMES = ("player stand in", "duel circle")  # unnamed but always on walkable ground


class Collector:
    def __init__(self, client, safe_distance: float = 700.0):
        self.client = client
        self.safe_distance = safe_distance
        self._taken: dict[int, float] = {}  # entity id -> when we grabbed it
        self._cache: tuple[str, float, list] | None = None

    async def _candidates(self, item: str) -> list:
        # Entity scans are expensive; reuse the result for a few seconds.
        now = time.monotonic()
        if self._cache and self._cache[0] == item and now - self._cache[1] < CANDIDATE_CACHE_SECONDS:
            return self._cache[2]
        found = []
        for e in await self.client.get_base_entity_list():
            try:
                template = await e.object_template()
                if not template:
                    continue
                if matches_item(item, await template.object_name()):
                    found.append(e)
                    continue
                code = await template.display_name()
                if code and matches_item(item, await lang_name(self.client, code)):
                    found.append(e)
            except Exception:
                continue
        self._cache = (item, now, found)
        return found

    async def collect_once(self, item: str, press_interact) -> bool:
        """Grab the nearest safe matching entity. True if we tried one."""
        entities = await self._candidates(item)
        if not entities:
            return False
        safe = await self.client.find_safe_entities_from(entities, safe_distance=self.safe_distance)
        me = await self.client.body.position()
        now = time.monotonic()
        options = []
        for e in safe:
            try:
                gid = await e.global_id_full()
            except Exception:
                gid = id(e)
            if now - self._taken.get(gid, -1e9) < 60:
                continue  # just took it; give it time to despawn/respawn
            options.append((await e.location(), gid))
        if not options:
            return False
        spot, gid = min(options, key=lambda o: o[0].distance(me))
        self._taken[gid] = now
        logger.info(f"collecting {item!r} at ({spot.x:.0f}, {spot.y:.0f})")
        # Pickup prompts trigger on walking into range, not on teleporting onto
        # the item: land a short way off, then walk onto it.
        from wizwalker import XYZ

        await self.client.teleport(XYZ(spot.x + WALK_IN, spot.y, spot.z))
        await asyncio.sleep(0.8)
        await self.client.goto(spot.x, spot.y)
        await asyncio.sleep(0.3)
        await press_interact()
        return True
