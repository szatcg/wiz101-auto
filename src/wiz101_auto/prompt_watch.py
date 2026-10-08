"""Press X the moment the right person's talk prompt shows.

The quest step looks at the prompt only at certain points (after a teleport
settles, after an approach), so a prompt that came up on the way often sat
there a while. This loop reads the prompt window (memory, not screenshots:
milliseconds) five times a second and, when it's a talk prompt whose title
is the person the current objective names ("Talk To Junho Shan", "Locate
Joo-Young"), presses X at once. Never on anything else (a teleporter's
"activate", a sigil, a stranger).

It also watches the game's goal id (memory: changes the moment the quest
moves on): the step in progress is restarted on the new objective at once,
instead of talking to the same person until the step noticed (five talks,
15 s, at every "Talk To" in the Grand Chasm).
"""

from __future__ import annotations

import asyncio
import re
import time

from loguru import logger
from wizwalker import Keycode

from . import ui

NPC_RANGE_TITLE = ["WorldView", "NPCRangeWin", "wndTitleBackground", "NPCRangeTxtTitle"]
CHECK_EVERY = 0.2  # seconds between looks at the prompt
PRESS_COOLDOWN = 3.0  # after a press, let the dialogue come up before pressing again
COLLECT_COOLDOWN = 1.0  # between presses on pick-up prompts
QUEUE_CHECK_EVERY = 60.0  # queued for a team: this often, make sure the game still has us queued


def _norm(s: str) -> str:
    return "".join(ch for ch in (s or "").lower() if ch.isalnum())


def wanted_person(objective: str) -> str | None:
    """The person a Talk To / Locate objective is about."""
    from .quest import locate_target, talk_target

    return talk_target(objective or "") or locate_target(objective or "")


def should_talk(objective: str, prompt: str, title: str) -> bool:
    """A talk prompt titled with the person the objective names."""
    who = wanted_person(objective)
    return bool(who) and "talk" in (prompt or "").lower() and _norm(title) == _norm(who)


_OPERATE = re.compile(
    r"(?i)^\s*(?:use|repair|lock|unlock|open|light|burn|ring|pull|push|press|activate|turn|flip|place|"
    r"charge|smash|destroy|read|study|examine|inspect|touch|free|release|rescue|break|search|unseal)\s+(?:the\s+)?(.+?)(?:\s+in\s+[^()]+)?"
    r"(?:\s*\(\d+ of \d+\))?\s*$")


def operated_thing(objective: str) -> str | None:
    """The object a "Use Crystal Charger in The Grand Chasm" objective names."""
    m = _OPERATE.match(objective or "")
    return m.group(1).strip() if m else None


def should_use(objective: str, prompt: str, title: str) -> bool:
    """A use/activate prompt (not a talk prompt) titled with the object the
    objective names ('Repair East Bridge': the East Bridge's prompt)."""
    thing, name = _norm(operated_thing(objective) or ""), _norm(title)
    if not thing or not name or "talk" in (prompt or "").lower() or "press" not in (prompt or "").lower():
        return False
    return name == thing or (min(len(name), len(thing)) >= 5 and (name in thing or thing in name))


def should_collect(objective: str, prompt: str, title: str) -> bool:
    """A pick-up prompt titled like the item a collect objective names, or
    anything like it ('Collect Red Crystal Sample': every sample's prompt is
    just "Crystal Sample")."""
    from .collect import collect_item_name, loose_names, matches_item

    item = collect_item_name(objective or "")
    if not item or not title or "talk" in (prompt or "").lower():
        return False
    if len(title.split()) > len(item.split()):
        return False  # a bigger thing named after it: the "Drum House" sigil isn't a Drum
    return any(matches_item(name, title) for name in loose_names(item)[:2])


async def _goal_id(client):
    try:
        return await client.goal_id()
    except Exception:
        return None


async def _in_team_dungeon(client) -> bool:
    """No using objects in a team dungeon: they count for the whole team, and
    in the Waterworks' lever room a wrong lever brings 4 Slithering Eels (the
    bot pulled one walking through and started that fight)."""
    from .teamup import is_team_up_zone

    try:
        return is_team_up_zone(await client.zone_name() or "")
    except Exception:
        return True


