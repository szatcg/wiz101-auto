"""Static travel data for objectives the quest helper gives no marker for.

Some objectives never get a compass marker, e.g. Prospector Zeke's "Go To
Golem Court Smith in Golem Court": `quest_position` stays (0, 0, 0) in the hub
and in Golem Court itself. WizSprinter (bundled with WizWalker, from Deimos)
ships data files with the walk-through gates between zones, zone display
names, and the spots of Zeke's/Eloise's hidden quest targets in each zone.
"""

from __future__ import annotations

import json
import math
import re
import time
from pathlib import Path

import wizwalker
from wizwalker import XYZ

_TRAVERSAL_DIR = Path(wizwalker.__file__).parent / "extensions" / "wizsprinter" / "traversalData"

Gates = dict[str, list[tuple[XYZ, str]]]
DisplayZones = list[tuple[str, str]]
Spots = dict[str, list[XYZ]]

# Gates the data files lack (e.g. Triton Avenue <-> Haunted Cave), learned while
# playing: arriving in a zone puts the wizard just in front of the way back.
LEARNED_GATES = Path("state") / "learned_gates.json"
GATE_BEHIND = 250.0  # the gate trigger sits about this far behind the arrival point


def gate_behind(pos: XYZ, yaw: float, dist: float = GATE_BEHIND) -> XYZ:
    """A point `dist` behind a wizard standing at `pos` facing `yaw`. The facing
    direction is found with WizWalker's own yaw function (the one `goto` uses),
    so no assumption about the game's angle convention is needed."""
    from wizwalker.utils import calculate_perfect_yaw

    def off(a: float) -> float:
        target = XYZ(pos.x + math.cos(a) * 1000, pos.y + math.sin(a) * 1000, pos.z)
        d = (calculate_perfect_yaw(pos, target) - yaw) % (2 * math.pi)
        return min(d, 2 * math.pi - d)

    facing = min((math.radians(deg) for deg in range(0, 360, 2)), key=off)
    return XYZ(pos.x - math.cos(facing) * dist, pos.y - math.sin(facing) * dist, pos.z)


def add_gate(gates: Gates, from_zone: str, to_zone: str, pos: XYZ) -> bool:
    """Add a gate unless one from `from_zone` to `to_zone` is known. True if added."""
    if any(to == to_zone for _p, to in gates.get(from_zone, [])):
        return False
    gates.setdefault(from_zone, []).append((pos, to_zone))
    return True


def _load_learned(gates: Gates):
    try:
        for frm, to, x, y, z in json.loads(LEARNED_GATES.read_text(encoding="utf-8")):
            add_gate(gates, frm, to, XYZ(x, y, z))
    except Exception:
        pass


_last_jump = [0.0]


def note_zone_jump():
    """A teleport between zones (hub button, Go Home, Recall) is about to
    happen: the arrival that follows is not a walked gate."""
    _last_jump[0] = time.monotonic()


def last_zone_jump() -> float:
    return _last_jump[0]


def learn_gate(from_zone: str, to_zone: str, pos: XYZ) -> bool:
    """Remember a gate found while playing (kept in state/learned_gates.json)."""
    if not add_gate(_data()[0], from_zone, to_zone, pos):
        return False
    try:
        known = json.loads(LEARNED_GATES.read_text(encoding="utf-8")) if LEARNED_GATES.exists() else []
        known.append([from_zone, to_zone, pos.x, pos.y, pos.z])
        LEARNED_GATES.parent.mkdir(exist_ok=True)
        LEARNED_GATES.write_text(json.dumps(known, indent=1), encoding="utf-8")
    except (OSError, ValueError):
        pass
    return True


def parse_gates(text: str) -> Gates:
    """`gates_list.txt` lines `type;x;y;z;from_zone;to_zone`, grouped by from_zone."""
    gates: Gates = {}
    for line in text.splitlines():
        parts = line.split(";")
        if len(parts) != 6:
            continue
        _kind, x, y, z, from_zone, to_zone = parts
        gates.setdefault(from_zone, []).append((XYZ(float(x), float(y), float(z)), to_zone))
    return gates


