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
import time

from loguru import logger
from wizwalker import Keycode

from . import ui

NPC_RANGE_TITLE = ["WorldView", "NPCRangeWin", "wndTitleBackground", "NPCRangeTxtTitle"]
CHECK_EVERY = 0.2  # seconds between looks at the prompt
PRESS_COOLDOWN = 3.0  # after a press, let the dialogue come up before pressing again


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


async def _goal_id(client):
    try:
        return await client.goal_id()
    except Exception:
        return None


async def prompt_loop(client, quester, controller):
    last_press = 0.0
    last_goal = await _goal_id(client)
    while not controller.stopped.is_set():
        await asyncio.sleep(CHECK_EVERY)
        if controller.paused:
            continue
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
        except Exception as exc:  # a read during a zone change: look again next time
            logger.debug(f"prompt watch: {exc!r}")
