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
