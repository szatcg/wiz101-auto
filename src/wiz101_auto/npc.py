"""NPC services menu: the list shown when an NPC has several quests or services.

The layout of this window hasn't been mapped yet, so the handler is generic:
it collects the clickable entries, prefers ones whose text overlaps the
current objective, and remembers what it already tried for this objective so
repeated visits walk through the options until the objective changes.
"""

from __future__ import annotations

import re
import time
from pathlib import Path

from loguru import logger

from . import ui

NPC_SERVICES = ["WorldView", "NPCServicesWin"]
_SKIP_NAMES = (
    "exit",
    "close",
    "cancel",
    "scroll",
    "arrow",
    "pageup",
    "pagedown",
    "up",
    "down",
    "prev",
    "next",
)
_WORD = re.compile(r"[a-z]{3,}")
_STOP_WORDS = {"the", "and", "talk", "with", "for", "from", "into", "your", "you"}


def _words(text: str) -> set[str]:
    return {w for w in _WORD.findall(text.lower()) if w not in _STOP_WORDS}


def rank_options(labels: list[str], objective: str) -> list[int]:
    """Indexes of `labels`, best match for the objective first (stable otherwise)."""
    target = _words(objective)
    return sorted(range(len(labels)), key=lambda i: -len(_words(labels[i]) & target))


async def _text_of(window, depth: int = 0) -> str:
    parts = []
    try:
        t = await window.maybe_text()
        if t:
            parts.append(re.sub(r"<[^>]+>", "", t).strip())
    except Exception:
        pass
    if depth < 3:
        try:
            for c in await window.children():
                parts.append(await _text_of(c, depth + 1))
        except Exception:
            pass
    return " ".join(p for p in parts if p)


async def _clickable(window, out: list, depth: int = 0):
    if depth > 8:
        return
    try:
        children = await window.children()
    except Exception:
        return
    for c in children:
        try:
            if not await c.is_visible():
                continue
            name = (await c.name() or "").lower()
            kind = (await c.maybe_read_type_name() or "").lower()
        except Exception:
            continue
        skip = any(name == s or name.startswith(s) for s in _SKIP_NAMES)
        if ("button" in kind or "checkbox" in kind) and not skip:
            out.append(c)
        await _clickable(c, out, depth + 1)


class ServicesMenu:
    def __init__(self, client):
        self.client = client
        self._tried: dict[str, set[str]] = {}
        self._dumped = False

    async def is_open(self) -> bool:
        return await ui.is_visible(self.client, NPC_SERVICES)

    async def choose(self, objective: str) -> bool:
        """Click the most promising untried entry. Returns False if nothing left to try."""
        win = await ui.window_at(self.client, NPC_SERVICES)
        if win is None:
            return False
        if not self._dumped:
            Path("state").mkdir(exist_ok=True)
            path = Path("state") / f"npc_services_window_{int(time.time())}.txt"
            lines = await ui.dump_tree(win, max_depth=10, only_visible=False, with_types=True)
            path.write_text("\n".join(lines))
            logger.info(f"NPC menu layout saved to {path} (send this file for tuning)")
            self._dumped = True

        options: list = []
        await _clickable(win, options)
        labels = []
        for i, o in enumerate(options):
            text = await _text_of(o)
            labels.append(text or f"{await o.name()}#{i}")
        logger.info(f"NPC menu options: {labels}")

        tried = self._tried.setdefault(objective, set())
        for i in rank_options(labels, objective):
            if labels[i] in tried:
                continue
            tried.add(labels[i])
            logger.info(f"NPC menu: choosing {labels[i]!r} for objective {objective!r}")
            await self.client.mouse_handler.click_window(options[i])
            return True

        logger.warning("NPC menu: every option tried without progress; starting over next visit")
        tried.clear()
        return False
