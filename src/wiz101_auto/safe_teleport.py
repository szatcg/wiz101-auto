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
OUTSIDE_MARGIN = 300.0  # ... and past the known ground's outline by this much (not just an unseen corner)
WALK_MAX_SECONDS = 20.0  # no single walk holds W longer than this
WALK_MIN_SPEED = 250.0  # units/second a walk is given at the least (normal is ~500)
WITH_TARGET = 700.0  # an enemy this close to one we're after joins that fight: not a stranger
WALK_CLEARANCE = 600.0  # a walk whose straight path passes an enemy closer than this isn't taken
RETRY_WAIT = 2.0  # a teleport the game didn't pick up in WizWalker's 0.6 s: once more, waiting this long
NOT_TAKEN = 5.0  # moved less than this: the teleport didn't happen
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


def is_target(name: str, targets) -> bool:
    """An enemy named like one of `targets` ("Otomo Supply Runner" for
    "Otomo Supply Runners")."""
    key = "".join(c for c in (name or "").lower() if c.isalpha())
    for t in targets or []:
        want = "".join(c for c in t.lower() if c.isalpha())
        if key and want and (want in key or key in want or want.rstrip("s") in key):
            return True
    return False


def segment_distance(p, a, b) -> float:
    """Distance from point `p` to the segment a-b (2D)."""
    ax, ay = a
    bx, by = b
    dx, dy = bx - ax, by - ay
    length2 = dx * dx + dy * dy
    t = 0.0 if length2 == 0 else max(0.0, min(1.0, ((p[0] - ax) * dx + (p[1] - ay) * dy) / length2))
    return math.dist(p, (ax + t * dx, ay + t * dy))