def parse_gate_kinds(text: str) -> dict[tuple[str, str], str]:
    """(from_zone, to_zone) -> gate type from `gates_list.txt`: "standard" is
    walked through; "xNoWait...", "dungeon" and "xSkipRide..." (boats) are
    used by pressing X at the spot."""
    kinds: dict[tuple[str, str], str] = {}
    for line in text.splitlines():
        parts = line.split(";")
        if len(parts) == 6:
            kinds.setdefault((parts[4], parts[5]), parts[0])
    return kinds


def press_x_gate(kind: str) -> bool:
    """Gates used with the X key (an NPC, a boat, a door that asks), not walked into."""
    return kind.startswith("x") or kind == "dungeon"


def ride_gate(kind: str) -> bool:
    """Gates that go through a ride zone first (a second X press skips the ride)."""
    return "skipride" in kind.lower()


def parse_display_zones(text: str) -> DisplayZones:
    """`displayZones.txt` lines `world;zone_id;display name` -> [(name, zone_id)], longest first."""
    zones = []
    for line in text.splitlines():
        parts = line.split(";")
        if len(parts) != 3:
            continue
        _world, zone_id, display_name = parts
        zones.append((display_name.strip().lower(), zone_id.strip()))
    zones.sort(key=lambda z: -len(z[0]))
    return zones


def parse_spots(text: str) -> Spots:
    """`objectLocations.txt` lines `kind;x;y;z;zone` (zekeObject, eloiseObject...), grouped by zone."""
    spots: Spots = {}
    for line in text.splitlines():
        parts = line.split(";")
        if len(parts) != 5:
            continue
        _kind, x, y, z, zone = parts
        spots.setdefault(zone.strip(), []).append(XYZ(float(x), float(y), float(z)))
    return spots


# Quest-text place names missing from displayZones.txt.
EXTRA_DISPLAY_ZONES = [
    ("the commons", "WizardCity/WC_Hub"),
    # The Grand Chasm's past, through the Hall of Time's portal: not the Grand Chasm itself.
    ("grand chasm past", "DragonSpire/DS_A1_Knowledge/DS_A1Z4_GrandChasm_Past"),
    ("ravenwood", "WizardCity/WC_Ravenwood"),
    ("the oasis", "Krokotopia/KT_Hub"),  # Krokotopia's hub (quest book / objective text)
    ("katzenstein's lab", "Marleybone/MB_ScotlandYard/MB_KatzLab"),  # a dungeon off Scotland Yard Roof
    ("knight's court", "Marleybone/MB_ScotlandYard/MB_KnightsCourt"),
    ("counterweight east", "Marleybone/MB_BigBen/MB_CounterweightEast"),  # a dungeon off the Museum
    ("nightside", "WizardCity/WC_NightSide"),  # off the Commons
    ("fireglobe theatre", "WizardCity/WC_Streets/Interiors/WC_Firecat_Theatre"),  # off Firecat Alley
    ("akori's chamber", "Krokotopia/KT_Pyramid/Interiors/KT_PalaceOfFire_T4"),  # Palace of Fire boss
    ("throne room of fire", "Krokotopia/KT_Pyramid/KT_ThroneRoom"),  # not MooShu's "throne room"
    ("altar of kings", "Krokotopia/KT_Pyramid/KT_AltarOfKings"),  # the data says "altar of the kings"
    ("hall of champions", "Krokotopia/KT_Krokosphinx/KT_ChampHall"),  # the data says "hall of champion's"
    ("wizard city library", "WizardCity/Interiors/WC_Library"),  # Harold Argleston (not in the data)
    ("djeserit family tomb", "Krokotopia/KT_Tomb/KT_DjeseritTomb"),  # the data says "djeserit tomb"
    ("ahnic family tomb", "Krokotopia/KT_Tomb/KT_AhnicTomb"),  # the data says "ahnic tomb"
    ("karanahn palace", "Krokotopia/KT_Tomb/KT_KaranahnPalace"),  # not in the data
]


