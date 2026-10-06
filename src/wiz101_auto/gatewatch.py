"""Learn every gate walked through, by the bot or by the player (paused).

The player walked the Waterworks' gates by hand while the bot was paused and
it learned none of them. This loop samples the zone and position a few times
a second; when the zone changes right after the wizard was walking (not a
teleport, Recall or the hub button: those stand still first, or jump), the
last spot before the change is the door and a spot a little before it the
place to walk from. Saved in state/doors.json, which the next-room moves and
door searches use first.
"""

from __future__ import annotations

import asyncio
import math
from collections import deque

from loguru import logger

SAMPLE_EVERY = 0.25  # seconds between looks
WALK_FROM = 1.5  # seconds before the change: where the walk in starts
MIN_STEP = 40.0  # the wizard moved at least this much over WALK_FROM: walking, not standing (a Recall)
MAX_STEP = 1200.0  # ... and at most this (a teleport jumps further)
DOOR_AHEAD = 60.0  # the door: this far on past the last spot, along the walk


def walked_through(samples: list[tuple[float, str, float, float, float]], new_zone: str):
    """From (time, zone, x, y, z) samples ending in the old zone, the door
    walked through into `new_zone`: (door xy, start xyz), or None when the
    change wasn't a walk."""
    if not samples:
        return None
    t_last, zone, x, y, z = samples[-1]
    if not zone or not new_zone or zone == new_zone or zone.split("/")[0] != new_zone.split("/")[0]:
        return None
    before = [s for s in samples if s[1] == zone and t_last - s[0] >= WALK_FROM]
    if not before:
        return None
    _t, _z, bx, by, bz = before[-1]
    step = math.hypot(x - bx, y - by)
    if step < MIN_STEP or step > MAX_STEP:
        return None
    ux, uy = (x - bx) / step, (y - by) / step
    door = (x + ux * DOOR_AHEAD, y + uy * DOOR_AHEAD)
    return door, (bx, by, bz)


async def gate_watch(client, doors, controller) -> None:
    samples: deque = deque(maxlen=int(6 / SAMPLE_EVERY))
    loop = asyncio.get_running_loop()
    while not controller.stopped.is_set():
        await asyncio.sleep(SAMPLE_EVERY)
        try:
            if await client.is_loading():
                continue
            zone = await client.zone_name() or ""
            pos = await client.body.position()
        except Exception:
            continue
        if samples and samples[-1][1] and zone and zone != samples[-1][1]:
            found = walked_through(list(samples), zone)
            old = samples[-1][1]
            samples.clear()
            if found is not None:
                door, start = found
                try:
                    doors.record(old, (door[0], door[1], start[2]), start, zone)
                    logger.info(f"learned the walk from {old.split('/')[-1]} into {zone.split('/')[-1]}: "
                                f"from ({start[0]:.0f}, {start[1]:.0f}) "
                                f"through ({door[0]:.0f}, {door[1]:.0f})")
                except Exception as exc:
                    logger.debug(f"gate walk not saved: {exc!r}")
        samples.append((loop.time(), zone, pos.x, pos.y, pos.z))
