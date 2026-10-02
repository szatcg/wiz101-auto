"""Plays each combat round using the decision logic in `brain.py`."""

from __future__ import annotations

import asyncio
import dataclasses
import json
import time
from collections import Counter
from pathlib import Path

from loguru import logger
from wizwalker.combat import CombatHandler

from .. import ui
from ..deck import load_deck_counts
from ..dungeons import DungeonMemory
from .brain import Strategy, decide, out_of_attacks, plan_fight, predicted_damage, prism_first
from .model import Action, ActionKind, Card, EffectKind
from .reader import read_battle

MAX_STEPS_PER_ROUND = 8
CARD_INFO = Path("state") / "card_info.pkl"


def _load_card_info() -> dict:
    import pickle

    try:
        return pickle.loads(CARD_INFO.read_bytes())
    except Exception:
        return {}


def _save_card_info(info: dict) -> None:
    import pickle

    try:
        CARD_INFO.parent.mkdir(exist_ok=True)
        CARD_INFO.write_bytes(pickle.dumps({k: dataclasses.replace(v, index=-1) for k, v in info.items()}))
    except Exception as exc:
        logger.debug(f"could not save card info: {exc!r}")
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


def _deck_name(card) -> str:
    """How the deck list names a card: its spell template ("Bloodbat")."""
    return card.template_name or card.name


FLEE_FILE = Path("state") / "flee.request"  # created by the user: flee this fight

MY_STATS = Path("state") / "my_stats.json"


def _minion_text(battle) -> str:
    """Our minion's health in the round line (calibrate.py measures how much
    enemy damage goes to it)."""
    m = next((a for a in battle.allies if a.is_minion and a.health > 0), None)
    return f" minion={m.health}/{m.max_health}" if m else ""


HAND_MAX = 7  # cards a hand refills to each round
PLAN_FILE = Path("state") / "battle_plan.json"


def _write_plan(battle, action, strategy, discards: int, gone=None) -> None:
    """The plan the stream page shows (state/battle_plan.json): the fight played
    on from this decision (sim.plan_preview); rewritten whenever the decision
    changes (a heal comes in), cleared when the fight ends."""
    try:
        data: dict = {"active": False, "time": time.time()}
        if battle is not None and action is not None:
            from .sim import category, plan_preview

            data = plan_preview(battle, action, strategy, discards=discards)
            data.update({
                "active": True, "time": time.time(), "round": battle.round,
                "pips": battle.pips, "power": battle.power_pips,
                "me": {"hp": battle.me.health, "max": battle.me.max_health},
                "now": {"kind": category(action), "spell": action.card.name if action.card else "",
                        "target": action.target.name if action.target else "", "why": action.reason},
                "deck": deck_tracker(battle, gone or {}),
                "dig": dig_odds(battle, action),
            })
        PLAN_FILE.write_text(json.dumps(data), encoding="utf-8")
    except Exception as exc:
        logger.debug(f"battle plan not written: {exc!r}")


def deck_tracker(battle, gone) -> list[dict]:
    """The deck this fight: per card, copies still to draw, in hand and used
    (cast or discarded), for the stream page; a deck that isn't known: []."""
    if not battle.deck_known:
        return []
    rows: dict[str, dict] = {}

    def row(card) -> dict:
        key = _deck_name(card)
        return rows.setdefault(key, {"name": card.name, "left": 0, "hand": 0, "used": int(gone.get(key, 0))})

    for c in battle.upcoming:
        row(c)["left"] += 1
    for c in battle.cards:
        if not c.treasure and not c.item:
            row(c)["hand"] += 1
    for key, n in gone.items():
        if key not in rows and n:
            rows[key] = {"name": key, "left": 0, "hand": 0, "used": int(n)}
    return sorted(rows.values(), key=lambda r: (-r["left"], r["name"]))


def draw_chance(left: int, wanted: int, draws: int) -> float:
    """Chance of at least one of `wanted` cards among `draws` from `left`."""
    from math import comb

    if wanted <= 0 or draws <= 0 or left <= 0:
        return 0.0
    draws = min(draws, left)
    return 1.0 - comb(left - wanted, draws) / comb(left, draws) if left - wanted >= draws else 1.0


