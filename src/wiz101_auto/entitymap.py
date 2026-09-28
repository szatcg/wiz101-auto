"""What the wizard has seen where: faster searches and door approaches.

Teleports are instant, so a search costs the number of stops. Two memories
cut them down:

- the entity map (state/entity_map.json): every named enemy, NPC and object
  seen, by zone, with its positions (close sightings merged). Looking for
  "Sokkwi" or "Gemstones" goes to the spots they were seen at first, nearest
  first, and only then sweeps the zone;
- door memory (state/doors.json): where a walk through a door (a quest
  marker the game won't let us teleport onto) started from. The next time,
  the wizard lands there and walks in, instead of trying spots around it.

The data structures are pure (unit tested); `scan` reads the game.
"""

from __future__ import annotations

import json
import math
import time
from collections.abc import Callable
from pathlib import Path

from loguru import logger

ENTITY_FILE = Path("state") / "entity_map.json"
DOOR_FILE = Path("state") / "doors.json"
MERGE_DISTANCE = 400.0  # sightings closer than this are the same spot
MAX_SPOTS = 12  # per name per zone
DOOR_MATCH = 300.0  # a marker this close to a known door is that door
_NOISE = ("basic ", "cinematic", "player object", "player stand in", "dynatrigger", "camera", "wisp")

Point = tuple[float, float, float]


def _dist(a, b) -> float:
    return math.dist((a[0], a[1]), (b[0], b[1]))


class EntityMap:
    def __init__(self, path: Path = ENTITY_FILE):
        self.path = path
        self.zones: dict[str, dict[str, list[list[float]]]] = {}
        self._dirty_since = 0.0
        try:
            self.zones = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            pass

    def record(self, zone: str, name: str, pos: Point) -> bool:
        """Note `name` seen at `pos`. True if it's a new spot."""
        name = name.strip()
        if not zone or not name or name.lower().startswith(_NOISE):
            return False
        spots = self.zones.setdefault(zone, {}).setdefault(name, [])
        if any(_dist(s, pos) < MERGE_DISTANCE for s in spots):
            return False
        spots.append([round(pos[0]), round(pos[1]), round(pos[2])])
        del spots[:-MAX_SPOTS]
        self._dirty_since = self._dirty_since or time.monotonic()
        return True

    def spots(self, zone: str, matches: Callable[[str], bool], start: Point) -> list[Point]:
        """Known spots of anything in `zone` whose name `matches`, nearest first."""
        found = [
            tuple(s) for name, spots in self.zones.get(zone, {}).items() if matches(name) for s in spots
        ]
        return sorted(found, key=lambda s: _dist(s, start))

    def save(self, force: bool = False):
        if not self._dirty_since or (not force and time.monotonic() - self._dirty_since < 20):
            return
        try:
            self.path.parent.mkdir(exist_ok=True)
            self.path.write_text(json.dumps(self.zones), encoding="utf-8")
            self._dirty_since = 0.0
        except OSError:
            pass


class DoorMemory:
    """zone -> [(door x, y), (approach x, y, z)]: where walking in worked."""

    def __init__(self, path: Path = DOOR_FILE):
        self.path = path
        self.doors: dict[str, list[list[list[float]]]] = {}
        try:
            self.doors = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            pass

    def approach(self, zone: str, door: Point) -> Point | None:
        for (dx, dy), spot in self.doors.get(zone, []):
            if _dist((dx, dy), door) < DOOR_MATCH:
                return tuple(spot)
        return None

    def record(self, zone: str, door: Point, start: Point):
        entries = [e for e in self.doors.get(zone, []) if _dist(e[0], door) >= DOOR_MATCH]
        spot = [round(start[0]), round(start[1]), round(start[2])]
        entries.append([[round(door[0]), round(door[1])], spot])
        self.doors[zone] = entries
        try:
            self.path.parent.mkdir(exist_ok=True)
            self.path.write_text(json.dumps(self.doors), encoding="utf-8")
        except OSError:
            pass


async def scan(client, zone: str, emap: EntityMap) -> int:
    """Record every named thing around the wizard. Returns new spots."""
    from .names import lang_name

    new = 0
    try:
        entities = await client.get_base_entity_list()
    except Exception:
        return 0
    for e in entities:
        try:
            t = await e.object_template()
            if not t:
                continue
            code = await t.display_name()
            name = (await lang_name(client, code) if code else "") or (await t.object_name() or "")
            pos = await e.location()
            new += emap.record(zone, name, (pos.x, pos.y, pos.z))
        except Exception:
            continue
    if new:
        logger.debug(f"entity map: {new} new spot(s) in {zone}")
    emap.save()
    return new
