"""Plays each combat round using the decision logic in `brain.py`."""

from __future__ import annotations

import asyncio

from loguru import logger
from wizwalker.combat import CombatHandler

from .brain import Strategy, decide
from .model import ActionKind
from .reader import read_battle

MAX_STEPS_PER_ROUND = 8


class Fighter(CombatHandler):
    def __init__(self, client, strategy: Strategy, *, max_discards: int = 2, flee_below: float = 0.0):
        super().__init__(client)
        self.strategy = strategy
        self.max_discards = max_discards
        self.flee_below = flee_below
        self.fights = 0
        self._unusable: set[str] = set()  # cards whose cast didn't register this fight

    async def _hand_size(self) -> int:
        try:
            return len(await self.get_cards())
        except Exception:
            return 0

    async def _committed(self, before: int, timeout: float = 3.0) -> bool:
        """A committed cast hides the hand (or at least removes the card)."""
        deadline = asyncio.get_running_loop().time() + timeout
        while asyncio.get_running_loop().time() < deadline:
            if await self._hand_size() < before:
                return True
            await asyncio.sleep(0.2)
        return False

    async def _log_failed_cast(self, snap, action, target):
        cards = ", ".join(
            f"#{c.index} {c.name} (pips {c.pip_cost}, castable={c.castable}, treasure={c.treasure})"
            for c in snap.battle.cards
        )
        where = "no target"
        if target is not None:
            try:
                w = await target.get_health_text_window()
                rect = await w.scale_to_client()
                where = f"target health window visible={await w.is_visible()} rect={rect}"
            except Exception as exc:
                where = f"target health window unreadable: {exc!r}"
        logger.warning(f"cast of {action.card.name} did not register; hand: {cards}; {where}")

    async def handle_round(self):
        discards_left = self.max_discards
        for _ in range(MAX_STEPS_PER_ROUND):
            snap = await read_battle(self)
            battle = snap.battle
            for c in battle.cards:
                if c.name in self._unusable:
                    c.castable = False

            if self.flee_below and battle.me.health_ratio < self.flee_below:
                logger.warning(f"health {battle.me.health}/{battle.me.max_health}: fleeing")
                await self.flee_button()
                return

            action = decide(battle, self.strategy, discards_left=discards_left)
            logger.info(
                f"[round {battle.round}] pips={battle.pips}+{battle.power_pips}P "
                f"hp={battle.me.health}/{battle.me.max_health} -> {action.describe()}"
            )

            if action.kind is ActionKind.PASS or action.card is None:
                await self.pass_button()
                return

            live_card = snap.cards[action.card.index]

            if action.kind is ActionKind.ENCHANT:
                await live_card.cast(snap.cards[action.target_card.index])
                await asyncio.sleep(0.3)
                continue

            if action.kind is ActionKind.DISCARD:
                await live_card.discard()
                discards_left -= 1
                continue

            target = snap.members.get(id(action.target)) if action.target else None
            before = await self._hand_size()
            await live_card.cast(target)
            if await self._committed(before):
                return
            await self._log_failed_cast(snap, action, target)
            if target is not None:
                # The card is probably still selected; click the target again.
                try:
                    await self.client.mouse_handler.click_window(await target.get_health_text_window())
                except Exception as exc:
                    logger.debug(f"retargeting failed: {exc!r}")
                if await self._committed(before):
                    logger.info("cast registered after clicking the target again")
                    return
            logger.warning(f"giving up on {action.card.name} for this fight")
            self._unusable.add(action.card.name)

        logger.warning("too many steps this round, passing")
        await self.pass_button()

    async def handle_combat(self):
        self._unusable.clear()
        await super().handle_combat()
        self.fights += 1
        logger.success(f"combat over (fights so far: {self.fights})")
