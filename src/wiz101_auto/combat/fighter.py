"""Plays each combat round using the decision logic in `brain.py`."""

from __future__ import annotations

import asyncio

from loguru import logger
from wizwalker.combat import CombatHandler

from .. import ui
from ..dungeons import DungeonMemory
from .brain import Strategy, decide, plan_fight
from .model import ActionKind
from .reader import read_battle

MAX_STEPS_PER_ROUND = 8
CLICK_PROBES = (0.1, 0.5)  # fallbacks if the default click point stops working


LOW_MANA = 5  # spells cost roughly a mana per pip


def out_of_mana(battle) -> bool:
    mana = battle.me.mana
    return mana is not None and mana < LOW_MANA and not any(c.castable for c in battle.cards)


class Fighter(CombatHandler):
    def __init__(self, client, strategy: Strategy, *, max_discards: int = 2, flee_below: float = 0.0):
        super().__init__(client)
        self.strategy = strategy
        self.max_discards = max_discards
        self.flee_below = flee_below
        self.fights = 0
        self._unusable: set[str] = set()  # cards whose cast didn't register this round
        self._card_click_x = 0.25  # the hit area sits left of the reported card rect
        # async (battle) -> bool, set by the bot in quest mode; True means flee.
        self.unneeded_fight = None
        self._judged_fight = False
        self._last_plan = ""

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
        rects = []
        for i, lc in sorted(snap.cards.items()):
            try:
                rects.append(f"#{i} {await lc._spell_window.scale_to_client()}")
            except Exception as exc:
                rects.append(f"#{i} {exc!r}")
        logger.warning(f"cast of {action.card.name} did not register; hand: {cards}; {where}")
        logger.warning(f"card windows: {' | '.join(rects)}")

    async def _cast_at(self, live_card, target, fx: float):
        """Like CombatCard.cast, but clicks the card at `fx` of its width."""
        try:
            r = await live_card._spell_window.scale_to_client()
            x = int(r.x1 + (r.x2 - r.x1) * fx)
            y = int((r.y1 + r.y2) / 2)
            await self.client.mouse_handler.click(x, y)
            if target is not None:
                await asyncio.sleep(1.0)
                await self.client.mouse_handler.click_window(await target.get_health_text_window())
        except (ValueError, AttributeError) as exc:
            # The round ended (or the target died) while we were clicking.
            logger.debug(f"cast click failed: {exc!r}")
            return False
        return True

    async def _remember_bosses(self, battle):
        """Bosses fought inside a dungeon: remember which dungeon (for boss farming)."""
        try:
            zone = await self.client.zone_name() or ""
            if "interiors" not in zone.lower():
                return
            mem = DungeonMemory.load()
            for e in battle.enemies:
                if e.is_boss and mem.record_boss(e.name, zone):
                    logger.info(f"remembered boss {e.name} in {zone}")
        except Exception as exc:
            logger.debug(f"could not remember bosses: {exc!r}")

    async def cancel_flee_box(self) -> bool:
        box = await ui.modal_box(self.client)
        if box is None or "flee" not in (await ui.modal_text(box)).lower():
            return False
        logger.info("cancelling a flee confirmation")
        await ui.modal_click(self.client, box, "rightButton")
        await asyncio.sleep(0.5)
        return True

    async def pass_button(self):
        """Pass by clicking the Focus button at its center: WizWalker's click goes
        through the global 25%-of-width click, which can hit Flee next to it."""
        await self.cancel_flee_box()
        for done in await self.client.root_window.get_windows_with_name("DoneWindow"):
            if await done.is_visible():
                for b in await done.get_windows_with_name("DefeatedPassButton"):
                    return await ui.click_center(self.client, b)
        for b in await self.client.root_window.get_windows_with_name("Focus"):
            return await ui.click_center(self.client, b)

    async def handle_round(self):
        # One bad round (UI changing under us, a window gone) mustn't end the fight loop.
        try:
            await self._handle_round()
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            logger.opt(exception=exc).warning("combat round failed; carrying on")

    async def _handle_round(self):
        self._unusable.clear()  # a card that failed last round may sit in a working slot now
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

            # Never flee by accident: fleeing costs all of the wizard's mana.
            if await self.cancel_flee_box():
                continue
            if await ui.dismiss_notice(self.client):
                continue

            if out_of_mana(battle):
                logger.warning(f"out of mana ({battle.me.mana}) and nothing castable: passing")
                await self.pass_button()
                return

            if not self._judged_fight:
                await self._remember_bosses(battle)

            # Decide once, on the first round, whether this fight is worth having.
            if self.unneeded_fight and not self._judged_fight:
                self._judged_fight = True
                if await self.unneeded_fight(battle):
                    names = ", ".join(e.name for e in battle.enemies)
                    logger.info(f"fight with {names} isn't needed for the quest: fleeing")
                    await self.flee_button()
                    return

            plan = plan_fight(battle, self.strategy).text
            if plan != self._last_plan:
                logger.info(plan)
                self._last_plan = plan
            action = decide(battle, self.strategy, discards_left=discards_left)
            foes = ", ".join(
                f"{e.name}{'*' if e.is_boss else ''} {e.health}/{e.max_health}{' dead' if e.is_dead else ''}"
                for e in battle.enemies
            )
            logger.info(
                f"[round {battle.round}] pips={battle.pips}+{battle.power_pips}P "
                f"hp={battle.me.health}/{battle.me.max_health} vs {foes} -> {action.describe()}"
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
                r = await live_card._spell_window.scale_to_client()
                x = int(r.x1 + (r.x2 - r.x1) * self._card_click_x)
                await self.client.mouse_handler.click(x, int((r.y1 + r.y2) / 2), right_click=True)
                await asyncio.sleep(1.0)
                discards_left -= 1
                continue

            target = snap.members.get(id(action.target)) if action.target else None
            before = await self._hand_size()
            if not await self._cast_at(live_card, target, self._card_click_x):
                return  # the round is over
            if await self._committed(before):
                return
            await self._log_failed_cast(snap, action, target)
            # Clicks on the leftmost card don't register at its center; probe
            # further left inside the card and keep whatever works.
            for fx in CLICK_PROBES:
                if fx == self._card_click_x:
                    continue
                if not await self._cast_at(live_card, target, fx):
                    return
                if await self._committed(before):
                    logger.warning(f"cast registered clicking at {fx:.0%} of the card width; using that now")
                    self._card_click_x = fx
                    return
            logger.warning(f"giving up on {action.card.name} this round")
            self._unusable.add(action.card.name)

        logger.warning("too many steps this round, passing")
        await self.pass_button()

    async def handle_combat(self):
        self._unusable.clear()
        self._judged_fight = False
        self._last_plan = ""
        await super().handle_combat()
        self.fights += 1
        logger.success(f"combat over (fights so far: {self.fights})")
