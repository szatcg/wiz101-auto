"""Remembers dungeons the bot has entered and which boss lives in which one.

Learned while questing (every sigil entry, every boss fight) and saved to
state/dungeons.json, so boss farming needs no coordinates from the user.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path

Point = tuple[float, float, float]

# Open battlefields behind a sigil: entered like a dungeon (the sigil is
# remembered for travel), but enemies wander and nothing is lost by fleeing
# or leaving to heal, so they're played like the open world. Crimson Fields:
# the bot fought every Otomo Scout it met and searched for War Oni at 30%
# health, treating it as a dungeon.
OPEN_ZONES = frozenset({"MooShu/MS_War/MS_War_BattlefieldA"})
# Zones of a dungeon instance that aren't under its rooms' area (reached by a
# portal inside it): zone -> the dungeon's first room. The Grand Chasm's past
# is the Hall of Time's ('Back to the Beginning'); taken for outside, the
# main quest was ranked first and the bot tried to Recall out of the instance.
INSTANCE_ZONES = {
    "DragonSpire/DS_A1_Knowledge/DS_A1Z4_GrandChasm_Past":
        "DragonSpire/DS_A1_Knowledge/Interiors/DS_Chasm_HallOfTime",
}


def is_open_zone(zone: str) -> bool:
    return zone in OPEN_ZONES


# Zones Recall can't bring us back into (the Death Realm, MooShu's Spirit
# World: "You cannot teleport to that location"): no leaving them to heal,
# fight on (wisps in the room, else a potion); the mark stays at the
# entrance. Learned from a Recall refusal into a dungeon (the player's rule)
# unless the message says its timer ended (that copy is gone, not barred);
# kept in state/no_return_zones.json.
NO_RETURN_FILE = Path("state") / "no_return_zones.json"
NO_RETURN_WORDS = ("spiritworld",)


def refusal_means_no_return(message: str) -> bool:
    """A Recall refusal that bars the dungeon (not one whose timer ran out)."""
    low = (message or "").lower()
    return "cannot teleport" in low and "timer" not in low and "reset" not in low


def learn_no_return(zone: str) -> bool:
    """Remember `zone` as one Recall can't come back into. True if new."""
    if not zone or no_return(zone):
        return False
    try:
        known = json.loads(NO_RETURN_FILE.read_text(encoding="utf-8")) if NO_RETURN_FILE.exists() else []
    except (OSError, ValueError):
        known = []
    known.append(zone)
    try:
        NO_RETURN_FILE.parent.mkdir(exist_ok=True)
        NO_RETURN_FILE.write_text(json.dumps(sorted(set(known)), indent=1), encoding="utf-8")
    except OSError:
        return False
    return True


def no_return(zone: str) -> bool:
    low = zone.lower().replace("_", "")
    if any(w in low for w in NO_RETURN_WORDS):
        return True
    try:
        return zone in json.loads(NO_RETURN_FILE.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return False


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
