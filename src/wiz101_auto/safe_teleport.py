"""Every teleport lands clear of enemies (and of fights going on).

Checking for enemies at each call site kept missing cases (a patrol walking
over a gem between the scan and the jump, a spot nobody checked), and each
miss is an unplanned fight. So the client's own `teleport` is wrapped: just
before moving it reads the enemies and duel circles afresh and, if the
destination is within LANDING_CLEARANCE of one, lands on the nearest clear
spot around it instead (the caller then walks on as usual). Teleports meant
to start a fight (going after a named enemy, a boss) are marked with
`allow_engage` and go straight there.

Enemies only load near the wizard, so from across a zone a spot can look
clear when it isn't. After landing, once they've streamed in, the check
runs again: an enemy too close means an immediate jump back to where the
wizard came from, before it engages.
"""

from __future__ import annotations

import asyncio
import time

from loguru import logger
from wizwalker import XYZ

LANDING_CLEARANCE = 700.0  # an enemy closer than this to the landing spot tends to start a fight
BLOCKED_TRIES = 3  # a destination with no clear spot around it: after this many skips, go anyway
ARRIVAL_CLEARANCE = 550.0  # after landing: an enemy this close sends us straight back
ARRIVAL_SETTLE = 0.35  # seconds for nearby entities to stream in after a jump


def teleport_aborted(client) -> bool:
    """Did the last teleport not happen, or jump back from enemies? Then the
    caller shouldn't walk on toward its target."""
    return bool(getattr(client, "_teleport_aborted", False))


def allow_engage(client, seconds: float = 6.0):
    """The next teleports (for `seconds`) are meant to start a fight."""
    client._engage_until = time.monotonic() + seconds


def install(client):
    """Wrap `client.teleport` with the landing check (once)."""
    if getattr(client, "_safe_teleport", False):
        return
    original = client.teleport
    blocked: dict[tuple[int, int], int] = {}

    async def teleport(xyz, *args, **kwargs):
        from .quest import clear_of, safe_landing
        from .upkeep import mob_positions

        client._teleport_aborted = False
        try:
            engaging = time.monotonic() < getattr(client, "_engage_until", 0.0)
            if engaging or await client.in_battle():
                return await original(xyz, *args, **kwargs)
            hazards = [XYZ(*m) for m in await mob_positions(client)]
            start = await client.body.position()
            if clear_of(xyz, hazards, LANDING_CLEARANCE):
                return await _arrive(xyz, start, args, kwargs)
            spot = safe_landing(xyz, start, hazards, LANDING_CLEARANCE)
            if spot is not None:
                logger.info(
                    f"enemies at the teleport spot ({xyz.x:.0f}, {xyz.y:.0f}); "
                    f"landing clear of them at ({spot.x:.0f}, {spot.y:.0f})"
                )
                return await _arrive(spot, start, args, kwargs)
            key = (round(xyz.x / 100), round(xyz.y / 100))
            blocked[key] = blocked.get(key, 0) + 1
            if blocked[key] <= BLOCKED_TRIES:
                logger.info(f"enemies all around ({xyz.x:.0f}, {xyz.y:.0f}); not teleporting there now")
                client._teleport_aborted = True
                return None
            blocked.pop(key, None)
            logger.info(f"({xyz.x:.0f}, {xyz.y:.0f}) stays surrounded by enemies; going anyway")
        except Exception as exc:
            logger.debug(f"teleport check failed ({exc!r}); teleporting as asked")
        return await original(xyz, *args, **kwargs)

    async def _arrive(dest, start, args, kwargs):
        """Jump, then look again now that the enemies there have loaded."""
        from .quest import clear_of
        from .upkeep import mob_positions

        result = await original(dest, *args, **kwargs)
        await asyncio.sleep(ARRIVAL_SETTLE)
        if await client.in_battle():
            return result
        here = await client.body.position()
        near = [XYZ(*m) for m in await mob_positions(client)]
        if not clear_of(here, near, ARRIVAL_CLEARANCE) and clear_of(start, near, ARRIVAL_CLEARANCE):
            logger.info(f"enemies right by the landing at ({here.x:.0f}, {here.y:.0f}); jumping back")
            key = (round(dest.x / 100), round(dest.y / 100))
            blocked[key] = blocked.get(key, 0) + 1
            await original(start, *args, **kwargs)
            client._teleport_aborted = True
        return result

    client.teleport = teleport
    client._safe_teleport = True
