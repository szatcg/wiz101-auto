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


# Doors placed from the logs (the bot walked them): known before any walk.
# zone -> [(door x, y), (approach x, y, z), destination zone]
KNOWN_DOORS = {
    "Aquila/AQ_Z01_MountOlympus": [
        [[6681, 8306], [6523, 8137, -10], "Aquila/Interiors/AQ_Z01_Apollo_Room"],  # the Sun Chamber
        [[-6687, 8305], [-6438, 7969, -10], "Aquila/Interiors/AQ_Z01_ArtemusRoom"],  # the Moon Chamber
        [[3, 13710], [98, 13499, -1291], "Aquila/Interiors/AQ_Z01_HallOfWatchfulEye"],
    ],
}


class DoorMemory:
    """zone -> [(door x, y), (approach x, y, z), destination zone or None]:
    where walking in worked, and where it led (so a boss's room can be routed
    to through known doors instead of guessed at)."""

    def __init__(self, path: Path = DOOR_FILE):
        self.path = path
        self.doors: dict[str, list[list]] = {}
        try:
            self.doors = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            pass
        for zone, entries in KNOWN_DOORS.items():
            for door, spot, dest in entries:
                mine = next((e for e in self.doors.get(zone, []) if _dist(e[0], door) < DOOR_MATCH), None)
                if mine is None:
                    self.doors.setdefault(zone, []).append([door, spot, dest])
                elif len(mine) < 3 or not mine[2]:
                    mine[2:] = [dest]

    def approach(self, zone: str, door: Point) -> Point | None:
        for e in self.doors.get(zone, []):
            if _dist(e[0], door) < DOOR_MATCH:
                return tuple(e[1])
        return None

    def leading_to(self, zone: str, dest: str) -> list[tuple[Point, Point]]:
        """(door, approach) of this zone's known doors into `dest`."""
        return [(tuple(e[0]), tuple(e[1])) for e in self.doors.get(zone, []) if len(e) > 2 and e[2] == dest]

    def route(self, start: str, dest: str) -> list[tuple[str, Point, Point, str]]:
        """The shortest chain of known doors from `start` to `dest`: (zone,
        door, approach, next zone) per hop; [] when none is known."""
        from collections import deque

        queue = deque([(start, [])])
        seen = {start}
        while queue:
            zone, path = queue.popleft()
            if zone == dest:
                return path
            for e in self.doors.get(zone, []):
                nxt = e[2] if len(e) > 2 else None
                if nxt and nxt not in seen:
                    seen.add(nxt)
                    queue.append((nxt, path + [(zone, tuple(e[0]), tuple(e[1]), nxt)]))
        return []

    def record(self, zone: str, door: Point, start: Point, dest: str | None = None):
        old = next((e for e in self.doors.get(zone, []) if _dist(e[0], door) < DOOR_MATCH), None)
        dest = dest or (old[2] if old and len(old) > 2 else None)
        entries = [e for e in self.doors.get(zone, []) if _dist(e[0], door) >= DOOR_MATCH]
        spot = [round(start[0]), round(start[1]), round(start[2])]
        entries.append([[round(door[0]), round(door[1])], spot, dest])
        self.doors[zone] = entries
        try:
            self.path.parent.mkdir(exist_ok=True)
            self.path.write_text(json.dumps(self.doors), encoding="utf-8")
        except OSError:
            pass

    def forget(self, zone: str, dest: str) -> int:
        """Drop `zone`'s learned doors into `dest` (a wrong way: the NPC
        wasn't behind it). Returns how many."""
        entries = self.doors.get(zone, [])
        keep = [e for e in entries if not (len(e) > 2 and e[2] == dest)]
        if len(keep) == len(entries):
            return 0
        self.doors[zone] = keep
        try:
            self.path.write_text(json.dumps(self.doors), encoding="utf-8")
        except OSError:
            pass
        return len(entries) - len(keep)


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
