"""Game UI window paths and helpers.

Window paths are chains of window names from the root window. Most were
mapped by the Deimos project (GPL-3.0, https://github.com/Deimos-Wizard101);
if the game UI changes after a patch, update them here and use
`wiz101-auto inspect --windows` to explore the live tree.
"""

from __future__ import annotations

import asyncio
import re

from loguru import logger

# Dialogue
ADVANCE_DIALOG = ["WorldView", "wndDialogMain", "btnRight"]
DECLINE_QUEST = ["WorldView", "wndDialogMain", "btnLeft"]
DIALOG_TEXT = ["WorldView", "wndDialogMain", "txtArea", "txtMessage"]

# HUD
QUEST_GOAL_TEXT = ["WorldView", "windowHUD", "QuestHelperHud", "ElementWindow", "", "txtGoalName"]
NPC_RANGE = ["WorldView", "NPCRangeWin"]
NPC_RANGE_TEXT = ["WorldView", "NPCRangeWin", "imgBackground", "NPCRangeTxtMessage"]
POTION_BUTTON = ["WorldView", "windowHUD", "btnPotions"]

# Spiral door / world gate
SPIRAL_DOOR_TELEPORT = ["WorldView", "", "messageBoxBG", "ControlSprite", "teleportButton"]
SPIRAL_DOOR_EXIT = ["WorldView", "", "messageBoxBG", "ControlSprite", "cancelButton"]

# Modal popups
MISSING_AREA = ["MessageBoxModalWindow", "messageBoxBG", "messageBoxLayout", "AdjustmentWindow"]
MISSING_AREA_RETRY = [*MISSING_AREA, "RetryBtn"]
CANCEL_CHEST_REROLL = ["WorldView", "Container", "background", "", "CancelButton"]
# "Endorse a Wizard" after fighting alongside another player.
ENDORSEMENT = ["WorldView", "EndorsementWindow"]
ENDORSE_FRIENDLY = [*ENDORSEMENT, "MainLayout", "FriendlyWindow", "FriendlyButton"]
ENDORSE_CLOSE = [*ENDORSEMENT, "CloseEndorsementWindowButton"]

TRAINER_EXIT = ["WorldView", "NPCTrainingGUI", "TrainingSelection", "Exit"]

# Menus that NPC interactions can leave open; closed after every interaction.
CLOSE_BUTTONS = [
    ["WorldView", "shopGUI", "buyWindow", "exit"],
    ["WorldView", "ShoppingPetSnackWindow", "buyWindow", "exit"],
    ["WorldView", "ShoppingReagentWindow", "buyWindow", "exit"],
    ["WorldView", "NPCServicesWin", "wndDialogMain", "Exit"],
    ["WorldView", "main", "exit"],
    ["WorldView", "mainwindow", "exit"],
    ["WorldView", "TournamentRanking", "exit"],
    ["WorldView", "ClassPicture", "exit"],
    ["WorldView", "PetLevelUpWindow", "wndPetLevelBkg", "btnPetLevelClose"],
    ["WorldView", "HelpHousingTips2", "toolbar", "exit"],
    ["WorldView", "DeckConfiguration", "Close_Button"],
    ["WorldView", "", "Exit"],
]

_TAGS = re.compile(r"<[^>]+>")


async def window_at(client, path: list[str]):
    """Follow `path` from the root window; returns the window or None."""

    async def _follow(window, rest):
        if not rest:
            return window
        for child in await window.children():
            if await child.name() == rest[0]:
                found = await _follow(child, rest[1:])
                if found is not None:
                    return found
        return None

    try:
        return await _follow(client.root_window, path)
    except Exception as exc:  # memory can shift mid-read during zone changes
        logger.trace(f"window_at {path} failed: {exc}")
        return None


async def modal_box(client):
    """The visible message box ("Are you sure...?"), wherever it sits in the tree."""
    try:
        for w in await client.root_window.get_windows_with_name("MessageBoxModalWindow"):
            if await w.is_visible():
                return w
    except Exception as exc:
        logger.trace(f"modal lookup failed: {exc}")
    return None


async def modal_text(box) -> str:
    for t in await box.get_windows_with_name("CaptionText"):
        try:
            return _TAGS.sub("", await t.maybe_text() or "")
        except Exception:
            pass
    return ""


async def click_named(client, name: str) -> bool:
    """Click the first visible window called `name`, wherever it is in the tree."""
    try:
        for w in await client.root_window.get_windows_with_name(name):
            if await w.is_visible():
                await client.mouse_handler.click_window(w)
                return True
    except Exception as exc:
        logger.trace(f"click_named {name} failed: {exc}")
    return False


