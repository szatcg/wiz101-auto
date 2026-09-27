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

    async def handle_round(self):
        discards_left = self.max_discards
        for _ in range(MAX_STEPS_PER_ROUND):
            snap = await read_battle(self)
            battle = snap.battle

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
            await live_card.cast(target)
            return

        logger.warning("too many steps this round, passing")
        await self.pass_button()

    async def handle_combat(self):
        await super().handle_combat()
        self.fights += 1
        logger.success(f"combat over (fights so far: {self.fights})")
