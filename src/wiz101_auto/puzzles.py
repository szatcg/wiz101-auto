"""A general fallback for switch puzzles: try every combination.

Rooms like the Grand Arena's trophy room hide the objective ("Use Oka's
Chest") until the right switches are lit (three of six obelisks, with the
clue shown only briefly). Rather than a solver per puzzle, the bot flips the
room's switches through every on/off combination in Gray-code order: each
step flips exactly one switch, so all 2^n states are visited without
resetting the room, and after every flip it checks whether the objective
moved on or the hidden thing appeared. Six switches: at most 63 flips.

`gray_flips` is pure (unit tested); `solve_by_trying` plays it.
"""

from __future__ import annotations

import asyncio
import math
import re

from loguru import logger
from wizwalker import XYZ, Keycode

from . import ui
from .upkeep import is_free

SWITCH_WORDS = ("obelisk", "brazier", "lever", "switch", "torch", "crystal", "statue", "pedestal", "rune")
MAX_SWITCHES = 8  # 255 flips at most
SAME_SWITCH = 150.0  # entities this close together are one switch
_USE = re.compile(r"^\s*use\s+(.+?)(?:\s+in\s+.+)?\s*$", re.IGNORECASE)


def use_target(objective: str) -> str | None:
    """"Use Oka's Chest in Grand Arena" -> "Oka's Chest"."""
    m = _USE.match(objective or "")
    return m.group(1).strip() if m else None


def gray_flips(n: int) -> list[int]:
    """Which switch to flip at each step so the switches pass through every
    on/off state once: switch i flips when step's lowest set bit is i."""
    return [(k & -k).bit_length() - 1 for k in range(1, 2**n)]


def is_switch(name: str) -> bool:
    n = (name or "").lower()
    return any(w in n for w in SWITCH_WORDS)


async def find_switches(client) -> list[tuple[str, XYZ]]:
    """The room's switches: named like one, one per spot, left to right."""
    from .names import lang_name

    out: list[tuple[str, XYZ]] = []
    for e in await client.get_base_entity_list():
        try:
            t = await e.object_template()
            if not t:
                continue
            code = await t.display_name()
            names = [await t.object_name() or "", await lang_name(client, code) if code else ""]
            if not any(is_switch(n) for n in names):
                continue
            pos = await e.location()
            if any(math.dist((pos.x, pos.y), (p.x, p.y)) < SAME_SWITCH for _, p in out):
                continue
            out.append((next(n for n in names if n), pos))
        except Exception:
            continue
    return sorted(out, key=lambda s: (s[1].x, s[1].y))[:MAX_SWITCHES]


async def _flip(quester, pos: XYZ) -> bool:
    """Walk up to a switch and press X at its prompt. True if a prompt came."""
    client = quester.client
    here = await client.body.position()
    dx, dy = here.x - pos.x, here.y - pos.y
    length = math.hypot(dx, dy) or 1.0
    await client.teleport(XYZ(pos.x + dx / length * 180, pos.y + dy / length * 180, pos.z))
    await asyncio.sleep(0.6)
    await client.goto(pos.x, pos.y)
    for nudge in (None, (Keycode.S, 0.15), (Keycode.W, 0.25)):
        if nudge:
            await client.send_key(*nudge)
            await asyncio.sleep(0.2)
        if await ui.is_visible(client, ui.NPC_RANGE):
            await client.send_key(Keycode.X, 0.1)
            await asyncio.sleep(1.2)
            return True
    return False


async def solve_by_trying(quester, objective: str) -> bool:
    """Flip the room's switches through every combination until the objective
    moves on or its hidden target appears. True if solved."""
    from .bossfarm import find_entity_named

    client = quester.client
    target = use_target(objective)
    switches = await find_switches(client)
    if not target or len(switches) < 2:
        return False
    names = ", ".join(n for n, _ in switches)
    flips = gray_flips(len(switches))
    logger.info(f"{target!r} isn't here: trying every combination of {len(switches)} switches ({names}); "
                f"up to {len(flips)} flips")
    quester.controller.allow_idle(len(flips) * 12 + 60)
    try:
        for step, i in enumerate(flips, 1):
            if not await is_free(client):
                return False
            _name, pos = switches[i]
            if not await _flip(quester, pos):
                logger.debug(f"no prompt at switch {i + 1}; going on")
            if await quester.objective() != objective or await find_entity_named(client, target) is not None:
                logger.success(f"puzzle solved after {step} flip(s): {target!r} is here")
                return True
        logger.warning(f"tried every combination of the {len(switches)} switches; {target!r} didn't appear")
        return False
    finally:
        quester.controller.end_idle()