def dig_odds(battle, action) -> dict:
    """What the move digs for and the chances of drawing it next round (the
    stream's deck tracker): {"want": [card names], "draws": n, "odds": {name:
    %}, "any": %}; {} when the move isn't a dig."""
    why = (action.reason or "").lower()
    kinds = set()
    if why.startswith("dig") or "digging for" in why:
        if "blade" in why or why.startswith("dig"):
            kinds.add(EffectKind.BLADE)
        if "trap" in why or why.startswith("dig"):
            kinds.add(EffectKind.TRAP)
        if "hit" in why:
            kinds.add(EffectKind.DAMAGE)
    if not kinds or not battle.upcoming:
        return {}
    wanted = [c for c in battle.upcoming if kinds & set(c.kinds) and not c.is_enchant]
    if not wanted:
        return {}
    tossed = len(action.plan_cards) if action.plan_cards else (1 if action.card is not None else 0)
    draws = max(1, HAND_MAX - len(battle.cards) + tossed)
    left = len(battle.upcoming)
    names = sorted({c.name for c in wanted})
    odds = {n: round(100 * draw_chance(left, sum(1 for c in wanted if c.name == n), draws)) for n in names}
    any_odds = round(100 * draw_chance(left, len(wanted), draws))
    return {"want": names, "draws": draws, "odds": odds, "any": any_odds}


def _save_my_stats(me) -> None:
    """Our wizard as the game reads it (max health, gear's damage bonus and
    resists), for the simulator's fights from the start (sim.simulate)."""
    try:
        data = {"max_health": me.max_health, "damage_bonus": dict(me.damage_bonus),
                "resist": dict(me.resist or {}), "school": me.school}
        MY_STATS.write_text(json.dumps(data), encoding="utf-8")
    except Exception:
        pass


