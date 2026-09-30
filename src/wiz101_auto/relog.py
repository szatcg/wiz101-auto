"""Log out to character select and back in: clears a wizard stuck in place.

Standing on a duel circle whose fight never starts, the game stopped taking
moves (WizWalker's teleport timed out on `should_update` over and over) while
still running fine. Quitting to character select and pressing Play puts the
wizard back in the world, free.

The game menu (Escape), its Quit button, the "are you sure" box and the
character select Play button are found by the text they show, not by window
paths (not mapped yet); the visible window tree is saved at each stage to
state/relog_<stage>.txt so a missed step can be mapped exactly.
"""

from __future__ import annotations

import asyncio
import time
from pathlib import Path

from loguru import logger
from wizwalker import Keycode

from . import ui
from .upkeep import wait_for_loading

QUIT_WORDS = ("quit",)
CONFIRM_WORDS = ("yes", "ok", "quit")
PLAY_WORDS = ("play",)
RELOG_SECONDS = 120.0  # the watchdog holds off this long while a relog runs


async def _dump(client, stage: str):
    try:
        lines = await ui.dump_tree(client.root_window, max_depth=10)
        path = Path("state") / f"relog_{stage}.txt"
        path.parent.mkdir(exist_ok=True)
        path.write_text("\n".join(lines), encoding="utf-8", errors="replace")
    except Exception as exc:
        logger.debug(f"relog: could not save the {stage} windows: {exc!r}")


async def _find_button(window, words: tuple[str, ...], depth: int = 0):
    """A visible window whose text is one of `words` (whole text, any case)."""
    try:
        if depth and not await window.is_visible():
            return None
    except Exception:
        return None
    try:
        text = ui._TAGS.sub("", await window.maybe_text() or "").strip().lower()
    except Exception:
        text = ""  # (not a text window: still look inside it)
    if text in words:
        return window
    try:
        name = (await window.name() or "").lower()
    except Exception:
        name = ""
    if any(name == f"{w}button" or name == f"btn{w}" for w in words):
        return window  # QuitButton, btnPlay...
    if depth > 12:
        return None
    for child in await window.children():
        found = await _find_button(child, words, depth + 1)
        if found is not None:
            return found
    return None


async def _click_text(client, words: tuple[str, ...], stage: str, tries: int = 10) -> bool:
    for _ in range(tries):
        button = await _find_button(client.root_window, words)
        if button is not None:
            logger.info(f"relog: clicking {await button.name() or words[0]!r} ({stage})")
            await ui.click_center(client, button)
            await asyncio.sleep(1.5)
            return True
        await asyncio.sleep(0.5)
    await _dump(client, stage)
    logger.warning(f"relog: no {words[0]!r} button ({stage}); windows saved to state/relog_{stage}.txt")
    return False


async def relog(client) -> bool:
    """Quit to character select and play again. True once back in the world.
    The watchdog leaves it alone meanwhile (client._relogging_until)."""
    client._relogging_until = time.monotonic() + RELOG_SECONDS
    try:
        return await _relog(client)
    finally:
        client._relogging_until = 0.0


async def at_character_select(client) -> bool:
    """The game is at character select (no zone, a Play button showing)."""
    try:
        if await client.zone_name():
            return False
    except Exception:
        pass
    return await _find_button(client.root_window, PLAY_WORDS) is not None


async def play_from_character_select(client) -> bool:
    """Left at character select (a relog cut short): press Play."""
    client._relogging_until = time.monotonic() + RELOG_SECONDS
    try:
        logger.warning("at character select: pressing Play to get back in")
        return await _play(client, None)
    finally:
        client._relogging_until = 0.0


async def _relog(client) -> bool:
    logger.warning("relogging: the wizard is stuck in place; quitting to character select and back")
    zone_before = await client.zone_name()
    if await _confirm_logout(client):
        return await _play(client, zone_before)  # the Log Out box was already up
    await ui.close_menus(client)
    # Escape first closes whatever is open (the quest book), then opens the
    # game menu: press it until Quit shows.
    quit_button = None
    for _ in range(3):
        await client.send_key(Keycode.ESC, 0.1)
        await asyncio.sleep(1.5)
        quit_button = await _find_button(client.root_window, QUIT_WORDS)
        if quit_button is not None:
            break
    if quit_button is None:
        await _dump(client, "menu")
        logger.warning("relog: no Quit button after Escape; windows saved to state/relog_menu.txt")
        return False
    await _dump(client, "menu")
    if not await _click_text(client, QUIT_WORDS, "menu"):
        return False
    await asyncio.sleep(1.0)
    if not await _confirm_logout(client):
        await _dump(client, "confirm")
        logger.warning("relog: no Log Out box to confirm; windows saved to state/relog_confirm.txt")
    return await _play(client, zone_before)


async def _confirm_logout(client) -> bool:
    """Answer Yes to "Log Out: ... are you sure you want to log out now?"
    (the game's message box: Yes is its centerButton). True if it did."""
    for _ in range(8):
        box = await ui.modal_box(client)
        if box is not None and "log out" in (await ui.modal_text(box)).lower():
            logger.info("relog: confirming Log Out")
            await ui.press_modal_button(client, box, "centerButton")
            await asyncio.sleep(2.0)
            return True
        await asyncio.sleep(0.5)
    return False


async def _play(client, zone_before) -> bool:
    # Character select: wait for its Play button.
    deadline = time.monotonic() + 60
    while time.monotonic() < deadline:
        if await _find_button(client.root_window, PLAY_WORDS) is not None:
            break
        await asyncio.sleep(1.0)
    await _dump(client, "character_select")
    if not await _click_text(client, PLAY_WORDS, "character_select"):
        return False
    await wait_for_loading(client, appear_timeout=10.0)
    for _ in range(30):
        if await client.zone_name():
            break
        await asyncio.sleep(1.0)
    logger.success(f"relogged: back in {await client.zone_name()} (was {zone_before})")
    return True
