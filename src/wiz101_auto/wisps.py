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


def _dist(a: Point, b: Point) -> float:
    return math.dist(a, b)


@dataclass
class WispMemory:
    path: Path = Path("state") / "wisps.json"
    spots: dict[str, list[Point]] = field(default_factory=dict)
    _visited: dict[tuple[str, Point], float] = field(default_factory=dict)

    @classmethod
    def load(cls, path: Path | None = None) -> WispMemory:
        mem = cls(path or cls.path)
        try:
            raw = json.loads(mem.path.read_text(encoding="utf-8"))
            mem.spots = {z: [tuple(p) for p in pts] for z, pts in raw.items()}
        except Exception:
            pass
        return mem

    def save(self):
        self.path.parent.mkdir(exist_ok=True)
        self.path.write_text(json.dumps(self.spots, indent=1), encoding="utf-8")

    def record(self, zone: str, points: list[Point]) -> int:
        """Add newly seen spawn points. Returns how many were new."""
        known = self.spots.setdefault(zone, [])
        added = 0
        for p in points:
            p = (round(p[0], 1), round(p[1], 1), round(p[2], 1))
            if all(_dist(p, k) > MERGE_DISTANCE for k in known):
                known.append(p)
                added += 1
        return added

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
    ) -> Point | None:
        """Closest known spawn point that's away from mobs and not checked recently."""
        now = time.monotonic() if now is None else now
        options = [
            s
            for s in self.spots.get(zone, [])
            if all(_dist(s, m) > safe_distance for m in mobs)
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