def named_zone(objective: str, display_zones: DisplayZones) -> str | None:
    """Zone id of the place `objective` names ("... in Golem Court"), if known."""
    text = objective.lower()
    return next((zone_id for name, zone_id in display_zones if name in text), None)


def find_gate(
    objective: str,
    current_zone: str,
    gates: Gates,
    display_zones: DisplayZones,
    blocked: set[tuple[str, str]] = frozenset(),
) -> tuple[XYZ, str] | None:
    """First gate on the shortest gate path from `current_zone` to the place
    `objective` names, avoiding `blocked` (from_zone, to_zone) gates. None if it
    names no known place, the current zone, or a place no gate path reaches."""
    dest = named_zone(objective, display_zones)
    if dest is None:
        return None
    return first_hop_toward(current_zone, dest, gates, blocked)


def first_hop_toward(
    current_zone: str, dest: str, gates: Gates, blocked: set[tuple[str, str]] = frozenset()
) -> tuple[XYZ, str] | None:
    """First gate on the shortest gate path from `current_zone` to `dest`."""
    if dest == current_zone:
        return None
    first_hop: dict[str, tuple[XYZ, str]] = {}
    frontier = [current_zone]
    seen = {current_zone}
    while frontier:
        nxt = []
        for zone in frontier:
            for pos, to_zone in gates.get(zone, []):
                if to_zone in seen or (zone, to_zone) in blocked:
                    continue
                seen.add(to_zone)
                first_hop[to_zone] = (pos, to_zone) if zone == current_zone else first_hop[zone]
                if to_zone == dest:
                    return first_hop[to_zone]
                nxt.append(to_zone)
        frontier = nxt
    return None


def hop_count(current_zone: str, dest: str, gates: Gates) -> int | None:
    """Gate hops from `current_zone` to `dest` (0 if already there, None if unreachable)."""
    if dest == current_zone:
        return 0
    frontier, seen, hops = [current_zone], {current_zone}, 0
    while frontier:
        hops += 1
        nxt = []
        for zone in frontier:
            for _pos, to_zone in gates.get(zone, []):
                if to_zone == dest:
                    return hops
                if to_zone not in seen:
                    seen.add(to_zone)
                    nxt.append(to_zone)
        frontier = nxt
    return None


_cache: tuple[Gates, DisplayZones, Spots] | None = None


def _data() -> tuple[Gates, DisplayZones, Spots]:
    global _cache
    if _cache is None:
        display_text = (_TRAVERSAL_DIR / "displayZones.txt").read_text()
        display_zones = parse_display_zones(display_text) + EXTRA_DISPLAY_ZONES
        display_zones.sort(key=lambda z: -len(z[0]))
        _cache = (
            parse_gates((_TRAVERSAL_DIR / "gates_list.txt").read_text()),
            display_zones,
            parse_spots((_TRAVERSAL_DIR / "objectLocations.txt").read_text()),
        )
        _load_learned(_cache[0])
    return _cache


_kinds: dict[tuple[str, str], str] | None = None


GATE_KINDS_FILE = Path("state") / "gate_kinds.json"  # {"from|to": kind}: learned gates' kinds


def gate_kind(from_zone: str, to_zone: str) -> str:
    """A gate's kind: WizSprinter's list, else state/gate_kinds.json (the rope
    from Triton Avenue down into Crab Alley is "press X to jump", not a door
    walked into: walking at it ran into Haunted Minions again and again)."""
    global _kinds
    if _kinds is None:
        _kinds = parse_gate_kinds((_TRAVERSAL_DIR / "gates_list.txt").read_text())
        try:
            for key, kind in json.loads(GATE_KINDS_FILE.read_text(encoding="utf-8")).items():
                a, b = key.split("|", 1)
                _kinds[(a, b)] = kind
        except (OSError, ValueError):
            pass
    return _kinds.get((from_zone, to_zone), "standard")


