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
# How quest steps name those dungeons ("Defeat Zeus Sky Father in Mount Olympus").
TEAM_UP_NAMES = ("mount olympus",)
TEAM_UP_WORDS = ("team up!", "team up")
# Queue with Team Up, or only wait on the sigil for players to gather there
# (the user's choice for now: the queue took us in with teams that then left).
USE_QUEUE = False
CANCEL_WORDS = ("cancel", "cancel team up", "leave", "leave queue", "stop", "yes", "ok")


def is_team_up_zone(zone: str) -> bool:
    """A room of a dungeon that is only entered with a team."""
    return zone in TEAM_UP_DUNGEONS or zone.startswith(TEAM_UP_PREFIXES)


# The form TEAM UP! opens (mapped from state/teamup_window.txt): farming
# runs, at least 4 players.
CONFIRM_WINDOW = "TeamUpConfirmationWindow"
TEAM_CHOICES = ("TeamTypeFarmingCheckBox", "TeamSize4CheckBox")
CONFIRM_WORDS = ("team up", "join", "join team", "find team", "search", "yes", "ok", "go", "accept", "ready")
TEAM_UP_WAIT = 15 * 60  # seconds to wait for a team before giving up for now
# Players gathering on the sigil to go in: with this many others on it, press
# X and stand still through the countdown to go in with them.
PARTY_ON_SIGIL = 2
SIGIL_RADIUS = 450.0  # a player this near the sigil's center is standing on it
SIGIL_FIND_RANGE = 800.0  # the sigil object nearest us within this is ours
SIGIL_COUNTDOWN_WAIT = 15.0
SIGIL_RETRY = 45.0  # seconds before pressing X again if they didn't take us in
NO_PLAYERS_SWITCH = 5 * 60  # nobody near the sigil this long while queued: switch realm
SIGIL_AREA = 1500.0  # "near the sigil": players around here may be coming to go in
IN_FIGHT_RANGE = 900.0  # a player this near a duel circle (or an enemy) is in its fight
# (Apollo's arena: the team stood ~500+ from the circle's center, fighting)
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


def players_on_sigil(center, mates) -> int:
    """How many other players stand on the sigil at `center`."""
    return sum(1 for m in mates if m.distance(center) < SIGIL_RADIUS)


async def sigil_center(client, me):
    """The sigil we stand at (its object), or our own spot if none is listed."""
    best, best_d = me, SIGIL_FIND_RANGE
    for e in await client.get_base_entity_list():
        try:
            t = await e.object_template()
            if not t or "sigil" not in (await t.object_name() or "").lower():
                continue
            pos = await e.location()
            if pos.distance(me) < best_d:
                best, best_d = pos, pos.distance(me)
        except Exception:
            continue
    return best


async def _join_party_on_sigil(client, zone: str, center, last_press: list[float]) -> bool:
    """Two or more players on the sigil: press X once and stand still through
    the countdown so we go in with them. True once in the dungeon."""
    from wizwalker import Keycode

    if time.monotonic() - last_press[0] < SIGIL_RETRY:
        return False
    me = await client.body.position()
    on = players_on_sigil(center, await teammates(client, me))
    if on < PARTY_ON_SIGIL:
        return False
    last_press[0] = time.monotonic()
    logger.info(f"team up: {on} players on the sigil; pressing X to go in with them "
                f"(waiting {SIGIL_COUNTDOWN_WAIT:.0f}s)")
    await client.send_key(Keycode.X, 0.1)
    deadline = time.monotonic() + SIGIL_COUNTDOWN_WAIT + 10  # + the loading screen
    while time.monotonic() < deadline:
        if await client.is_loading() or await client.zone_name() != zone:
            await wait_for_loading(client)
            logger.success(f"team up: in {await client.zone_name()} with the players from the sigil")
            return True
        await asyncio.sleep(1.0)
    logger.info("team up: the sigil's party didn't take us in; back to waiting")
    return False


def team_fight_at(circles, mates):
    """The duel circle a teammate is fighting on (one of them stands in it),
    nearest first by the order of `circles`; None if none."""
    for c in circles:
        if any(c.distance(m) < IN_FIGHT_RANGE for m in mates):
            return c
    return None