async def named_text(client, name: str) -> str:
    """Text of the first window called `name` anywhere in the tree ("" if none)."""
    try:
        for w in await client.root_window.get_windows_with_name(name):
            return (await w.maybe_text() or "").strip()
    except Exception:
        pass
    return ""


async def modal_click(client, box, button: str) -> bool:
    """Click `button` ("centerButton" = yes/ok, "rightButton" = no/cancel) in `box`."""
    for b in await box.get_windows_with_name(button):
        await client.mouse_handler.click_window(b)
        return True
    return False


async def confirm_modal(client, buttons: tuple[str, ...] = ("centerButton",)) -> bool:
    """Accept an open message box (never a flee prompt). True if clicked."""
    box = await modal_box(client)
    if box is None or "flee" in (await modal_text(box)).lower():
        return False
    for b in buttons:
        if await modal_click(client, box, b):
            return True
    return False


NOTICE_WORDS = ("not allowed", "cannot", "can't", "unable")


async def dismiss_notice(client) -> bool:
    """Close an information message ("Quest Helper is not allowed for this
    quest", "You cannot ...") that would otherwise sit on the screen and block
    clicks, even mid-fight. Questions (flee, dungeon warnings) are left alone."""
    box = await modal_box(client)
    if box is None:
        return False
    text = await modal_text(box)
    low = text.lower()
    if "flee" in low or not any(w in low for w in NOTICE_WORDS):
        return False
    logger.info(f"closing message: {text[:100]!r}")
    # Only a visible button, clicked at its exact center: a one-button box
    # keeps hidden siblings, and the usual left-shifted click can miss.
    for name in ("centerButton", "rightButton", "leftButton"):
        for btn in await box.get_windows_with_name(name):
            try:
                if not await btn.is_visible():
                    continue
                await click_center(client, btn)
            except Exception:
                continue
            await asyncio.sleep(0.4)
            if not await box.is_visible():
                return True
    # No Enter fallback: in Wizard101, Enter opens the chat box, and every key
    # after that (X, W, Q...) would be typed into chat instead of playing.
    return False


async def click_center(client, window) -> None:
    """Click the exact center (the global 25%-of-width click can land on a
    neighbouring button, e.g. Pass -> Flee)."""
    r = await window.scale_to_client()
    await client.mouse_handler.click(int((r.x1 + r.x2) / 2), int((r.y1 + r.y2) / 2))


async def is_visible(client, path: list[str]) -> bool:
    w = await window_at(client, path)
    if w is None:
        return False
    try:
        return await w.is_visible()
    except Exception:
        return False


async def text_at(client, path: list[str]) -> str:
    w = await window_at(client, path)
    if w is None:
        return ""
    try:
        return _TAGS.sub("", await w.maybe_text() or "").strip()
    except Exception:
        return ""


async def click(client, path: list[str]) -> bool:
    """Click the window at `path` if it is visible. Returns True if clicked."""
    w = await window_at(client, path)
    if w is None:
        return False
    try:
        if not await w.is_visible():
            return False
        await client.mouse_handler.click_window(w)
        return True
    except Exception as exc:
        logger.debug(f"click {path[-1]} failed: {exc}")
        return False


async def close_menus(client) -> int:
    closed = 0
    for path in [*CLOSE_BUTTONS, TRAINER_EXIT]:
        if await click(client, path):
            closed += 1
    return closed


async def dump_tree(
    window, depth: int = 0, max_depth: int = 6, only_visible: bool = True, with_types: bool = False
) -> list[str]:
    """Readable window tree, for debugging paths after game patches."""
    lines: list[str] = []
    try:
        name = await window.name()
        visible = await window.is_visible()
    except Exception:
        return lines
    if only_visible and not visible and depth > 0:
        return lines
    text = ""
    try:
        text = _TAGS.sub("", await window.maybe_text() or "")[:60]
    except Exception:
        pass
    kind = ""
    if with_types:
        try:
            kind = f"[{await window.maybe_read_type_name()}] "
        except Exception:
            kind = "[?] "
    hidden = "" if visible else " (hidden)"
    lines.append(f"{'  ' * depth}{kind}{name or '<unnamed>'}{hidden}{'  : ' + text if text else ''}")
    if depth < max_depth:
        for child in await window.children():
            lines.extend(await dump_tree(child, depth + 1, max_depth, only_visible, with_types))
    return lines