async def prompt_loop(client, quester, controller):
    last_press = 0.0
    last_ready = 0.0  # last look for a team-ready prompt (queued for a team)
    last_queue_check = time.monotonic()  # last look whether the game still has us queued
    misses = 0  # looks in a row that found us not queued
    last_collect = 0.0  # (its own cooldown: the crystal objective changes as we move, and each
    # change held back the press, so the bot stood on a sample's prompt without pressing X)
    last_goal = await _goal_id(client)
    while not controller.stopped.is_set():
        await asyncio.sleep(CHECK_EVERY)
        if controller.paused:
            continue
        try:
            # Queued for a team (a main-quest boss too hard alone): its ready
            # prompt is accepted wherever we are, not in a fight.
            from .teamup import accept_team_ready, load_queue, queued, start_early

            if load_queue() and time.monotonic() - last_ready >= 2.0 and not await client.in_battle():
                last_ready = time.monotonic()
                if await start_early(client) or await accept_team_ready(client):
                    quester.cancel_step()
                elif time.monotonic() - last_queue_check >= QUEUE_CHECK_EVERY:
                    # The game drops the queue (a dungeon entered for a side
                    # quest, a timeout) while the bot thought it waited: the
                    # player saw no Team Up running. Back to the sigil now.
                    last_queue_check = time.monotonic()
                    fresh = time.time() - float(load_queue().get("at", 0)) < QUEUE_CHECK_EVERY * 1.5
                    # (Two looks in a row, never right after queuing: the
                    # badge shows a moment late, and a lapse was read 1 s in.)
                    misses = 0 if fresh or await queued(client) else misses + 1
                    if misses >= 2:
                        misses = 0
                        quester.queue_lapsed()
        except Exception as exc:
            logger.debug(f"team ready watch: {exc!r}")
        goal = await _goal_id(client)
        if goal is not None and last_goal is not None and goal != last_goal:
            # The quest moved on: no more talking to the last person, and the
            # step restarts on the new objective (not inside a declared trip
            # or rest: those finish first).
            last_press = time.monotonic()
            try:
                busy = await client.in_battle() or time.monotonic() < controller.idle_until
            except Exception:
                busy = True
            if not busy:
                logger.info("the quest moved on: restarting the step on the new objective")
                quester.cancel_step()
        if goal is not None:
            last_goal = goal
        try:
            if (time.monotonic() - last_collect >= COLLECT_COOLDOWN and not await client.in_battle()
                    and await ui.is_visible(client, ui.NPC_RANGE)):
                objective = await quester.objective() or ""
                prompt = await ui.text_at(client, ui.NPC_RANGE_TEXT)
                title = await ui.text_at(client, NPC_RANGE_TITLE)
                if should_collect(objective, prompt, title):
                    last_collect = time.monotonic()
                    logger.info(f"pick-up prompt for {title} showed: collecting at once")
                    await client.send_key(Keycode.X, 0.1)
                    continue
        except Exception as exc:
            logger.debug(f"prompt watch (collect): {exc!r}")
        if time.monotonic() - last_press < PRESS_COOLDOWN:
            continue
        try:
            if await client.in_battle() or not await ui.is_visible(client, ui.NPC_RANGE):
                continue
            # The objective as it is now (not the step's last look: stale while
            # the step is busy, and the prompt kept being answered).
            objective = await quester.objective() or quester._last_progress[0] or ""
            prompt = await ui.text_at(client, ui.NPC_RANGE_TEXT)
            title = await ui.text_at(client, NPC_RANGE_TITLE)
            if should_talk(objective, prompt, title):
                last_press = time.monotonic()
                logger.info(f"talk prompt for {title} showed: talking at once")
                await client.send_key(Keycode.X, 0.1)
                # Possibly a turn-in no step saw ('Straight Edge' to Abbot
                # Ewan): asked again if a quest completes soon after.
                zone = await client.zone_name() or ""
                quester._recent_talks = [*getattr(quester, "_recent_talks", [])[-3:],
                                         (title, zone, time.monotonic())]
            elif should_use(objective, prompt, title) and not await _in_team_dungeon(client):
                last_press = time.monotonic()
                logger.info(f"prompt for {title} showed: using it at once")
                await client.send_key(Keycode.X, 0.1)
        except Exception as exc:  # a read during a zone change: look again next time
            logger.debug(f"prompt watch: {exc!r}")
