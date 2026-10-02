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
DONE_FILE = Path("state") / "dungeons_done.json"  # dungeons finished (first room zones)


def load_done(path: Path = DONE_FILE) -> set[str]:
    """Dungeons the bot finished: entering one again for another quest (a
    spell quest's boss in the Labyrinth) doesn't make its own quest come first."""
    try:
        return set(json.loads(path.read_text(encoding="utf-8")))
    except (OSError, ValueError):
        return set()


def mark_done(first_room: str, path: Path = DONE_FILE) -> bool:
    """Note a dungeon as finished. True if it's new."""
    done = load_done(path)
    if not first_room or first_room in done:
        return False
    path.parent.mkdir(exist_ok=True)
    path.write_text(json.dumps(sorted(done | {first_room}), indent=1), encoding="utf-8")
    return True


EXITS_FILE = Path("state") / "dungeon_exits.json"  # dungeon room -> spots that took us out


def dungeon_exits(room: str, path: Path = EXITS_FILE) -> list[tuple[float, float, float]]:
    """Spots in a dungeon room where a landing took us out of the dungeon."""
    try:
        return [tuple(p) for p in json.loads(path.read_text(encoding="utf-8")).get(room, [])]
    except (OSError, ValueError, AttributeError):
        return []


def add_exit(room: str, spot: tuple[float, float, float], path: Path = EXITS_FILE) -> bool:
    """Remember a spot that took us out of `room` (the Haunted Cave's cave
    room: a landing clear of the Blood Bats was on its exit, and the dungeon
    and its quest were gone). True if it's new."""
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        data = {}
    spots = data.setdefault(room, [])
    if any(abs(s[0] - spot[0]) < 150 and abs(s[1] - spot[1]) < 150 for s in spots):
        return False
    spots.append([round(c, 1) for c in spot])
    path.parent.mkdir(exist_ok=True)
    path.write_text(json.dumps(data, indent=1), encoding="utf-8")
    return True


ZONE_BOSSES_FILE = Path("state") / "zone_bosses.json"  # zone -> bosses fought there (any zone)


def zone_bosses(zone: str, path: Path = ZONE_BOSSES_FILE) -> list[str]:
    try:
        return list(json.loads(path.read_text(encoding="utf-8")).get(zone, []))
    except (OSError, ValueError, AttributeError):
        return []


def note_zone_boss(zone: str, name: str, path: Path = ZONE_BOSSES_FILE) -> bool:
    """A boss fought in `zone` (outside dungeons too: Gurtok Firebender in
    the volcano, whose fight a locked door waits on). True if it's new."""
    if not zone or not name:
        return False
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        data = {}
    names = data.setdefault(zone, [])
    if name in names:
        return False
    names.append(name)
    path.parent.mkdir(exist_ok=True)
    path.write_text(json.dumps(data, indent=1), encoding="utf-8")
    return True


LAST_FIGHTS_FILE = Path("state") / "last_fights.json"  # zone -> where our last fight there was


def note_last_fight(zone: str, pos: tuple[float, float, float], path: Path = LAST_FIGHTS_FILE):
    if not zone:
        return
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        data = {}
    data[zone] = [round(c, 1) for c in pos]
    path.parent.mkdir(exist_ok=True)
    path.write_text(json.dumps(data, indent=1), encoding="utf-8")


def last_fight(zone: str, path: Path = LAST_FIGHTS_FILE) -> tuple[float, float, float] | None:
    """Where we last fought in `zone` (walkable ground near its enemies)."""
    try:
        p = json.loads(path.read_text(encoding="utf-8")).get(zone)
        return (float(p[0]), float(p[1]), float(p[2])) if p else None
    except (OSError, ValueError, AttributeError, TypeError, IndexError):
        return None


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
