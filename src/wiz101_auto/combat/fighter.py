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


_snapped: set[str] = set()


def _snapshot(path: str):
    """Save one screenshot per path per session (debugging UI positions)."""
    if path in _snapped:
        return
    _snapped.add(path)
    try:
        from ..screenshot import save_screenshot

        save_screenshot(path)
        logger.info(f"saved {path}")
    except Exception as exc:
        logger.debug(f"screenshot failed: {exc!r}")


# Spots (fraction of width, fraction of height) to try on the thin action-row
# buttons (Pass, Flee); a negative height is just above the row.
ACTION_SPOTS = [(fx, fy) for fy in (0.5, 0.2, -0.3, 0.8) for fx in (0.5, 0.3, 0.7)]  # never up into the cards


def _ordered(spots: list, first) -> list:
    """`spots` with `first` (one that worked before) tried first."""
    return [first, *[s for s in spots if s != first]] if first in spots else list(spots)


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
        self._fleeing = False
        self._want_flee = False  # this fight isn't needed: try to flee every round
        self._flee_spot: tuple[float, float] | None = None  # where on Flee a click worked
        self._action_spot: tuple[float, float] | None = None  # where on Pass a click worked
        self._flee_method = ""
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

    async def _visible_named(self, name: str):
        for w in await self.client.root_window.get_windows_with_name(name):
            if await w.is_visible():
                return w
        return None

    async def flee(self) -> bool:
        """Flee on purpose, then answer Yes to "Are you sure you want to flee?
        You will lose all your Mana..." (mana comes back quickly from wisps).
        True once the flee went through."""
        self._fleeing = True
        flee_btn = await self._visible_named("Flee")
        # Hit areas don't sit exactly on the rects WizWalker computes, and the
        # action buttons are thin: probe points inside Flee's own width (well
        # clear of Draw and Pass), at a few heights, until the confirmation shows.
        # The spot that works is remembered for the rest of the session.
        attempts = []
        if flee_btn is not None:
            r = await flee_btn.scale_to_client()
            w, h = r.x2 - r.x1, r.y2 - r.y1
            spots = _ordered(ACTION_SPOTS, self._flee_spot or self._action_spot)
            for fx, fy in spots:
                x, y = int(r.x1 + w * fx), int(r.y1 + h * fy)

                async def click_at(x=x, y=y):
                    await ui.button_click(self.client, x, y)

                attempts.append((f"Flee at ({fx:.2f}, {fy:.2f}) = ({x}, {y})", click_at, (fx, fy)))
        attempts.append(("WizWalker flee_button", self.flee_button, None))
        for how, click, spot in attempts:
            try:
                await click()
            except Exception as exc:
                logger.debug(f"flee ({how}) click failed: {exc!r}")
                continue
            seen = ""
            for _ in range(5):
                await asyncio.sleep(0.3)
                if not await self.client.in_battle():
                    logger.info(f"fled ({how})")
                    return True
                box = await ui.modal_box(self.client)
                text = (await ui.modal_text(box)) if box is not None else ""
                if text and text != seen:
                    seen = text
                    logger.debug(f"flee ({how}): message box {text[:80]!r}")
                if box is not None and "flee" in text.lower():
                    _snapshot("state/flee_box.png")  # to see where its buttons really are
                    if await ui.press_modal_button(self.client, box, "centerButton"):
                        logger.info(f"confirmed fleeing ({how})")
                        self._flee_method = how
                        if spot is not None:
                            self._flee_spot = spot
                        return True
            logger.debug(f"flee ({how}): no confirmation")
        return False

    async def cancel_flee_box(self) -> bool:
        if self._fleeing:
            return False  # we asked to flee: leave the confirmation to flee()
        box = await ui.modal_box(self.client)
        if box is None or "flee" not in (await ui.modal_text(box)).lower():
            return False
        logger.info("cancelling a flee confirmation")
        # The left-shifted click misses message-box buttons; click No at its center.
        if not await ui.press_modal_button(self.client, box, "rightButton"):
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
        # Only a visible one: a hidden "Focus" earlier in the tree would take the click.
        button = await self._visible_named("Focus")
        if button is None:
            logger.warning("no visible Pass button; the round will time out")
            return
        # The action row is thin and its hit areas don't sit exactly on the rects
        # WizWalker computes: try spots inside Pass until it goes through (a
        # submitted turn hides the hand; the Pass button itself stays visible),
        # and remember the spot.
        r = await button.scale_to_client()
        w, h = r.x2 - r.x1, r.y2 - r.y1
        for fx, fy in _ordered(ACTION_SPOTS, self._action_spot):
            before = await self._hand_size()
            await ui.button_click(self.client, int(r.x1 + w * fx), int(r.y1 + h * fy))
            if before == 0 or await self._committed(before, timeout=1.5):
                if self._action_spot != (fx, fy):
                    logger.info(f"Pass went through clicking at ({fx:.2f}, {fy:.2f}) of the button")
                self._action_spot = (fx, fy)
                return
        logger.warning("Pass didn't go through at any spot; the round will time out")

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
        self._flee_tried_this_round = False
        discards_left = self.max_discards
        for _ in range(MAX_STEPS_PER_ROUND):
            snap = await read_battle(self)
            battle = snap.battle
            for c in battle.cards:
                if c.name in self._unusable:
                    c.castable = False

            if self.flee_below and battle.me.health_ratio < self.flee_below:
                logger.warning(f"health {battle.me.health}/{battle.me.max_health}: fleeing")
                await self.flee()
                return

            # A flee confirmation already open (e.g. from before a restart): answer
            # Yes when this fight isn't wanted, otherwise No (fleeing costs all mana).
            box = await ui.modal_box(self.client)
            if box is not None and "flee" in (await ui.modal_text(box)).lower():
                if not self._judged_fight and self.unneeded_fight:
                    self._judged_fight = True
                    self._want_flee = await self.unneeded_fight(battle)
                if self._want_flee and await ui.press_modal_button(self.client, box, "centerButton"):
                    logger.info("confirmed fleeing (the confirmation was already open)")
                    return
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

            # Decide once, on the first round, whether this fight is worth having;
            # then keep trying to flee every round until it goes through.
            if self.unneeded_fight and not self._judged_fight:
                self._judged_fight = True
                if await self.unneeded_fight(battle):
                    names = ", ".join(e.name for e in battle.enemies)
                    logger.info(f"fight with {names} isn't needed for the quest: fleeing")
                    self._want_flee = True
            if self._want_flee and not self._flee_tried_this_round:
                self._flee_tried_this_round = True
                if await self.flee():
                    return
                logger.warning("flee didn't go through; playing this round and trying again next round")
                self._fleeing = False  # let a stray confirmation be cancelled while we play

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
        self._fleeing = False
        self._want_flee = False
        self._last_plan = ""
        await super().handle_combat()
        self.fights += 1
        logger.success(f"combat over (fights so far: {self.fights})")
