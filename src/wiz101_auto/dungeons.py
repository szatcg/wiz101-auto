"""Remembers dungeons the bot has entered and which boss lives in which one.

Learned while questing (every sigil entry, every boss fight) and saved to
state/dungeons.json, so boss farming needs no coordinates from the user.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path

Point = tuple[float, float, float]


@dataclass
class DungeonEntry:
    outside: str  # zone the sigil is in
    sigil: Point
    spawn: Point  # where the wizard appears inside
    yaw: float  # which way it faces on arrival (the exit is behind it)


@dataclass
class DungeonMemory:
    path: Path = Path("state") / "dungeons.json"
    dungeons: dict[str, DungeonEntry] = field(default_factory=dict)  # interior zone -> entry
    bosses: dict[str, str] = field(default_factory=dict)  # boss name -> interior zone

    @classmethod
    def load(cls, path: Path | None = None) -> DungeonMemory:
        mem = cls(path or cls.path)
        try:
            raw = json.loads(mem.path.read_text(encoding="utf-8"))
            mem.dungeons = {
                z: DungeonEntry(d["outside"], tuple(d["sigil"]), tuple(d["spawn"]), d["yaw"])
                for z, d in raw.get("dungeons", {}).items()
            }
            mem.bosses = dict(raw.get("bosses", {}))
        except Exception:
            pass
        return mem

    def save(self):
        self.path.parent.mkdir(exist_ok=True)
        data = {
            "dungeons": {z: e.__dict__ for z, e in self.dungeons.items()},
            "bosses": self.bosses,
        }
        self.path.write_text(json.dumps(data, indent=1), encoding="utf-8")

    def record_entry(self, interior: str, entry: DungeonEntry):
        self.dungeons[interior] = entry
        self.save()

    def record_boss(self, boss: str, interior: str) -> bool:
        if self.bosses.get(boss) == interior:
            return False
        self.bosses[boss] = interior
        self.save()
        return True

    def find_for_boss(self, boss: str) -> tuple[str, DungeonEntry] | None:
        """The dungeon a boss was seen in (name matched loosely), if we've been there."""
        want = boss.strip().lower()
        for name, interior in self.bosses.items():
            if want and (want in name.lower() or name.lower() in want) and interior in self.dungeons:
                return interior, self.dungeons[interior]
        return None
