"""Team Up: enter a group dungeon with other players instead of alone.

Mount Olympus (Aquila) beat the wizard twice solo. The dungeon sigil's
"Press X to Enter" prompt has a TEAM UP! button: it queues the wizard for a
team, and when one forms the game offers to take everyone into the dungeon.

The windows aren't mapped yet, so buttons are found by the text they show
(like relog.py), and the visible window tree is saved at each stage to
state/teamup_<stage>.txt for mapping. Dungeons that need a team are listed
in TEAM_UP_DUNGEONS (zone ids of the dungeons' first rooms).
"""

from __future__ import annotations

import asyncio
import time
from pathlib import Path

from loguru import logger

from . import ui
from .relog import _find_button
from .upkeep import wait_for_loading

TEAM_UP_DUNGEONS = {"Aquila/AQ_Z01_MountOlympus"}
# Every zone of those dungeons (their rooms are separate interiors).
TEAM_UP_PREFIXES = ("Aquila/AQ_Z01_", "Aquila/Interiors/AQ_Z01_")
TEAM_UP_WORDS = ("team up!", "team up")


def is_team_up_zone(zone: str) -> bool:
    """A room of a dungeon that is only entered with a team."""
    return zone in TEAM_UP_DUNGEONS or zone.startswith(TEAM_UP_PREFIXES)


CONFIRM_WORDS = ("team up", "join", "join team", "find team", "search", "yes", "ok", "go", "accept", "ready")
TEAM_UP_WAIT = 15 * 60  # seconds to wait for a team before giving up for now


async def _dump(client, stage: str):
    try:
        lines = await ui.dump_tree(client.root_window, max_depth=12)
        path = Path("state") / f"teamup_{stage}.txt"
        path.parent.mkdir(exist_ok=True)
        path.write_text("\n".join(lines), encoding="utf-8", errors="replace")
    except Exception as exc:
        logger.debug(f"team up: could not save the {stage} windows: {exc!r}")


async def _click(client, words, stage: str) -> bool:
    button = await _find_button(client.root_window, words)
    if button is None:
        return False
    logger.info(f"team up: clicking {await button.name() or words[0]!r} ({stage})")
    await ui.click_center(client, button)
    await asyncio.sleep(1.5)
    return True


async def team_up(quester, dungeon: str) -> bool:
    """On the dungeon's sigil (prompt showing): press TEAM UP!, confirm what
    the game asks, and wait for a team to form and take us in. True once in
    the dungeon."""
    client = quester.client
    zone = await client.zone_name()
    await _dump(client, "sigil")
    if not await _click(client, TEAM_UP_WORDS, "sigil"):
        logger.warning("team up: no TEAM UP! button on the sigil; windows saved to state/teamup_sigil.txt")
        return False
    await _dump(client, "window")
    quester.controller.allow_idle(TEAM_UP_WAIT + 60)
    try:
        # Whatever the Team Up window asks (join / search / confirm): accept.
        for _ in range(3):
            if not await _click(client, CONFIRM_WORDS, "window"):
                break
            await _dump(client, "window_after")
        box = await ui.modal_box(client)
        if box is not None:
            await ui.press_modal_button(client, box, "centerButton")
        logger.info(f"team up: waiting for a team for {dungeon} (up to {TEAM_UP_WAIT // 60} min)")
        started = time.monotonic()
        last_report = started
        while time.monotonic() - started < TEAM_UP_WAIT:
            if await client.is_loading() or await client.zone_name() != zone:
                await wait_for_loading(client)
                logger.success(f"team up: in {await client.zone_name()} with a team")
                return True
            box = await ui.modal_box(client)
            if box is not None:
                text = (await ui.modal_text(box)).lower()
                logger.info(f"team up: the game asks: {text[:120]!r}")
                await _dump(client, "ready")
                await ui.press_modal_button(client, box, "centerButton")  # yes / go
            elif await _click(client, ("go", "teleport", "enter", "ready", "accept"), "ready"):
                pass
            if time.monotonic() - last_report > 60:
                last_report = time.monotonic()
                logger.info(f"team up: still waiting ({(time.monotonic() - started) / 60:.0f} min)")
            await asyncio.sleep(2.0)
        logger.warning(f"team up: no team within {TEAM_UP_WAIT // 60} min")
        return False
    finally:
        quester.controller.end_idle()
