"""Teleport spots that worked, per zone (state/teleport_spots.json).

A teleport often takes a few tries: the spot is off the map, inside
collision, or by enemies. Every jump that lands where it aimed (no fight, no
jump back) is remembered, and those spots are used again: as known ground
when a target looks off the map, and as the first thing to try near a target
whose teleport was refused.
"""

from __future__ import annotations

import json
import math
from pathlib import Path

SPOTS_FILE = Path("state") / "teleport_spots.json"
SAME_SPOT = 150.0  # closer than this to a saved spot: the same one
MAX_PER_ZONE = 400


class TeleportSpots:
    def __init__(self, path: Path = SPOTS_FILE):
        self.path = path
        self.spots: dict[str, list[list[float]]] = {}
        try:
            self.spots = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            pass

    def add(self, zone: str, point: tuple[float, float, float]) -> bool:
        """Remember a spot a teleport landed on. True if it was new."""
        if not zone:
            return False
        have = self.spots.setdefault(zone, [])
        if any(math.dist(point[:2], p[:2]) < SAME_SPOT and abs(point[2] - p[2]) < 200 for p in have):
            return False
        have.append([round(point[0]), round(point[1]), round(point[2])])
        del have[:-MAX_PER_ZONE]
        self.save()
        return True

    def near(self, zone: str, point: tuple[float, float, float], within: float,
             height: float = 300.0) -> list[tuple[float, float, float]]:
        """Saved spots within `within` of `point` (and near its height), nearest first."""
        out = [
            tuple(p) for p in self.spots.get(zone, [])
            if math.dist(p[:2], point[:2]) < within and abs(p[2] - point[2]) < height
        ]
        return sorted(out, key=lambda p: math.dist(p[:2], point[:2]))

    def all(self, zone: str) -> list[tuple[float, float, float]]:
        return [tuple(p) for p in self.spots.get(zone, [])]

    def save(self):
        try:
            self.path.parent.mkdir(exist_ok=True)
            self.path.write_text(json.dumps(self.spots), encoding="utf-8")
        except OSError:
            pass


_shared: TeleportSpots | None = None


def spots() -> TeleportSpots:
    global _shared
    if _shared is None:
        _shared = TeleportSpots()
    return _shared
