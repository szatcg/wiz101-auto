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


# The form TEAM UP! opens (mapped from state/teamup_window.txt): farming
# runs, at least 4 players.
CONFIRM_WINDOW = "TeamUpConfirmationWindow"
TEAM_CHOICES = ("TeamTypeFarmingCheckBox", "TeamSize4CheckBox")
CONFIRM_WORDS = ("team up", "join", "join team", "find team", "search", "yes", "ok", "go", "accept", "ready")
TEAM_UP_WAIT = 15 * 60  # seconds to wait for a team before giving up for now
IN_FIGHT_RANGE = 450.0  # a player this near a duel circle is in its fight
ME_RANGE = 60.0  # a Player Object this near us is our own wizard


async def teammates(client, me) -> list:
    """Where the other players in the zone stand (Player Objects not ours)."""
    from wizwalker import XYZ

    out = []
    for e in await client.get_base_entity_list():
        try:
            t = await e.object_template()
            if not t or (await t.object_name() or "") != "Player Object":
                continue
            pos = await e.location()
            if pos.distance(me) > ME_RANGE:
                out.append(XYZ(pos.x, pos.y, pos.z))
        except Exception:
            continue
    return out


def team_fight_at(circles, mates):
    """The duel circle a teammate is fighting on (one of them stands in it),
    nearest first by the order of `circles`; None if none."""
    for c in circles:
        if any(c.distance(m) < IN_FIGHT_RANGE for m in mates):
            return c
    return None


async def _fill_form(client) -> bool:
    """Tick Farming and a minimum team size of 4 on the Team Up form, then
    press its TEAM UP!. True if the form was there."""
    form = await ui._visible_named(client.root_window, CONFIRM_WINDOW)
    if form is None:
        return False
    for name in TEAM_CHOICES:
        box = await ui._visible_named(form, name)
        if box is None:
            logger.warning(f"team up: no {name} on the form")
            continue
        logger.info(f"team up: ticking {name}")
        await ui.click_center(client, box)
        await asyncio.sleep(0.8)
    await _dump(client, "form_filled")
    button = await ui._visible_named(form, "TeamUpButton")
    if button is None:
        logger.warning("team up: no TEAM UP! button on the form")
        return True
    logger.info("team up: pressing TEAM UP! on the form (farming, 4+ players)")
    await ui.click_center(client, button)
    await asyncio.sleep(1.5)
    await close_stray_forms(client)
    return True


async def close_stray_forms(client) -> int:
    """Close Team Up forms left open once ours is sent (one opened by the run
    before a restart stayed on screen over the queue). How many it closed."""
    closed = 0
    for _ in range(3):
        form = await ui._visible_named(client.root_window, CONFIRM_WINDOW)
        if form is None:
            break
        close = await ui._visible_named(form, "CloseTeamUpConfirmationWindow")
        if close is None:
            break
        logger.info("team up: closing another Team Up form still open")
        await ui.click_center(client, close)
        await asyncio.sleep(1.0)
        closed += 1
    return closed


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
    # The form may still be open from before (a restart): fill that one in.
    form_done = await _fill_form(client)
    if not form_done and not await _click(client, TEAM_UP_WORDS, "sigil"):
        logger.warning("team up: no TEAM UP! button on the sigil; windows saved to state/teamup_sigil.txt")
        return False
    await _dump(client, "window")
    quester.controller.allow_idle(TEAM_UP_WAIT + 60)
    try:
        # Whatever the Team Up window asks (join / search / confirm): accept.
        for _ in range(0 if form_done or await _fill_form(client) else 3):
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
            await close_stray_forms(client)
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
