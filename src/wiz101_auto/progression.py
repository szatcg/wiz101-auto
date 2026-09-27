"""Character progression: detect level-ups and new spells, keep the deck current,
and (experimentally) train spells when a trainer window is open.

State is stored in `state/progress.json` so restarts don't redo work.
"""

from __future__ import annotations

import asyncio
import json
import time
from pathlib import Path

from loguru import logger

from . import ui
from .config import ProgressionConfig
from .deck import current_school, rebuild_deck

TRAINER = ["WorldView", "NPCTrainingGUI"]
STATE_DIR = Path("state")


class Progression:
    def __init__(self, client, cfg: ProgressionConfig):
        self.client = client
        self.cfg = cfg
        self.state_file = STATE_DIR / "progress.json"
        self.state = self._load()
        self.level = 0
        self.school = cfg.school
        self._last_check = 0.0
        self._pending_reason = "startup" if cfg.rebuild_on_start else ""
        self._trainer_dumped = False

    def _load(self) -> dict:
        try:
            return json.loads(self.state_file.read_text())
        except Exception:
            return {}

    def _save(self):
        STATE_DIR.mkdir(exist_ok=True)
        self.state_file.write_text(json.dumps(self.state, indent=2))

    async def start(self):
        self.level = await self.client.stats.reference_level()
        if not self.school:
            self.school = await current_school(self.client)
        logger.info(f"wizard: level {self.level} {self.school or '(school unknown)'}")
        if self.state.get("level") and self.level > self.state["level"]:
            self._pending_reason = f"levelled up to {self.level} since last run"

    async def tick(self):
        """Call while the wizard is free. Rebuilds the deck when something changed."""
        if not self.cfg.enabled:
            return
        level = await self.client.stats.reference_level()
        if level > self.level:
            logger.success(f"LEVEL UP: {self.level} -> {level}")
            self.level = level
            self._pending_reason = f"level {level}"
            if self.cfg.remind_to_train:
                logger.warning(
                    "new spells may be available: visit your school professor in Ravenwood "
                    "(the bot trains automatically when the training window opens)"
                )
        if not self._pending_reason and self.cfg.check_minutes > 0:
            if time.monotonic() - self._last_check > self.cfg.check_minutes * 60:
                self._pending_reason = "periodic check"
        if self._pending_reason:
            reason, self._pending_reason = self._pending_reason, ""
            await self.update_deck(reason)

    async def update_deck(self, reason: str, *, force: bool = False):
        self._last_check = time.monotonic()
        logger.info(f"checking spellbook ({reason})")
        try:
            known, plan = await rebuild_deck(self.client, self.school, self.cfg.deck, dry_run=True)
        except Exception as exc:
            logger.opt(exception=exc).warning("could not read the spellbook")
            return
        names = sorted(s.name for s in known)
        new = sorted(set(names) - set(self.state.get("known_spells", [])))
        if new:
            logger.success(f"new spells: {', '.join(new)}")
        if force or new or self.state.get("deck") != plan.totals:
            try:
                await rebuild_deck(self.client, self.school, self.cfg.deck)
            except Exception as exc:
                logger.opt(exception=exc).warning("deck rebuild failed; will retry later")
                return
        self.state.update(level=self.level, known_spells=names, deck=plan.totals)
        self._save()

    # --- trainer ---------------------------------------------------------------

    async def handle_trainer(self) -> bool:
        """If a spell trainer window is open, record it and try to train. True if it was open."""
        if not await ui.is_visible(self.client, TRAINER):
            return False
        gui = await ui.window_at(self.client, TRAINER)
        if not self._trainer_dumped:
            STATE_DIR.mkdir(exist_ok=True)
            path = STATE_DIR / f"trainer_window_{int(time.time())}.txt"
            lines = await ui.dump_tree(gui, max_depth=10, only_visible=False, with_types=True)
            path.write_text("\n".join(lines))
            logger.info(f"trainer window layout saved to {path} (send this file for tuning)")
            self._trainer_dumped = True
        if self.cfg.auto_train:
            trained = await self._try_train(gui)
            logger.info(f"auto-train: trained {trained} spell(s)")
        self._pending_reason = "visited trainer"
        return True

    async def _try_train(self, gui) -> int:
        """Best-effort: click each trainable spell entry, then the Train button.

        The trainer UI hasn't been mapped yet. This looks for spell entries and a
        button named like 'Train'/'Learn', and confirms any popup.
        """
        entries, buttons = [], []
        for w in await _descendants(gui):
            try:
                if not await w.is_visible():
                    continue
                name = (await w.name()).lower()
                type_name = (await w.maybe_read_type_name()).lower()
            except Exception:
                continue
            if "button" in type_name and ("train" in name or "learn" in name):
                buttons.append(w)
            elif "spell" in type_name and "list" not in type_name:
                entries.append(w)
        if not buttons:
            logger.warning("auto-train: no Train button found; train manually this time")
            return 0

        trained = 0
        for entry in entries[:12]:
            try:
                await self.client.mouse_handler.click_window(entry)
                await asyncio.sleep(0.4)
                await self.client.mouse_handler.click_window(buttons[0])
                await asyncio.sleep(0.8)
                for path in (_MODAL_LEFT, ui.MODAL_CENTER_BUTTON):
                    if await ui.click(self.client, path):
                        trained += 1
                        await asyncio.sleep(0.8)
                        break
            except Exception as exc:
                logger.debug(f"auto-train click failed: {exc}")
        return trained


_MODAL_LEFT = [*ui.MODAL_CENTER_BUTTON[:-1], "leftButton"]


async def _descendants(window, depth: int = 0, max_depth: int = 10) -> list:
    out = []
    if depth > max_depth:
        return out
    try:
        children = await window.children()
    except Exception:
        return out
    for c in children:
        out.append(c)
        out.extend(await _descendants(c, depth + 1, max_depth))
    return out
