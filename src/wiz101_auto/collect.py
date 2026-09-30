"""Collect objectives ("Collect Cog in Triton Avenue (0 of 3)").

The quest helper often has no marker for these, so we look for entities in
the zone whose name matches the item, teleport to each (away from mobs) and
press X. Matching logic is pure and unit tested; approach follows Deimos'
auto-collect.
"""

from __future__ import annotations

import asyncio
import math
import re
import time

from loguru import logger

from .names import lang_name

CANDIDATE_CACHE_SECONDS = 5.0
BOSS_CHEST_WAIT = 6.0  # seconds to wait for a boss's loot chest to appear
BOSS_CHEST_RANGE = 3000.0  # the chest appears in the boss room
LOOT_RANGE = 2000.0  # free pickups this close are worth the small detour
WALK_IN = 150.0  # distance to land from an item before walking onto it

_VERBS = r"(?:collect|destroy|find|gather|get|retrieve|recover|pick up)"
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
    # Plurals: "Supplies" -> "supply" (the game's "Supply Crate"), "Cogs" -> "cog".
    single = item[:-3] + "y" if item.lower().endswith("ies") else item.rstrip("s")
    target = _norm(single) or _norm(item)
    if len(target) < 3:
        return False
    for name in names:
        n = _norm(name)
        if not n or any(s.replace(" ", "") in n for s in _SKIP):
            continue
        if target in n:
            return True
        # Object names use short words: "Gemstones" lie around as "KT_Gem_Fire".
        # Only the object's kind (its first word after a zone prefix like
        # "KT_") counts, so "Firecat Whiskers" doesn't match that gem's "Fire".
        words = [_norm(w) for w in re.split(r"[_\-\s]+", name)]
        words = [w for w in words if len(w) >= 3 and not w.isdigit()]
        if words and target.startswith(words[0]):
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


# Unnamed but always on walkable ground. Not duel circles: those are fights
# (often another player's), and landing on one joins it.
LANDMARK_NAMES = ("player stand in",)
DUEL_CIRCLE = "duel circle"
CIRCLE_EXTRA = 350.0  # pick-ups keep this much further from a duel circle than from an enemy


async def duel_circles(client) -> list[tuple[float, float, float]]:
    """Fights going on in the zone (ours or other players'): walking or
    teleporting into one joins it, and their enemies may not be listed as
    mobs while they fight."""
    out = []
    try:
        for e in await client.get_base_entity_list():
            try:
                template = await e.object_template()
                if template and (await template.object_name() or "").lower() == DUEL_CIRCLE:
                    pos = await e.location()
                    out.append((pos.x, pos.y, pos.z))
            except Exception:
                continue
    except Exception:
        pass
    return out


async def landmarks(client) -> list[tuple[float, float, float]]:
    """Positions of ground-level things in the zone (named NPCs/objects, stand-in
    spots, duel circles). Safe teleport stops: unlike cameras and path markers,
    they're on the walkable map."""
    out = []
    for e in await client.get_base_entity_list():
        try:
            template = await e.object_template()
            if not template:
                continue
            name = (await template.object_name() or "").lower()
            named = bool(await template.display_name())
            if not named and not any(n in name for n in LANDMARK_NAMES):
                continue
            if "wisp" in name:
                continue
            pos = await e.location()
            out.append((pos.x, pos.y, pos.z))
        except Exception:
            continue
    return out


PATH_POINT_NAME = "basic positional"  # unnamed markers along a zone's walkways (and cameras)
PATH_POINT_HEIGHT = 400.0  # keep those about as high as the wizard's floor (cameras hang higher)


def floor_points(points: list, ground_z: float, tolerance: float = PATH_POINT_HEIGHT) -> list:
    """Points near the wizard's floor height (drops camera markers up in the air)."""
    return [p for p in points if abs(p[2] - ground_z) <= tolerance]


async def path_points(client) -> list[tuple[float, float, float]]:
    """Positions of the zone's unnamed path markers: wandering enemies patrol
    along the walkways these line, well away from any named landmark."""
    out = []
    for e in await client.get_base_entity_list():
        try:
            template = await e.object_template()
            if template and (await template.object_name() or "").lower() == PATH_POINT_NAME:
                pos = await e.location()
                out.append((pos.x, pos.y, pos.z))
        except Exception:
            continue
    return out


