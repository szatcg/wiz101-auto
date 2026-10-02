"""The route the bot means to take to the objective's zone, zone by zone,
for the stream page's navigation graph (state/route.json).

It follows the bot's own travel rules: Go Home to Wizard City from another
world (dorm, then Ravenwood), the World Tree's Spiral Map out of Wizard City
(landing where that world's map trips land), the hub button when the hub is
nearer the objective than here, else the gate path.
"""

from __future__ import annotations

import json
import re
import time
from pathlib import Path

ROUTE_FILE = Path("state") / "route.json"
ARRIVALS_FILE = Path("state") / "world_arrivals.json"  # world -> zone a Spiral Map trip landed in

DORM = "WizardCity/Interiors/WC_Housing_Dorm_Interior"
RAVENWOOD = "WizardCity/WC_Ravenwood"
WORLD_TREE = "WizardCity/WC_Ravenwood_Teleporter"

# Names for zones the game's list lacks or names oddly.
NICE_NAMES = {
    DORM: "Dorm",
    RAVENWOOD: "Ravenwood",
    WORLD_TREE: "World Tree",
    "WizardCity/WC_Hub": "The Commons",
    "WizardCity/Interiors/WC_SchoolMyth": "Myth School",
    "Krokotopia/KT_WorldTeleporter": "Krokotopia Gate",
}


def gate_path(start: str, dest: str, gates: dict) -> list[str] | None:
    """Zones along the shortest gate path, both ends included (None if no
    known route)."""
    if start == dest:
        return [start]
    parent: dict[str, str] = {start: ""}
    frontier = [start]
    while frontier:
        nxt = []
        for zone in frontier:
            for _pos, to_zone in gates.get(zone, []):
                if to_zone in parent:
                    continue
                parent[to_zone] = zone
                if to_zone == dest:
                    path = [dest]
                    while parent[path[-1]]:
                        path.append(parent[path[-1]])
                    return path[::-1]
                nxt.append(to_zone)
        frontier = nxt
    return None


def _walk(start: str, dest: str, gates: dict) -> list[str]:
    return gate_path(start, dest, gates) or [start, dest]


def plan_route(here: str, dest: str, gates: dict, hub: str | None = None,
               arrival: str | None = None, final: str | None = None) -> list[str]:
    """Zone ids from `here` to `dest` (then `final`, a room off `dest` where
    the objective is), as the bot will travel; [] when already there.
    `hub`: this world's hub (the hub button); `arrival`: where a Spiral Map
    trip to `dest`'s world lands."""
    end = final or dest
    if not here or not dest or here == end:
        return []
    world, dest_world = here.split("/")[0], dest.split("/")[0]
    if dest_world != world:
        if dest_world == "WizardCity":
            route = [here, DORM, RAVENWOOD] + _walk(RAVENWOOD, dest, gates)[1:]
        else:
            to_tree = ([here, DORM, RAVENWOOD] if world != "WizardCity"
                       else _walk(here, RAVENWOOD, gates)) if here != WORLD_TREE else []
            land = arrival or dest
            route = to_tree + [WORLD_TREE] + _walk(land, dest, gates)
    else:
        route = _walk(here, dest, gates)
        if hub and here != hub:
            via = gate_path(hub, dest, gates)
            if via is not None and len(via) < len(route):  # (the hub nearer: its button)
                route = [here] + via
    if final and final != route[-1]:
        route.append(final)
    out: list[str] = []
    for z in route:  # (no repeats: the dorm's door is Ravenwood's)
        if not out or out[-1] != z:
            out.append(z)
    return out


def zone_label(zone: str, display: list[tuple[str, str]]) -> str:
    """A zone's name for the graph: ours, the game's list, else its id tidied
    ("DS_A1Hub_Library" -> "Library")."""
    if zone in NICE_NAMES:
        return NICE_NAMES[zone]
    name = next((n for n, z in display if z == zone), None)
    if name:
        return name.title().replace("'S", "'s")
    last = zone.split("/")[-1]
    last = re.sub(r"^[A-Z]{2}_(A\d+(Z\d+|Hub)?_)?", "", last)
    last = re.sub(r"([a-z])([A-Z])", r"\1 \2", last).replace("_", " ").strip()
    return last or zone


def note_arrival(world: str, zone: str):
    """Where a Spiral Map trip into `world` landed."""
    try:
        data = json.loads(ARRIVALS_FILE.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        data = {}
    if data.get(world) != zone:
        data[world] = zone
        ARRIVALS_FILE.parent.mkdir(exist_ok=True)
        ARRIVALS_FILE.write_text(json.dumps(data, indent=1), encoding="utf-8")


def arrival_for(world: str) -> str | None:
    try:
        return json.loads(ARRIVALS_FILE.read_text(encoding="utf-8")).get(world)
    except (OSError, ValueError):
        return None


def write_route(route: list[str], objective: str, display: list[tuple[str, str]], at: int = 0):
    """The route for the stream page (`at`: the zone we're in); an empty one
    clears it."""
    data = {
        "time": time.time(),
        "objective": objective,
        "at": at,
        "zones": [{"id": z, "name": zone_label(z, display)} for z in route],
    }
    try:
        ROUTE_FILE.parent.mkdir(exist_ok=True)
        ROUTE_FILE.write_text(json.dumps(data, indent=1), encoding="utf-8")
    except OSError:
        pass