def outside(p, ground: list, margin: float) -> bool:
    """`p` lies past the box around the known ground points by `margin`."""
    xs, ys = [g[0] for g in ground], [g[1] for g in ground]
    return (p.x < min(xs) - margin or p.x > max(xs) + margin
            or p.y < min(ys) - margin or p.y > max(ys) + margin)


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
    wizwalker_teleport = client.teleport
    client._teleport_raw = wizwalker_teleport  # (scouting under the map: no snapping to the ground)
    blocked: dict[tuple[int, int], int] = {}

    async def original(xyz, *args, **kwargs):
        """WizWalker's teleport hands the spot to the game and waits 0.6 s for
        it to be picked up, then quietly drops it: the wizard doesn't move
        (logged as 'rejected' 400 times). Once more with a longer wait."""
        try:
            before = await client.body.position()
        except Exception:
            return await wizwalker_teleport(xyz, *args, **kwargs)
        # The zone's collision geometry: land on walkable ground clear of walls
        # (inside a forge or a door frame the game refused or bounced us).
        from . import walkmap

        asked = xyz
        xyz = await walkmap.landing(client, xyz)
        result = await wizwalker_teleport(xyz, *args, **kwargs)
        far = math.dist((before.x, before.y), (xyz.x, xyz.y)) > NOT_TAKEN * 10
        try:
            after = await client.body.position()
        except Exception:
            return result
        if far and math.dist((after.x, after.y), (before.x, before.y)) >= NOT_TAKEN:
            client._refused_in_row = 0
        if far and math.dist((after.x, after.y), (before.x, before.y)) < NOT_TAKEN:
            try:
                await wizwalker_teleport(xyz, *args, purge_on_after_unuser_fixer_timeout=RETRY_WAIT, **kwargs)
            except TypeError:  # a teleport without WizWalker's options (tests)
                await wizwalker_teleport(xyz, *args, **kwargs)
            again = await client.body.position()
            took = math.dist((again.x, again.y), (before.x, before.y)) >= NOT_TAKEN
            # Refused even with the long wait, twice running: the wizard is frozen
            # (it couldn't walk either; a relog fixed it). The quest step checks.
            client._refused_in_row = 0 if took else getattr(client, "_refused_in_row", 0) + 1
            if not took:
                took = await refused_fallbacks(asked, xyz, before)
            logger.info(f"teleport to ({xyz.x:.0f}, {xyz.y:.0f}) didn't happen; retried with a longer wait: "
                        f"{'it worked' if took else 'still refused'}")
        return result

    async def _hazards() -> list:
        """Enemies and duel-circle seats, read now."""
        from .collect import duel_circles
        from .upkeep import mob_positions

        try:
            ring = [
                XYZ(cx + DUEL_CIRCLE_RING * math.cos(a), cy + DUEL_CIRCLE_RING * math.sin(a), cz)
                for cx, cy, cz in await duel_circles(client) for a in (i * math.pi / 4 for i in range(8))
            ]
            return [XYZ(*m) for m in await mob_positions(client)] + ring
        except Exception:
            return []

    async def refused_fallbacks(asked, tried, before) -> bool:
        """A refused jump (Deimos's order): the strict walkable spot (every
        collision volume counted), then spots stepping back toward where we
        were. True if one took (the caller sees how near it got)."""
        from . import walkmap
        from .quest import clear_of

        try:
            if await walkmap.world_for(await client.zone_name() or "") is None:
                return False  # (no geometry: stepping back would be guessing)
        except Exception:
            return False
        options = [await walkmap.landing(client, asked, strict=True)]
        options += [await walkmap.landing(client, p) for p in walkmap.retreat_points(tried, before)]
        hazards = [] if time.monotonic() < getattr(client, "_engage_until", 0.0) else await _hazards()
        for spot in options:
            if math.dist((spot.x, spot.y), (tried.x, tried.y)) < 5:
                continue
            if hazards and not clear_of(spot, same_level(spot, hazards), LANDING_CLEARANCE):
                continue  # (a fallback landing is checked for enemies too: one landed on Zora Steelwielder)
            try:
                await wizwalker_teleport(spot)
                now = await client.body.position()
            except Exception:
                continue
            if math.dist((now.x, now.y), (before.x, before.y)) >= NOT_TAKEN:
                client._refused_in_row = 0
                logger.info(f"teleport refused at ({tried.x:.0f}, {tried.y:.0f}); landed at "
                            f"({spot.x:.0f}, {spot.y:.0f}) instead")
                return True
        return False

    async def off_map_fallback(xyz):
        """Known ground to land on if `xyz` turns out to be off the map (the
        clouds past the Commons' edge, where walking does nothing): only past
        the outline of a well-known zone's ground, and not too far."""
        ground_of = getattr(client, "_ground_points", None)
        if ground_of is None:
            return None
        ground = [g for g in await ground_of(xyz) if abs(g[2] - xyz.z) < GROUND_HEIGHT]
        nearest = min((math.dist((xyz.x, xyz.y), g[:2]) for g in ground), default=0.0)
        known = len(ground) >= MIN_GROUND_POINTS
        if known and OFF_MAP < nearest < MAX_SNAP and outside(xyz, ground, OUTSIDE_MARGIN):
            return XYZ(*min(ground, key=lambda q: math.dist((xyz.x, xyz.y), q[:2])))
        return None

    async def teleport(xyz, *args, **kwargs):
        # Past the known ground isn't always off the map (a zone exit at the
        # edge of what we've seen: snapping it 1500 away looped in the Village
        # of Sorrow). Go there; only if we then can't walk, land on known ground.
        try:
            fallback = await off_map_fallback(xyz)
        except Exception:
            fallback = None
        result = await _teleport(xyz, *args, **kwargs)
        if fallback is not None and not teleport_aborted(client):
            from .upkeep import can_move

            try:
                here = await client.body.position()
                if math.dist((here.x, here.y), (xyz.x, xyz.y)) < LANDED and not await can_move(client):
                    logger.info(f"({xyz.x:.0f}, {xyz.y:.0f}) is off the map (can't walk there); "
                                f"landing on known ground at ({fallback.x:.0f}, {fallback.y:.0f})")
                    result = await _teleport(fallback, *args, **kwargs)
            except Exception:
                pass
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
            from .dungeons import dungeon_exits

            # A dungeon room's known exits too: landing on one leaves the
            # dungeon (and its progress and quest).
            try:
                exits = [XYZ(*e) for e in dungeon_exits(await client.zone_name() or "")]
            except Exception:
                exits = []
            hazards = same_level(xyz, [XYZ(*m) for m in await mob_positions(client)] + ring + exits)
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
        # A walk runs straight at its target, into whatever stands on the way
        # (backing off Scout Tower 3's marker walked into an Otomo Mercenary).
        # Unless heading into a fight on purpose, no walk passes an enemy.
        try:
            meant = time.monotonic() < max(getattr(client, "_engage_until", 0.0),
                                           getattr(client, "_close_ok_until", 0.0))
            targets = getattr(client, "_target_names", None)
            if meant and targets and not await client.in_battle():
                # Into a fight on purpose, but the objective's fight: an enemy of
                # another kind blocks the walk unless one of ours stands by it
                # (walking onto the marker for Supply Runners met Ronin
                # Keyholders, twice). A boss's guards stand by the boss: fine.
                from .bossfarm import mobs_named

                me = await client.body.position()
                named = [(n, p) for n, p in await mobs_named(client) if abs(p.z - me.z) < OTHER_LEVEL]
                ours = [p for n, p in named if is_target(n, targets)]
                strangers = [
                    p for n, p in named
                    if not is_target(n, targets)
                    and all(math.dist((p.x, p.y), (q.x, q.y)) > WITH_TARGET for q in ours)
                    and segment_distance((p.x, p.y), (me.x, me.y), (x, y)) < WALK_CLEARANCE
                ]
                if strangers:
                    m = strangers[0]
                    logger.info(f"not walking to ({x:.0f}, {y:.0f}): enemies that aren't the target "
                                f"by the way at ({m.x:.0f}, {m.y:.0f})")
                    client._teleport_aborted = True
                    return None
            if not meant and not await client.in_battle():
                from .upkeep import mob_positions

                me = await client.body.position()
                mobs = same_level(me, [XYZ(*m) for m in await mob_positions(client)])
                near = [m for m in mobs
                        if segment_distance((m.x, m.y), (me.x, me.y), (x, y)) < WALK_CLEARANCE]
                if near:
                    m = min(near, key=lambda q: math.dist((q.x, q.y), (me.x, me.y)))
                    logger.info(f"not walking to ({x:.0f}, {y:.0f}): an enemy by the way "
                                f"at ({m.x:.0f}, {m.y:.0f})")
                    client._teleport_aborted = True
                    return None
        except Exception as exc:
            logger.debug(f"walk check failed ({exc!r}); walking")
        # WizWalker holds W for distance / (speed x a multiplier read from the
        # game); a bad read made one walk hold W for 4.5 minutes into a wall.
        # Cap it to a sane time for the distance, and let go of W if it runs over.
        try:
            here = await client.body.position()
            seconds = min(WALK_MAX_SECONDS, 2.0 + math.dist((here.x, here.y), (x, y)) / WALK_MIN_SPEED)
        except Exception:
            seconds = WALK_MAX_SECONDS

        async def go(tx, ty):
            try:
                return await asyncio.wait_for(walk(tx, ty, *args, **kwargs), seconds)
            except TimeoutError:
                logger.info(f"walk to ({tx:.0f}, {ty:.0f}) ran over {seconds:.0f}s; stopping it")
                try:
                    from wizwalker import Keycode

                    await client.send_key(Keycode.W, 0.05)  # (lets go of the held W)
                except Exception:
                    pass
                return None

        # WizWalker's yaw maths does acos() of a value a hair past -1 when the
        # target is exactly in line (straight along an axis): ValueError. A
        # target nudged by a unit walks the same way.
        try:
            return await go(x, y)
        except ValueError as exc:
            if "range from -1" not in str(exc):
                raise
            return await go(x + 1.0, y + 1.0)

    client.goto = goto
