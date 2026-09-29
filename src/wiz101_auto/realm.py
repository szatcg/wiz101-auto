"""Switch realms: another copy of the world, with other players in it.

Waiting at a group dungeon's sigil with nobody around, a busier realm finds a
team sooner. The game menu (Escape) has a Realms button (RealmsButton, seen in
state/relog_menu.txt); the realm list it opens isn't mapped yet, so its
entries and its "go" button are found by text, and the visible window tree is
saved at each stage to state/realm_<stage>.txt for mapping. Realms are taken
in turn (state/realm.json remembers which were tried).
"""

from __future__ import annotations

import asyncio
import json
import re
from pathlib import Path

from loguru import logger
from wizwalker import Keycode

from . import ui
from .relog import _find_button
from .upkeep import wait_for_loading

STATE = Path("state") / "realm.json"
REALMS_WORDS = ("realms",)
GO_WORDS = ("go to realm", "go to realm!", "go", "switch", "switch realm", "travel", "select", "ok", "yes")
# Words of the realm window that aren't realm names.
NOT_REALMS = {
    "realms", "realm", "close", "back", "cancel", "done", "ok", "yes", "no", "go", "go to realm",
    "switch", "switch realm", "select", "travel", "friends", "population", "name", "current",
}
_WORD = re.compile(r"^[A-Za-z][A-Za-z' ]{2,19}$")


def realm_names(texts: list[str]) -> list[str]:
    """Realm names among the texts of the realm list: short words that aren't
    labels or buttons, each once, in order."""
    out: list[str] = []
    for t in texts:
        t = t.strip()
        if _WORD.match(t) and t.lower() not in NOT_REALMS and t not in out:
            out.append(t)
    return out


def next_realm(names: list[str], tried: list[str]) -> str | None:
    """The first realm not tried yet (all tried: start over)."""
    for n in names:
        if n not in tried:
            return n
    return names[0] if names else None


def _load() -> dict:
    try:
        return json.loads(STATE.read_text(encoding="utf-8"))
    except Exception:
        return {"tried": []}


def _save(data: dict):
    try:
        STATE.parent.mkdir(exist_ok=True)
        STATE.write_text(json.dumps(data, indent=1), encoding="utf-8")
    except Exception:
        pass


async def _dump(client, stage: str):
    try:
        lines = await ui.dump_tree(client.root_window, max_depth=14)
        path = Path("state") / f"realm_{stage}.txt"
        path.parent.mkdir(exist_ok=True)
        path.write_text("\n".join(lines), encoding="utf-8", errors="replace")
    except Exception as exc:
        logger.debug(f"realm: could not save the {stage} windows: {exc!r}")


async def _texts(window, depth: int = 0) -> list[tuple[str, object]]:
    """(text, window) of every visible text window under `window`."""
    out = []
    try:
        if depth and not await window.is_visible():
            return out
        text = ui._TAGS.sub("", await window.maybe_text() or "").strip()
        if text:
            out.append((text, window))
    except Exception:
        return out
    if depth < 14:
        for child in await window.children():
            out += await _texts(child, depth + 1)
    return out


async def _close(client):
    for _ in range(3):
        await client.send_key(Keycode.ESC, 0.1)
        await asyncio.sleep(1.0)
        if await _find_button(client.root_window, REALMS_WORDS) is None:
            break
    await ui.close_menus(client)


async def switch_realm(client) -> bool:
    """Go to the next realm. True once in it (same place, other players)."""
    await ui.close_menus(client)
    button = None
    for _ in range(3):  # Escape closes what's open, then opens the game menu
        await client.send_key(Keycode.ESC, 0.1)
        await asyncio.sleep(1.5)
        button = await _find_button(client.root_window, REALMS_WORDS)
        if button is not None:
            break
    if button is None:
        await _dump(client, "menu")
        logger.warning("realm: no Realms button in the game menu; windows saved to state/realm_menu.txt")
        await _close(client)
        return False
    before = {t for t, _ in await _texts(client.root_window)}
    logger.info("realm: opening the realm list")
    await ui.click_center(client, button)
    await asyncio.sleep(2.0)
    await _dump(client, "list")
    # The realm list: texts that weren't on screen before it opened.
    shown = [(t, w) for t, w in await _texts(client.root_window) if t not in before]
    names = realm_names([t for t, _ in shown])
    data = _load()
    target = next_realm(names, data.get("tried", []))
    if target is None:
        logger.warning("realm: no realm names in the list; windows saved to state/realm_list.txt")
        await _close(client)
        return False
    entry = next(w for t, w in shown if t.strip() == target)
    logger.info(f"realm: choosing {target} (of {len(names)}: {', '.join(names[:8])}...)")
    await ui.click_center(client, entry)
    await asyncio.sleep(1.0)
    await _dump(client, "chosen")
    go = await _find_button(client.root_window, GO_WORDS)
    if go is None:
        logger.warning("realm: no Go button after choosing; windows saved to state/realm_chosen.txt")
        await _close(client)
        return False
    zone = await client.zone_name()
    await ui.click_center(client, go)
    await asyncio.sleep(1.5)
    box = await ui.modal_box(client)
    if box is not None:
        logger.info(f"realm: the game asks: {(await ui.modal_text(box))[:100]!r}; yes")
        await ui.press_modal_button(client, box, "centerButton")
    tried = data.get("tried", [])
    data["tried"] = [target] if target in tried else [*tried, target]  # all tried: start over
    _save(data)
    await wait_for_loading(client, appear_timeout=15.0)
    for _ in range(30):
        if await client.zone_name():
            break
        await asyncio.sleep(1.0)
    if await _find_button(client.root_window, REALMS_WORDS) is not None:
        await _dump(client, "after")
        logger.warning("realm: the realm list is still open; windows saved to state/realm_after.txt")
        await _close(client)
        return False
    logger.success(f"realm: now in {target} ({await client.zone_name() or zone})")
    return True
