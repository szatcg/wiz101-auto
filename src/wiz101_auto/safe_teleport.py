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
import math
import time

from loguru import logger
from wizwalker import XYZ

OFF_MAP = 900.0  # no known ground point this close to a teleport's destination: off the map
GROUND_HEIGHT = 400.0  # ground points at about the destination's height count
MIN_GROUND_POINTS = 25  # judge only with enough of the zone known
MAX_SNAP = 1600.0  # never move a landing further than this
FIGHT_STARTING = 30.0  # seconds after heading into a fight in which a circle holds us
ON_CIRCLE = 700.0  # this close to a duel circle's center: on it (no teleporting off)
DUEL_CIRCLE_RING = 350.0  # a duel circle's seats: hazards on this ring around its center
LANDING_CLEARANCE = 700.0  # an enemy closer than this to the landing spot tends to start a fight
BLOCKED_TRIES = 3  # a destination with no clear spot around it: after this many skips, go anyway
ARRIVAL_CLEARANCE = 550.0  # after landing: an enemy this close sends us straight back
ARRIVAL_SETTLE = 0.35  # seconds for nearby entities to stream in after a jump
OTHER_LEVEL = 300.0  # an enemy this far above/below is on another level (the arena floor below a platform)


def same_level(spot, hazards: list) -> list:
    """Hazards on the same level as `spot`: those far above or below it (under
    an elevated platform) can't reach it."""
    return [h for h in hazards if abs(h.z - spot.z) < OTHER_LEVEL]


def teleport_aborted(client) -> bool:
    """Did the last teleport not happen, or jump back from enemies? Then the
    caller shouldn't walk on toward its target."""
    return bool(getattr(client, "_teleport_aborted", False))


def allow_close_landing(client, seconds: float = 20.0):
    """Going toward enemies we mean to fight ("Defeat X" here): still land
    clear of them, but don't jump back when they turn out to be close."""
    client._close_ok_until = time.monotonic() + seconds


def allow_engage(client, seconds: float = 6.0):
    """The next teleports (for `seconds`) are meant to start a fight."""
    client._engage_until = time.monotonic() + seconds
    client._engaged_at = time.monotonic()


LANDED = 200.0  # a teleport that ends this near its target worked


def install(client):
    """Wrap `client.teleport` with the landing check (once)."""
    if getattr(client, "_safe_teleport", False):
        return
    original = client.teleport
    blocked: dict[tuple[int, int], int] = {}

    async def teleport(xyz, *args, **kwargs):
        result = await _teleport(xyz, *args, **kwargs)
        try:
            here = await client.body.position()
            client._last_landing = (time.monotonic(), here.x, here.y, here.z)  # (door learning)
        except Exception:
            pass
        return result

    async def _teleport(xyz, *args, **kwargs):
        from .quest import clear_of, safe_landing
        from .upkeep import mob_positions

        client._teleport_aborted = False
        try:
            engaging = time.monotonic() < getattr(client, "_engage_until", 0.0)
            if engaging or await client.in_battle():
                return await original(xyz, *args, **kwargs)
            from .collect import duel_circles

            me = await client.body.position()
            fight_starting = time.monotonic() - getattr(client, "_engaged_at", -1e9) < FIGHT_STARTING
            if fight_starting and any(
                math.dist((me.x, me.y), c[:2]) < ON_CIRCLE for c in await duel_circles(client)
            ):
                # On a fight's circle whose battle hasn't shown yet (a boss's
                # entrance): moving now breaks it into a 0-opponent limbo.
                logger.info("standing on a duel circle; not teleporting while its fight may be starting")
                client._teleport_aborted = True
                return None
            from .collect import duel_circles

            ring = []
            for cx, cy, cz in await duel_circles(client):
                # Joining a duel circle whose boss hasn't spawned is a "battle"
                # with 0 opponents that never plays out (Counterweight East):
                # a circle reaches further than one enemy, so keep off its edge.
                ring += [
                    XYZ(cx + DUEL_CIRCLE_RING * math.cos(a), cy + DUEL_CIRCLE_RING * math.sin(a), cz)
                    for a in (i * math.pi / 4 for i in range(8))
                ]
            hazards = same_level(xyz, [XYZ(*m) for m in await mob_positions(client)] + ring)
            ground_of = getattr(client, "_ground_points", None)
            if ground_of is not None:
                # Off the map (the clouds past the Commons' edge, where walking
                # does nothing): land on the nearest known ground instead.
                ground = [g for g in await ground_of(xyz) if abs(g[2] - xyz.z) < GROUND_HEIGHT]
                nearest = min((math.dist((xyz.x, xyz.y), g[:2]) for g in ground), default=0.0)
                # Only with the zone well known, and only a short move: in a
                # sparsely known zone it moved landings 2000 away.
                if len(ground) >= MIN_GROUND_POINTS and OFF_MAP < nearest < MAX_SNAP:
                    g = min(ground, key=lambda q: math.dist((xyz.x, xyz.y), q[:2]))
                    logger.info(f"({xyz.x:.0f}, {xyz.y:.0f}) looks off the map; landing on known ground "
                                f"at ({g[0]:.0f}, {g[1]:.0f})")
                    xyz = XYZ(*g)
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
        if time.monotonic() < getattr(client, "_close_ok_until", 0.0):
            return result  # heading for a fight on purpose
        await asyncio.sleep(ARRIVAL_SETTLE)
        if await client.in_battle():
            return result
        here = await client.body.position()
        near = [XYZ(*m) for m in await mob_positions(client)]
        if not clear_of(here, same_level(here, near), ARRIVAL_CLEARANCE) and clear_of(
            start, same_level(start, near), ARRIVAL_CLEARANCE
        ):
            logger.info(f"enemies right by the landing at ({here.x:.0f}, {here.y:.0f}); jumping back")
            key = (round(dest.x / 100), round(dest.y / 100))
            blocked[key] = blocked.get(key, 0) + 1
            await original(start, *args, **kwargs)
            client._teleport_aborted = True
        elif math.dist((here.x, here.y), (dest.x, dest.y)) < LANDED:
            # It worked: remember the spot (teleports often take a few tries).
            from .tpspots import spots

            spots().add(await client.zone_name() or "", (here.x, here.y, here.z))
        return result

    client.teleport = teleport
    client._safe_teleport = True

    walk = getattr(client, "goto", None)
    if walk is None:
        return

    async def goto(x, y, *args, **kwargs):
        # WizWalker's yaw maths does acos() of a value a hair past -1 when the
        # target is exactly in line (straight along an axis): ValueError. A
        # target nudged by a unit walks the same way.
        try:
            return await walk(x, y, *args, **kwargs)
        except ValueError as exc:
            if "range from -1" not in str(exc):
                raise
            return await walk(x + 1.0, y + 1.0, *args, **kwargs)

    client.goto = goto
