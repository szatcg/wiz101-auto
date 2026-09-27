"""Static travel data for objectives the quest helper gives no marker for.

Some objectives never get a compass marker, e.g. Prospector Zeke's "Go To
Golem Court Smith in Golem Court": `quest_position` stays (0, 0, 0) in the hub
and in Golem Court itself. WizSprinter (bundled with WizWalker, from Deimos)
ships data files with the walk-through gates between zones, zone display
names, and the spots of Zeke's/Eloise's hidden quest targets in each zone.
"""

from __future__ import annotations

from pathlib import Path

import wizwalker
from wizwalker import XYZ

_TRAVERSAL_DIR = Path(wizwalker.__file__).parent / "extensions" / "wizsprinter" / "traversalData"

Gates = dict[str, list[tuple[XYZ, str]]]
DisplayZones = list[tuple[str, str]]
Spots = dict[str, list[XYZ]]


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
    ("ravenwood", "WizardCity/WC_Ravenwood"),
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
    return _cache


def find_zone_gate(
    objective: str, current_zone: str, blocked: set[tuple[str, str]] = frozenset()
) -> tuple[XYZ, str] | None:
    gates, display_zones, _ = _data()
    return find_gate(objective, current_zone, gates, display_zones, blocked)


def gate_toward(current_zone: str, dest: str, blocked: set[tuple[str, str]] = frozenset()):
    return first_hop_toward(current_zone, dest, _data()[0], blocked)


def objective_zone(objective: str) -> str | None:
    return named_zone(objective, _data()[1])


def quest_spots(zone: str) -> list[XYZ]:
    """Known hidden quest-target spots (Zeke, Eloise) in `zone`."""
    return _data()[2].get(zone, [])
