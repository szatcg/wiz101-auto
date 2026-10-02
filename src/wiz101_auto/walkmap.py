"""Collision-aware landing spots, from the zone's own collision geometry.

Every teleport goes through `landing()`: the zone's collision.bcd (read from
its WAD once per zone) gives the walkable ground and the walls, and the spot
nearest the destination that a wizard can stand on, clear of walls, at the
ground's real height, is used instead (the target itself when it's clear).
Teleports into walls, the inside of a forge or a door frame were refused or
bounced before; snapping only moves a landing a little (SNAP_MAX), so a far
answer (a gap in the geometry) leaves the destination as it was.

The solver is vendored from Deimos (geo/: collision.py by PeechezNCreem,
ISC; collision_math.py, GPL-3.0).
"""

from __future__ import annotations

import asyncio
import math

from loguru import logger
from wizwalker import XYZ

SNAP_MAX = 600.0  # never move a landing further than this
RETREAT_STEPS = (70.0, 140.0, 230.0, 350.0, 520.0)  # a refused jump: back toward the start by these (Deimos)
LOAD_TIMEOUT = 15.0  # seconds to read and index a zone's collision before going without it

_worlds: dict[str, object | None] = {}
_failed_logged: set[str] = set()


def _load(zone: str):
    """The zone's CollisionWorld, or None (no WAD, no collision.bcd)."""
    from wizwalker import Wad

    from .geo.bcd import CollisionWorld

    async def read() -> bytes:
        wad = Wad.from_game_data(zone.replace("/", "-"))
        return await wad.get_file("collision.bcd")

    data = asyncio.run(read())  # (in a worker thread: its own event loop)
    world = CollisionWorld()
    world.load(data)
    return world


async def world_for(zone: str):
    if zone in _worlds:
        return _worlds[zone]
    try:
        world = await asyncio.wait_for(asyncio.to_thread(_load, zone), LOAD_TIMEOUT)
    except Exception as exc:
        world = None
        if zone not in _failed_logged:
            _failed_logged.add(zone)
            logger.debug(f"walkmap: no collision for {zone}: {exc!r}")
    _worlds[zone] = world
    return world


async def landing(client, xyz: XYZ, strict: bool = False) -> XYZ:
    """The walkable spot to land on for `xyz` (xyz itself if nothing better)."""
    try:
        zone = await client.zone_name() or ""
    except Exception:
        return xyz
    if not zone:
        return xyz
    world = await world_for(zone)
    if world is None:
        return xyz
    from .geo import walk

    try:
        point, why = await asyncio.to_thread(walk.find_walkable_teleport_point, world, zone, xyz, None, 45.0,
                                             walk.GRID_SPACING, strict)
    except Exception as exc:
        logger.debug(f"walkmap: solve failed at ({xyz.x:.0f}, {xyz.y:.0f}): {exc!r}")
        return xyz
    if point is None:
        return xyz
    moved = math.dist((point.x, point.y), (xyz.x, xyz.y))
    if moved > SNAP_MAX:
        logger.debug(f"walkmap: nearest walkable spot is {moved:.0f} away ({why}); keeping the destination")
        return xyz
    if moved > 5:
        logger.debug(f"walkmap: landing moved {moved:.0f} to walkable ground ({why})")
    return point


WALK_WAYPOINT_EVERY = 6  # hex nodes between the waypoints walked to (straight runs between)


async def walk_path(zone: str, start: XYZ, goal: XYZ) -> list[XYZ] | None:
    """Waypoints to walk from `start` to `goal` around the zone's walls (A*
    over its collision), or None (no collision data, or no way on foot)."""
    world = await world_for(zone)
    if world is None:
        return None
    from .geo import walk

    def solve():
        return walk.get_walk_grid(world, zone).find_walk_path(start, goal)

    try:
        path = await asyncio.wait_for(asyncio.to_thread(solve), LOAD_TIMEOUT)
    except Exception as exc:
        logger.debug(f"walkmap: no walk path ({exc!r})")
        return None
    if not path:
        return None
    picked = path[WALK_WAYPOINT_EVERY::WALK_WAYPOINT_EVERY]
    if not picked or picked[-1] != path[-1]:
        picked.append(path[-1])
    return [XYZ(x, y, z if z is not None else goal.z) for x, y, z in picked]


def retreat_points(dest: XYZ, start: XYZ) -> list[XYZ]:
    """Spots on the way back from `dest` toward `start` (a refused jump's fallbacks)."""
    dx, dy = start.x - dest.x, start.y - dest.y
    length = math.hypot(dx, dy)
    if length < 1:
        return []
    out = []
    for step in RETREAT_STEPS:
        if step >= length:
            break
        out.append(XYZ(dest.x + dx / length * step, dest.y + dy / length * step, dest.z))
    return out


# --- the zone's navigation graph: coverage for searches -----------------------

LOAD_RANGE = 3147.0  # entities load within this of the wizard (Deimos)
CHUNK = math.sqrt(2) * LOAD_RANGE  # a square this wide sits inside one load circle
UNDER_MAP = 550.0  # scouting this far below the ground: things load, nothing sees us

_nav: dict[str, list[tuple[float, float, float]]] = {}


def parse_nav(data: bytes) -> list[tuple[float, float, float]]:
    """The vertices of a zone.nav (format from Deimos's teleport_math.parse_nav_data,
    by starrfox, from navwiz: Boost licence): vertex count, max index, then
    x, y, z floats and the index per vertex (entries off the sequence are skipped)."""
    import struct

    out: list[tuple[float, float, float]] = []
    if len(data) < 6:
        return out
    _count, vmax, _unknown = struct.unpack_from("<hhh", data, 0)
    pos, idx = 6, 0
    while idx <= vmax - 1 and pos + 14 <= len(data):
        x, y, z, index = struct.unpack_from("<fffh", data, pos)
        pos += 14
        if index != idx:
            vmax -= 1
            continue
        out.append((x, y, z))
        idx += 1
    return out


async def nav_points(zone: str) -> list[tuple[float, float, float]]:
    if zone in _nav:
        return _nav[zone]

    def load() -> list[tuple[float, float, float]]:
        from wizwalker import Wad

        async def read() -> bytes:
            return await Wad.from_game_data(zone.replace("/", "-")).get_file("zone.nav")

        return parse_nav(asyncio.run(read()))

    try:
        points = await asyncio.wait_for(asyncio.to_thread(load), LOAD_TIMEOUT)
    except Exception as exc:
        logger.debug(f"walkmap: no nav graph for {zone}: {exc!r}")
        points = []
    _nav[zone] = points
    return points


def chunk_centers(points: list[tuple[float, float, float]], start: tuple[float, float],
                  side: float = CHUNK) -> list[tuple[float, float, float]]:
    """One spot per square of the zone that has nav points in it (at their
    mean height), nearest first and then each nearest the last: from these,
    everything in the zone has loaded once."""
    cells: dict[tuple[int, int], list[tuple[float, float, float]]] = {}
    for p in points:
        cells.setdefault((math.floor(p[0] / side), math.floor(p[1] / side)), []).append(p)
    centers = [((cx + 0.5) * side, (cy + 0.5) * side, sum(p[2] for p in ps) / len(ps))
               for (cx, cy), ps in cells.items()]
    tour, here = [], start
    while centers:
        nxt = min(centers, key=lambda c: math.dist(c[:2], here))
        centers.remove(nxt)
        tour.append(nxt)
        here = nxt[:2]
    return tour
