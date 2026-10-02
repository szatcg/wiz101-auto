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
# "Select a game to play" (the minigame picker a minigame sign opens)
MINIGAME_EXIT = ["WorldView", "mainwindow", "exit"]
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


async def _visible_named(root, name: str):
    try:
        for w in await root.get_windows_with_name(name):
            if await w.is_visible():
                return w
    except Exception:
        pass
    return None


async def close_chat(client) -> bool:
    """If the chat box is in typing mode, leave it: while it is, every key the bot
    sends (X, W, Q...) is typed into chat instead of reaching the game. The chat
    log window itself is often just open showing messages, which is harmless;
    typing mode shows up as our own keystrokes piling up in the chat input."""
    box = await _visible_named(client.root_window, "WizardChatBox")
    if box is None:
        return False
    edit = await _visible_named(box, "chatEdit")
    try:
        typed = (await edit.maybe_text() or "").strip() if edit is not None else ""
    except Exception:
        typed = ""
    if not typed:
        return False
    from wizwalker import Keycode

    logger.warning(f"the chat box is in typing mode ({typed[:20]!r} typed); leaving it")
    await client.send_key(Keycode.ESC, 0.1)  # in typing mode, Esc cancels the input
    await asyncio.sleep(0.4)
    try:
        still = (await edit.maybe_text() or "").strip()
    except Exception:
        still = ""
    if still:
        close = await _visible_named(box, "closeButton")
        if close is not None:
            await click_center(client, close)
            await asyncio.sleep(0.4)
    return True


# Message-box buttons: where on/around the button a click lands. Tried in
# order until the box closes; the spot that works is used first from then on.
MODAL_SPOTS = [(fx, fy) for fy in (0.5, 0.2, -0.3, -0.7, 0.8, 1.2) for fx in (0.5, 0.25, 0.75)]
_modal_spot: list = []  # [(fx, fy)] once learned


async def press_modal_button(client, box, name: str, done=None) -> bool:
    """Click a visible message-box button until the box closes. Clicks right on
    the button's rect didn't register on the flee confirmation, so nearby spots
    are probed and the one that works is remembered (and logged)."""
    btn = None
    for w in await box.get_windows_with_name(name):
        try:
            if await w.is_visible():
                btn = w
                break
        except Exception:
            continue
    if btn is None:
        return False
    r = await btn.scale_to_client()
    w, h = r.x2 - r.x1, r.y2 - r.y1
    spots = ([_modal_spot[0]] if _modal_spot else []) + [s for s in MODAL_SPOTS if s not in _modal_spot]
    for fx, fy in spots:
        await button_click(client, int(r.x1 + w * fx), int(r.y1 + h * fy))
        # The box can stay up a couple of seconds after a click that worked
        # (a flee happens with the round), so give it time before the next spot.
        for _ in range(10):
            await asyncio.sleep(0.3)
            try:
                # Look the box up afresh (a stale window object can still read
                # as visible), and stop as soon as the caller's goal is met.
                gone = await modal_box(client) is None
                if gone or (done is not None and await done()):
                    if not _modal_spot or _modal_spot[0] != (fx, fy):
                        logger.info(f"message box button {name!r} works at ({fx:.2f}, {fy:.2f}) of its rect")
                        _modal_spot[:] = [(fx, fy)]
                    return True
            except Exception:
                return True  # the box is gone
    logger.warning(f"message box button {name!r} didn't close the box at any spot")
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


async def button_click(client, x: int, y: int) -> None:
    """Click like a person: hover a moment, then press and hold briefly.
    WizWalker's click presses and releases instantly and then moves the cursor
    away, which cards accept but buttons (Pass, Flee, Yes/No) ignore: seen on
    the flee confirmation, where clicks right on Yes did nothing."""
    mouse = client.mouse_handler
    await mouse.set_mouse_position(x, y)
    await asyncio.sleep(0.15)
    await mouse.click(x, y, sleep_duration=0.1)


async def click_center(client, window) -> None:
    """Click the exact center of a button (hover + held click)."""
    r = await window.scale_to_client()
    await button_click(client, int((r.x1 + r.x2) / 2), int((r.y1 + r.y2) / 2))


async def find_named(window, names: set[str], max_depth: int = 8) -> dict:
    """One walk of `window`'s subtree: the shallowest window of each wanted
    name. Reading many fields of one panel this way beats a `window_at` from
    the root for each (the quest book: ~2 s a page that way)."""
    found: dict = {}
    level = [window]
    for _ in range(max_depth):
        nxt = []
        for w in level:
            try:
                kids = await w.children()
            except Exception:
                continue
            for k in kids:
                try:
                    name = await k.name()
                except Exception:
                    continue
                if name in names and name not in found:
                    found[name] = k
                nxt.append(k)
        if len(found) == len(names) or not nxt:
            break
        level = nxt
    return found


async def window_text(w) -> str:
    if w is None:
        return ""
    try:
        return _TAGS.sub("", await w.maybe_text() or "").strip()
    except Exception:
        return ""


async def window_visible(w) -> bool:
    if w is None:
        return False
    try:
        return await w.is_visible()
    except Exception:
        return False


async def is_visible(client, path: list[str]) -> bool:
    w = await window_at(client, path)
    if w is None:
        return False
    try:
        return await w.is_visible()
    except Exception:
        return False


async def quest_goal(client) -> str:
    """The goal the quest helper shows (bottom of the screen). Every
    'txtGoalName' under QuestHelperHud, the visible one: the fixed path kept
    reading an old element ('Go To Desk' while the screen said 'Talk To
    Zarathax', the bot stuck at the desk for minutes)."""
    texts = await quest_goal_texts(client)
    if texts:
        return texts[-1]  # (the newest element is usually added last)
    return await text_at(client, QUEST_GOAL_TEXT)


def pick_goal(texts: list[str], goal_id, known: dict) -> str:
    """The quest helper's text for the game's active goal `goal_id`, out of
    the visible 'txtGoalName' texts. `known` (goal id -> text, updated here)
    remembers what each goal showed: after the goal changes, a text another
    goal showed is stale (the bot talked to Sandor Spearcaller for two
    minutes after the quest had moved on, the last element still his)."""
    if not texts:
        return ""
    if goal_id is None:
        return texts[-1]
    mine = known.get(goal_id)
    if mine in texts:
        return mine
    stale = {t for g, t in known.items() if g != goal_id}
    fresh = [t for t in texts if t not in stale]
    if not fresh:
        # The helper hasn't caught up with the new goal yet: its text isn't
        # this goal's (remembering it kept the old step for good).
        return texts[-1]
    known[goal_id] = fresh[-1]
    return fresh[-1]


async def quest_goal_texts(client) -> list[str]:
    """The texts of the visible 'txtGoalName' elements under QuestHelperHud."""
    hud = await window_at(client, QUEST_GOAL_TEXT[:3])
    if hud is None:
        return []
    try:
        found = await hud.get_windows_with_name("txtGoalName")
    except Exception:
        found = []
    texts = []
    for w in found:
        try:
            if await w.is_visible():
                t = _TAGS.sub("", await w.maybe_text() or "").strip()
                if t:
                    texts.append(t)
        except Exception:
            continue
    return texts


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
