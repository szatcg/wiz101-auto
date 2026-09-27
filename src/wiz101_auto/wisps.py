"""Remembers where health/mana wisps spawn in each zone.

Wisps respawn at fixed spots, so once we've seen a zone's wisps we can cycle
through those spots to heal quickly instead of waiting for regeneration.
The planning part is pure (plain tuples) so it can be unit tested.
"""

from __future__ import annotations

import json
import math
import time
from dataclasses import dataclass, field
from pathlib import Path

Point = tuple[float, float, float]

MERGE_DISTANCE = 150.0  # two sightings closer than this are the same spawn point
HEALTH, MANA, ANY = "health", "mana", "any"  # "any": a spot whose wisp kind we haven't seen
BOTH = frozenset({HEALTH, MANA})


def wisp_kind(name: str) -> str:
    """health/mana from an entity name (WC_WispHealth, a red/blue stand-in...)."""
    name = name.lower()
    if "health" in name or "red" in name:
        return HEALTH
    if "mana" in name or "blue" in name:
        return MANA
    return ANY


def usable(kind: str, need) -> bool:
    return kind == ANY or kind in need


def _dist(a: Point, b: Point) -> float:
    return math.dist(a, b)


@dataclass
class WispMemory:
    path: Path = Path("state") / "wisps.json"
    spots: dict[str, list[Point]] = field(default_factory=dict)
    kinds: dict[str, dict[Point, str]] = field(default_factory=dict)  # zone -> spot -> kind
    _visited: dict[tuple[str, Point], float] = field(default_factory=dict)

    @classmethod
    def load(cls, path: Path | None = None) -> WispMemory:
        mem = cls(path or cls.path)
        try:
            raw = json.loads(mem.path.read_text(encoding="utf-8"))
            for z, pts in raw.items():
                # [x, y, z] (older files) or [x, y, z, kind]
                mem.spots[z] = [tuple(p[:3]) for p in pts]
                mem.kinds[z] = {tuple(p[:3]): (p[3] if len(p) > 3 else ANY) for p in pts}
        except Exception:
            pass
        return mem

    def save(self):
        self.path.parent.mkdir(exist_ok=True)
        data = {z: [[*p, self.kind_of(z, p)] for p in pts] for z, pts in self.spots.items()}
        self.path.write_text(json.dumps(data, indent=1), encoding="utf-8")

    def kind_of(self, zone: str, spot: Point) -> str:
        return self.kinds.get(zone, {}).get(tuple(spot), ANY)

    def record(self, zone: str, points: list[Point], kind: str = ANY) -> int:
        """Add newly seen spawn points (a sighting of a known "any" spot tells us
        its kind). Returns how many changed."""
        known = self.spots.setdefault(zone, [])
        kinds = self.kinds.setdefault(zone, {})
        added = 0
        for p in points:
            p = (round(p[0], 1), round(p[1], 1), round(p[2], 1))
            same = [k for k in known if _dist(p, k) <= MERGE_DISTANCE]
            if not same:
                known.append(p)
                kinds[p] = kind
                added += 1
            elif kind != ANY and kinds.get(same[0], ANY) == ANY:
                kinds[same[0]] = kind
                added += 1
        return added

    def count(self, zone: str, need=BOTH) -> int:
        """Remembered spots in `zone` that can give what we need."""
        return sum(usable(self.kind_of(zone, s), need) for s in self.spots.get(zone, []))

    def forget(self, zone: str, spot: Point) -> bool:
        """Drop a remembered spot (e.g. a wisp that can't be reached). True if one was removed."""
        known = self.spots.get(zone, [])
        keep = [k for k in known if _dist(k, spot) > MERGE_DISTANCE]
        self.spots[zone] = keep
        return len(keep) < len(known)

    def mark_visited(self, zone: str, spot: Point, now: float | None = None):
        self._visited[(zone, spot)] = time.monotonic() if now is None else now

    def next_spot(
        self,
        zone: str,
        me: Point,
        mobs: list[Point],
        *,
        safe_distance: float,
        cooldown: float = 90.0,
        now: float | None = None,
        need=BOTH,
    ) -> Point | None:
        """Closest known spawn point of a kind we need, away from mobs and not
        checked recently."""
        now = time.monotonic() if now is None else now
        options = [
            s
            for s in self.spots.get(zone, [])
            if usable(self.kind_of(zone, s), need)
            and all(_dist(s, m) > safe_distance for m in mobs)
            and now - self._visited.get((zone, s), -1e9) > cooldown
        ]
        return min(options, key=lambda s: _dist(s, me)) if options else None


def sweep_points(center: Point, mobs: list[Point], safe_distance: float) -> list[Point]:
    """Points in rings around `center` for discovering wisps in an unknown zone."""
    points = []
    for radius in (1500.0, 3000.0):
        for i in range(8):
            a = i * math.pi / 4
            p = (center[0] + radius * math.cos(a), center[1] + radius * math.sin(a), center[2])
            if all(_dist(p, m) > safe_distance for m in mobs):
                points.append(p)
    return points