# Free pickups lying around the world: reagents (object names like "Parchment",
# "Wood", "Cattail", "FrostedFlax_01"), treasure chests ("WC-Chest-Common-001"
# Wooden Chest, "-Rare-" Silver Chest) and hidden house items ("COLLECT_...").
REAGENTS = (
    "parchment", "wood", "stone", "stoneblock", "ore", "cattail", "frostedflax", "frostflower",
    "blacklotus", "mandrake", "redmandrake", "deepmushroom", "mushroom", "fireflower", "pearl",
    "sunstone", "amber", "ash", "spidersilk", "springwater", "shell", "mistwood", "scrapiron",
    "bloodmoss", "lavalily", "sandstone", "stormcloud", "ghostfire", "jewel",
)
_REAGENT_RE = re.compile(r"^(?:[a-z]{2}_)?(" + "|".join(REAGENTS) + r")(?:_?\d+)?$")
_CHEST_RE = re.compile(r"(^|[-_])chest([-_]|$)")


def is_collectable(object_name: str) -> bool:
    """A free pickup worth a small detour (a reagent, chest or house item)."""
    name = (object_name or "").strip().lower().replace(" ", "")
    if not name:
        return False
    if "chest" in name and "rare" in name and not name.startswith("wc-"):
        # Locked (MB-Chest-Rare-001): X on it opened the Shockalock lock-picking
        # minigame. Wizard City's rare chests open like any other.
        return False
    return name.startswith("collect_") or bool(_CHEST_RE.search(name)) or bool(_REAGENT_RE.match(name))


def is_chest(object_name: str) -> bool:
    """Any chest, however it's named ("KT-Chest-Boss-001", "BossChest")."""
    return "chest" in (object_name or "").lower()


def away_from(points: list, mobs: list, safe_distance: float) -> list:
    """Points with no mob within `safe_distance` (mobs are landmarks too)."""
    def clear(p) -> bool:
        return all((p[0] - m[0]) ** 2 + (p[1] - m[1]) ** 2 > safe_distance**2 for m in mobs)

    return [p for p in points if clear(p)]


async def _text_windows(window, depth: int = 0) -> list:
    out = []
    try:
        if depth and not await window.is_visible():
            return out
        text = await window.maybe_text() or ""
        if text.strip():
            out.append((text, window))
    except Exception:
        return out
    if depth < 14:
        for child in await window.children():
            out += await _text_windows(child, depth + 1)
    return out


async def choose_free_chest(client) -> bool:
    """A boss chest offers ways to open it (free, or paid with Crowns or a
    key): pick the free one. The window isn't mapped yet: it's saved to
    state/chest_window.txt, and the option is found by the word "free"."""
    import re
    from pathlib import Path

    from . import ui

    try:
        lines = await ui.dump_tree(client.root_window, max_depth=12)
        Path("state").mkdir(exist_ok=True)
        Path("state/chest_window.txt").write_text("\n".join(lines), encoding="utf-8", errors="replace")
    except Exception:
        pass
    for text, w in await _text_windows(client.root_window):
        plain = re.sub(r"<[^>]+>", "", text).strip().lower()
        if re.search(r"\bfree\b", plain):
            logger.info(f"chest: choosing the free option ({plain[:60]!r})")
            await ui.click_center(client, w)
            await asyncio.sleep(1.5)
            box = await ui.modal_box(client)
            if box is not None and "crown" not in (await ui.modal_text(box)).lower():
                await ui.press_modal_button(client, box, "centerButton")
            return True
    return False