class Fighter(CombatHandler):
    def __init__(self, client, strategy: Strategy, *, max_discards: int = 2, flee_below: float = 0.0,
                 rollouts: bool = True):
        super().__init__(client)
        self.strategy = strategy
        # Hard fights: each move played out in the simulator (combat/rollout.py).
        self.planner = None
        if rollouts:
            from .rollout import RolloutPlanner

            self.planner = RolloutPlanner()
        self.max_discards = max_discards
        self.flee_below = flee_below
        self.fights = 0
        self.boss_fights = 0  # fights that had a boss in them (a loot chest may spawn after)
        self.combat_ended_at = 0.0  # monotonic time the last fight ended
        self.fled = False  # the last fight ended by fleeing (not a defeat)
        self.last_boss_names: list[str] = []  # bosses in the current/last fight (farm runs end on one)
        self.last_enemy_names: list[str] = []  # enemies of the current/last fight
        self.last_bosses: set[str] = set()  # which of them the game marks as bosses
        self.may_flee = None  # async () -> bool: whether fleeing is allowed here
        self._had_boss = False
        self._unusable: set[str] = set()  # cards whose cast didn't register this round
        self._prismed: set[str] = set()  # enemies prismed this fight
        self._summons = 0  # minions summoned this fight
        self._gone: Counter[str] = Counter()
        self._dig: tuple[int, list[str]] | None = None  # (round, cards still to discard) of a dig
        self._deck: dict[str, int] = {}
        # deck spell name -> a card seen in hand (for planning); kept across
        # restarts (after one, Humongofrog wasn't known to be still in the
        # deck and the pips went on a Minotaur instead).
        self._card_info: dict[str, Card] = _load_card_info()
        self._card_click_x = 0.25  # the hit area sits left of the reported card rect
        # async (battle) -> bool, set by the bot in quest mode; True means flee.
        self.unneeded_fight = None
        self._judged_fight = False
        self._unknown_left = 0  # deck cards to come not yet seen
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
                await self.client.mouse_handler.click_window(await self._target_window(target))
        except (ValueError, AttributeError) as exc:
            # The round ended (or the target died) while we were clicking.
            logger.debug(f"cast click failed: {exc!r}")
            return False
        return True

    async def _target_window(self, target):
        """Where to click to pick `target`: its health text, else its name, else
        its whole nameplate. In a 4-player fight our own nameplate had no
        Health child, so a Mythblade on ourselves never got its target."""
        try:
            return await target.get_health_text_window()
        except ValueError:
            pass
        try:
            w = await target.get_name_text_window()
            logger.debug("targeting by the name window (no health window)")
            return w
        except ValueError:
            pass
        control = getattr(target, "_combatant_control", None)
        if control is None:
            raise ValueError("no window to click for the target")
        logger.debug("targeting by the nameplate (no health or name window)")
        return control

    async def _remember_bosses(self, battle):
        """Bosses fought inside a dungeon: remember which dungeon (for boss farming)."""
        bosses = [e.name for e in battle.enemies if e.is_boss]
        self.last_enemy_names = [e.name for e in battle.enemies]
        self.last_bosses = {e.name for e in battle.enemies if e.is_boss}
        if bosses:
            self.last_boss_names = bosses
        try:
            zone = await self.client.zone_name() or ""
            mem = DungeonMemory.load()
            if "interiors" not in zone.lower() and zone not in mem.dungeons:
                return  # (Katzenstein's Lab is a dungeon without "Interiors" in its name)
            for e in battle.enemies:
                if e.is_boss and mem.record_boss(e.name, zone):
                    logger.info(f"remembered boss {e.name} in {zone}")
        except Exception as exc:
            logger.debug(f"could not remember bosses: {exc!r}")

    async def _out_of_battle(self) -> bool:
        return not await self.client.in_battle()

    async def _visible_named(self, name: str):
        for w in await self.client.root_window.get_windows_with_name(name):
            if await w.is_visible():
                return w
        return None

    async def flee(self) -> bool:
        """Flee (see _flee); remembers that this fight ended by fleeing, which
        also moves the wizard away but isn't a defeat. `may_flee` (set by the
        quester) can forbid it: fleeing in a dungeon resets it."""
        if self.may_flee is not None and not await self.may_flee():
            logger.info("not fleeing: in a dungeon with no mark to return to; fighting on")
            return False
        fled = await self._flee()
        self.fled = self.fled or fled
        return fled

    async def _flee(self) -> bool:
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
            for _ in range(22):  # the confirmation can take ~5s to show up
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

                    done = self._out_of_battle
                    if await ui.press_modal_button(self.client, box, "centerButton", done=done):
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

        # No minions at all (the player: slow, and their worth is hard to
        # judge): never summoned, and a minion card drawn is discarded.
        self.strategy.no_minions = True
        self._unusable.clear()  # a card that failed last round may sit in a working slot now
        self._flee_tried_this_round = False
        discards_left = self.max_discards
        for _ in range(MAX_STEPS_PER_ROUND):
            snap = await read_battle(self)
            battle = snap.battle
            for c in battle.cards:
                if c.name in self._unusable:
                    c.castable = False

            if FLEE_FILE.exists():
                # The user asked (state/flee.request): flee whatever the rules say.
                if not self._flee_tried_this_round:
                    self._flee_tried_this_round = True
                    logger.warning("flee requested (state/flee.request): fleeing")
                    if await self._flee():
                        self.fled = True
                        FLEE_FILE.unlink(missing_ok=True)
                        return

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
                if self._want_flee and await ui.press_modal_button(
                    self.client, box, "centerButton", done=self._out_of_battle
                ):
                    logger.info("confirmed fleeing (the confirmation was already open)")
                    return
            if await self.cancel_flee_box():
                continue
            if await ui.dismiss_notice(self.client):
                continue

            battle.upcoming = self._upcoming(battle)
            # Known only when every card still to come has been seen (after a
            # restart Humongofrog and Cyclops weren't yet: "no attack cards
            # left" fled a fight on its first round).
            battle.deck_known = bool(self._deck) and not self._unknown_left
            if out_of_mana(battle):
                # Nothing castable without mana: passing until defeated loses
                # anyway; fleeing keeps our health.
                if not self._flee_tried_this_round:
                    self._flee_tried_this_round = True
                    logger.warning(f"out of mana ({battle.me.mana}) and nothing castable: fleeing")
                    if await self.flee():
                        return
                logger.warning("could not flee; passing")
                await self.pass_button()
                return

            if battle.deck_known and out_of_attacks(battle) and not self._flee_tried_this_round:
                # Passing until we die (Shakes O'Leary healed back up) loses
                # the fight anyway; fleeing keeps our health for the retry.
                self._flee_tried_this_round = True
                logger.warning("no attack cards left in hand or deck: fleeing to try again")
                if await self.flee():
                    return

            if not self._judged_fight:
                self._had_boss = self._had_boss or any(e.is_boss for e in battle.enemies)
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

            hand = ", ".join(f"{c.name}{'' if c.castable else '(x)'}" for c in battle.cards)
            logger.debug(f"hand: {hand}; deck left: {len(battle.upcoming)}")
            plan = plan_fight(battle, self.strategy).text
            if plan != self._last_plan:
                logger.info(plan)
                self._last_plan = plan
            # (and any myth prism the game shows hanging on an enemy)
            battle.prismed = set(self._prismed) | {e.name for e in battle.enemies if e.myth_prism}
            battle.summoned = self._summons
            _save_my_stats(battle.me)
            dig = self._dig if self._dig and self._dig[0] == battle.round else None
            if dig is not None:
                card = next((c for c in battle.cards if c.name in dig[1]), None)
                if card is not None and discards_left > 0:
                    dig[1].remove(card.name)
                    action = Action(ActionKind.DISCARD, card, reason="dig: discarding for the plan's draws")
                else:
                    self._dig = None
                    logger.info(f"[round {battle.round}] dig done: passing, keeping the pips")
                    await self.pass_button()
                    return
            else:
                action = decide(battle, self.strategy, discards_left=discards_left)
            reshuffling = "reshuffle" in (action.reason or "").lower() or (
                action.card is not None and action.card.name.strip().lower() == "reshuffle")
            if self.planner is not None and not reshuffling and dig is None:
                # (The simulator knows nothing of Reshuffle: its plays and the
                # pips saved for it are the brain's.)
                action = await self.planner.choose(battle, action, self.strategy, discards_left)
            action = prism_first(battle, action)  # never a big hit into a resist a prism in hand turns
            digging = (action.reason or "").startswith("dig") and action.plan_cards
            if action.kind is ActionKind.PASS and digging:
                # The rollouts' dig: the discards first (free), then the pass.
                self._dig = (battle.round, [c.name for c in battle.cards if c.index in action.plan_cards])
                logger.info(f"[round {battle.round}] {action.reason}")
                continue
            _write_plan(battle, action, self.strategy, discards_left, self._gone)
            foes = ", ".join(
                f"{e.name}{'*' if e.is_boss else ''} {e.health}/{e.max_health}{' dead' if e.is_dead else ''}"
                for e in battle.enemies
            )
            logger.info(
                f"[round {battle.round}] pips={battle.pips}+{battle.power_pips}P "
                f"hp={battle.me.health}/{battle.me.max_health}{_minion_text(battle)} vs {foes} -> "
                f"{action.describe()}"
            )
            predicted = predicted_damage(battle, action)
            if predicted:  # for the stream page's health bars
                logger.info("predict: " + ", ".join(f"{i}={d}" for i, d in predicted.items()))

            if action.kind is ActionKind.PASS or action.card is None:
                await self.pass_button()
                return

            live_card = snap.cards[action.card.index]

            if action.kind is ActionKind.ENCHANT:
                self._gone[_deck_name(action.card)] += 1
                await live_card.cast(snap.cards[action.target_card.index])
                await asyncio.sleep(0.3)
                continue

            if action.kind is ActionKind.DISCARD:
                self._gone[_deck_name(action.card)] += 1
                r = await live_card._spell_window.scale_to_client()
                x = int(r.x1 + (r.x2 - r.x1) * self._card_click_x)
                await self.client.mouse_handler.click(x, int((r.y1 + r.y2) / 2), right_click=True)
                await asyncio.sleep(1.0)
                discards_left -= 1
                continue

            target = snap.members.get(id(action.target)) if action.target else None
            if "prism" in action.card.name.lower() and action.target:
                self._prismed.add(action.target.name)  # waits on them for our next myth hit
            elif action.card.is_damage and action.card.school.lower() == "myth":
                # That hit uses the prism (a hit-all: on every enemy it lands on).
                if action.card.is_aoe:
                    self._prismed.clear()
                elif action.target:
                    self._prismed.discard(action.target.name)
            if EffectKind.SUMMON in action.card.kinds:
                self._summons += 1
            self._gone[_deck_name(action.card)] += 1
            before = await self._hand_size()
            if not await self._cast_at(live_card, target, self._card_click_x):
                return  # the round is over
            if await self._committed(before):
                return
            await self._log_failed_cast(snap, action, target)
            # A window over the cards (the pet level-up) eats every click: close it.
            from ..upkeep import clear_popups

            try:
                await clear_popups(self.client)
            except Exception as exc:
                logger.debug(f"clearing popups failed: {exc!r}")
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

    def _upcoming(self, battle) -> list[Card]:
        """Deck cards not yet drawn, cast or discarded this fight (the ones
        still able to come), known from cards seen in hand before."""
        in_hand: Counter[str] = Counter()
        for c in battle.cards:
            if _deck_name(c) not in self._card_info:
                self._card_info[_deck_name(c)] = c
                _save_card_info(self._card_info)
            in_hand[_deck_name(c)] += 1
        out = []
        self._unknown_left = 0  # deck cards still to come that we haven't seen yet (what they do)
        for name, copies in self._deck.items():
            left = copies - self._gone[name] - in_hand[name]
            info = self._card_info.get(name)
            if left > 0 and info is not None:
                out += [dataclasses.replace(info, index=-1, castable=True)] * left
            elif left > 0:
                self._unknown_left += left
        return out

    async def handle_combat(self):
        self._unusable.clear()
        self._prismed: set[str] = set()
        self._summons = 0
        self._gone: Counter[str] = Counter()  # deck cards cast or discarded this fight
        self._dig = None  # (a dig doesn't carry over)
        self._deck = load_deck_counts()
        self._judged_fight = False
        self._fleeing = False
        self._want_flee = False
        self._last_plan = ""
        self._had_boss = False
        self.fled = False
        await super().handle_combat()
        self.fights += 1
        self.combat_ended_at = time.monotonic()
        if self._had_boss:
            self.boss_fights += 1
        logger.success(f"combat over (fights so far: {self.fights})")
        _write_plan(None, None, None, 0)