async def queued(client) -> bool:
    """Already in the Team Up queue: the badge by the friends button says
    Waiting (and the sigil then shows no TEAM UP!, only Resume)."""
    badge = await ui._visible_named(client.root_window, "btnTeamUp")
    if badge is None:
        return False  # (its text keeps saying Waiting while hidden)
    return "waiting" in ui._TAGS.sub("", await ui.named_text(client, "txtTeamUp")).lower()


async def close_events_window(client) -> bool:
    """The events window (Fall Scroll of Fortune...) that a press on the
    Waiting badge's spot opened: close it."""
    close = await ui._visible_named(client.root_window, "CloseClassProjectLaunchButton")
    if close is None:
        return False
    logger.info("team up: closing the events window")
    await ui.click_center(client, close)
    await asyncio.sleep(1.0)
    return True


async def cancel_queue(client) -> bool:
    """Leave the Team Up queue: press the Waiting badge and say yes to what
    it asks. True once no longer queued."""
    if not await queued(client):
        return True
    badge = await ui._visible_named(client.root_window, "btnTeamUp")
    if badge is None:
        logger.warning("team up: queued but no Waiting badge to press")
        return False
    logger.info("team up: leaving the queue (pressing the Waiting badge)")
    await ui.click_center(client, badge)
    await asyncio.sleep(1.5)
    await _dump(client, "cancel")
    if await close_events_window(client):
        return not await queued(client)
    box = await ui.modal_box(client)
    if box is not None:
        logger.info(f"team up: the game asks: {(await ui.modal_text(box))[:120]!r}; yes")
        await ui.press_modal_button(client, box, "centerButton")
    else:
        await _click(client, CANCEL_WORDS, "cancel")
    await asyncio.sleep(1.5)
    if await queued(client):
        logger.warning("team up: still queued; windows saved to state/teamup_cancel.txt")
        return False
    logger.success("team up: left the queue")
    return True


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


async def team_up(quester, dungeon: str) -> str:
    """On the dungeon's sigil (prompt showing): press TEAM UP!, confirm what
    the game asks, and wait for a team to form and take us in. "in" once in
    the dungeon, "switched" after moving to another realm (nobody around for
    NO_PLAYERS_SWITCH: queue again there), "none" with no team."""
    client = quester.client
    zone = await client.zone_name()
    await _dump(client, "sigil")
    if not USE_QUEUE:
        # No queue: wait on the sigil for players to gather (and go in with them).
        await close_stray_forms(client)
        # (No cancel_queue here: the Waiting badge's spot is the events button
        # when not queued, and pressing it opened the events window.)
        await close_events_window(client)
        logger.info("team up: waiting on the sigil for players to gather (not queueing)")
    # The form may still be open from before (a restart): fill that one in.
    form_done = not USE_QUEUE or await _fill_form(client)
    if not form_done and await queued(client):
        logger.info("team up: already in the queue (Waiting); waiting on")
        form_done = True
    if not form_done and not await _click(client, TEAM_UP_WORDS, "sigil"):
        # The sigil shows only Resume while we're queued (after a restart the
        # Waiting badge wasn't always readable): wait for the team to take us.
        logger.info("team up: no TEAM UP! button on the sigil (already queued?); waiting on")
        form_done = True
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
        center = await sigil_center(client, await client.body.position())
        last_press = [0.0]
        last_player = time.monotonic()
        while time.monotonic() - started < TEAM_UP_WAIT:
            await close_stray_forms(client)
            if await _join_party_on_sigil(client, zone, center, last_press):
                return "in"
            mates = await teammates(client, await client.body.position())
            if any(m.distance(center) < SIGIL_AREA for m in mates):
                last_player = time.monotonic()
            elif time.monotonic() - last_player > NO_PLAYERS_SWITCH:
                from .realm import switch_realm

                logger.info(f"team up: no other players near the sigil for {NO_PLAYERS_SWITCH // 60} min; "
                            "switching realm")
                if await switch_realm(client):
                    return "switched"
                last_player = time.monotonic()  # it didn't work: wait another spell
            if await client.is_loading() or await client.zone_name() != zone:
                await wait_for_loading(client)
                logger.success(f"team up: in {await client.zone_name()} with a team")
                return "in"
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
        return "none"
    finally:
        quester.controller.end_idle()