class Collector:
    def __init__(self, client, safe_distance: float = 700.0):
        self.client = client
        self.safe_distance = safe_distance
        self._looted: set[int] = set()  # free pickups already tried (by entity id)
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

    async def collect_nearby(self, press_interact, max_range: float = LOOT_RANGE) -> bool:
        """Grab the nearest safe free pickup (reagent, chest...) within
        `max_range`. Each one is tried once (chests stay after opening). True
        if it went for one."""
        me = await self.client.body.position()
        found = []
        for e in await self.client.get_base_entity_list():
            try:
                template = await e.object_template()
                if not template or not is_collectable(await template.object_name()):
                    continue
                gid = await e.global_id_full()
                if gid in self._looted:
                    continue
                pos = await e.location()
                if pos.distance(me) <= max_range:
                    found.append((e, gid, pos, await template.object_name()))
            except Exception:
                continue
        if not found:
            return False
        entities = [f[0] for f in found]
        safe = await self.client.find_safe_entities_from(entities, safe_distance=self.safe_distance)
        safe_ids = {id(e) for e in safe}
        circles = await duel_circles(self.client)
        options = [
            f for f in found
            if id(f[0]) in safe_ids
            and away_from([(f[2].x, f[2].y, f[2].z)], circles, self.safe_distance + CIRCLE_EXTRA)
        ]
        if not options:
            return False
        _e, gid, spot, name = min(options, key=lambda f: f[2].distance(me))
        self._looted.add(gid)
        logger.info(f"picking up {name!r} on the way ({spot.distance(me):.0f} away)")
        from wizwalker import XYZ

        back = XYZ(me.x, me.y, me.z)
        await self.client.teleport(XYZ(spot.x + WALK_IN, spot.y, spot.z))
        from .safe_teleport import teleport_aborted

        if teleport_aborted(self.client):
            return True  # enemies by the item: tried (it's skipped for a while), don't walk in
        await asyncio.sleep(0.8)
        await self.client.goto(spot.x, spot.y)
        await asyncio.sleep(0.3)
        await press_interact()
        await asyncio.sleep(1.0)
        await self.client.teleport(back)  # carry on from where we were
        return True

    async def loot_boss_chest(self, press_interact, wait: float = BOSS_CHEST_WAIT,
                              max_range: float = BOSS_CHEST_RANGE) -> bool:
        """Right after a boss fight: wait a few seconds for a loot chest to
        appear near us, open it, and go back to where we stood. True if opened."""
        from wizwalker import XYZ

        me = await self.client.body.position()
        deadline = time.monotonic() + wait
        while True:
            found = []
            for e in await self.client.get_base_entity_list():
                try:
                    template = await e.object_template()
                    name = await template.object_name() if template else ""
                    if not is_chest(name):
                        continue
                    gid = await e.global_id_full()
                    pos = await e.location()
                    if gid not in self._looted and pos.distance(me) <= max_range:
                        found.append((pos.distance(me), gid, pos, name))
                except Exception:
                    continue
            if found or time.monotonic() > deadline:
                break
            await asyncio.sleep(1.0)
        if not found:
            logger.info("no loot chest after the boss fight")
            return False
        _d, gid, spot, name = min(found, key=lambda f: f[0])
        self._looted.add(gid)
        logger.info(f"boss loot chest {name!r} ({spot.distance(me):.0f} away): opening it")
        back = XYZ(me.x, me.y, me.z)
        await self.client.teleport(XYZ(spot.x + WALK_IN, spot.y, spot.z))
        await asyncio.sleep(0.4)
        await self.client.goto(spot.x, spot.y)
        await asyncio.sleep(0.3)
        await press_interact()
        await asyncio.sleep(1.5)
        await choose_free_chest(self.client)
        await self.client.teleport(back)
        return True

    async def collect_once(self, item: str, press_interact) -> bool:
        """Grab the nearest safe matching entity. True if we tried one."""
        entities = await self._candidates(item)
        if not entities:
            return False
        safe = await self.client.find_safe_entities_from(entities, safe_distance=self.safe_distance)
        me = await self.client.body.position()
        now = time.monotonic()
        circles = await duel_circles(self.client)
        options = []
        for e in safe:
            loc = await e.location()
            if not away_from([(loc.x, loc.y, loc.z)], circles, self.safe_distance + CIRCLE_EXTRA):
                continue  # beside a fight going on (another player's): walking in joins it
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
        from .safe_teleport import teleport_aborted

        if teleport_aborted(self.client):
            return True  # enemies by the item: tried (it's skipped for a while), don't walk in
        await asyncio.sleep(0.8)
        await self.client.goto(spot.x, spot.y)
        await asyncio.sleep(0.3)
        await press_interact()
        return True


ROUTE_STEP = 550.0  # walkway points this close (and ROUTE_RISE in height) are linked
ROUTE_RISE = 300.0


def walk_route(points: list, start, goal, step: float = ROUTE_STEP, rise: float = ROUTE_RISE) -> list:
    """Waypoints from `start` to `goal` over the zone's walkway points: the
    shortest chain of points each within `step` of the next and at most `rise`
    higher or lower (so a staircase is climbed a few steps at a time, not
    through the floor above). [] when no chain links them."""
    import heapq

    nodes = [tuple(start), *[tuple(p) for p in points], tuple(goal)]
    n = len(nodes)

    def linked(a, b) -> bool:
        return abs(a[2] - b[2]) <= rise and math.dist(a[:2], b[:2]) <= step

    dist = {0: 0.0}
    prev: dict[int, int] = {}
    heap = [(0.0, 0)]
    while heap:
        d, i = heapq.heappop(heap)
        if i == n - 1:
            break
        if d > dist.get(i, float("inf")):
            continue
        for j in range(n):
            if j != i and linked(nodes[i], nodes[j]):
                nd = d + math.dist(nodes[i], nodes[j])
                if nd < dist.get(j, float("inf")):
                    dist[j], prev[j] = nd, i
                    heapq.heappush(heap, (nd, j))
    if n - 1 not in dist:
        return []
    route, i = [], n - 1
    while i != 0:
        route.append(nodes[i])
        i = prev[i]
    return route[::-1]