def find_zone_gate(
    objective: str, current_zone: str, blocked: set[tuple[str, str]] = frozenset()
) -> tuple[XYZ, str] | None:
    gates, display_zones, _ = _data()
    return find_gate(objective, current_zone, gates, display_zones, blocked)


def gate_toward(current_zone: str, dest: str, blocked: set[tuple[str, str]] = frozenset()):
    return first_hop_toward(current_zone, dest, _data()[0], blocked)


def hops_to_place(current_zone: str, place: str) -> int | None:
    """Gate hops to a place named like the quest book shows it ("Triton Avenue")."""
    gates, display_zones, _ = _data()
    dest = named_zone(place, display_zones)
    return hop_count(current_zone, dest, gates) if dest else None


HUBS_FILE = Path("state") / "world_hubs.json"  # world -> the zone the hub button lands in (learned)
_HUB_NAME = re.compile(r"(^|_)Hub(_|$)")


def learn_hub(zone: str):
    """The hub button landed in `zone`: that's its world's hub."""
    try:
        data = json.loads(HUBS_FILE.read_text(encoding="utf-8")) if HUBS_FILE.exists() else {}
        if data.get(zone.split("/")[0]) != zone:
            data[zone.split("/")[0]] = zone
            HUBS_FILE.parent.mkdir(exist_ok=True)
            HUBS_FILE.write_text(json.dumps(data, indent=1), encoding="utf-8")
    except (OSError, ValueError):
        pass


def is_world_hub(zone: str) -> bool:
    """`zone` is its world's hub (Krokotopia/KT_Hub, Dragonspyre's
    DS_Hub_Cathedral: a heal trip from it is pointless, no wisps there)."""
    if not zone:
        return False
    return zone.split("/")[-1].endswith("_Hub") or zone == world_hub(zone)


def world_hub(zone: str, gates: Gates | None = None) -> str | None:
    """The hub of `zone`'s world: where the hub button landed before, else a
    zone named as one ("Krokotopia/KT_Hub", Dragonspyre's "DS_Hub_Cathedral";
    not "DS_A2Hub_Necropolis", an area hub)."""
    world = zone.split("/")[0]
    if gates is None:
        try:
            learned = json.loads(HUBS_FILE.read_text(encoding="utf-8")).get(world)
            if learned:
                return learned
        except (OSError, ValueError):
            pass
    zones = [z for z in (gates if gates is not None else _data()[0])
             if z.split("/")[0] == world and "/interiors/" not in z.lower()]
    exact = [z for z in zones if z.split("/")[-1].endswith("_Hub")]
    named = [z for z in zones if _HUB_NAME.search(z.split("/")[-1])]
    return (exact or named or [None])[0]


def hops_from_hub(zone: str, dest: str) -> int | None:
    """Gate hops to `dest` from the hub of `zone`'s world (a heal trip starts
    there); from `zone` itself if the world has no known hub."""
    return zone_hops(world_hub(zone) or zone, dest)


def zone_hops(current_zone: str, dest: str) -> int | None:
    """Gate hops between two zone ids (0 if the same, None if no known route)."""
    return hop_count(current_zone, dest, _data()[0])


def zones_around(start: str, gates: Gates, depth: int = 2) -> list[str]:
    """`start` and the zones up to `depth` gates away, nearest first; shops,
    houses and other interiors are left out (nothing to collect there)."""
    order, frontier, seen = [start], [start], {start}
    for _ in range(depth):
        nxt = []
        for zone in frontier:
            for _pos, to_zone in gates.get(zone, []):
                if to_zone in seen or "/interiors/" in to_zone.lower():
                    continue
                seen.add(to_zone)
                order.append(to_zone)
                nxt.append(to_zone)
        frontier = nxt
    return order


def zones_near(zone: str, depth: int = 2) -> list[str]:
    return zones_around(zone, _data()[0], depth)


def objective_zone(objective: str) -> str | None:
    return named_zone(objective, _data()[1])


def quest_spots(zone: str) -> list[XYZ]:
    """Known hidden quest-target spots (Zeke, Eloise) in `zone`."""
    return _data()[2].get(zone, [])
