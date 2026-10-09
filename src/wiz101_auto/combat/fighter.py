"""Plays each combat round using the decision logic in `brain.py`."""

from __future__ import annotations

import asyncio
import contextlib
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
LOOKAHEAD_ON = True  # the whole-deck planner in live fights (the simulator: as good or better everywhere)
LOOKAHEAD_LATE = 15.0  # seconds into the round: past this, no whole-deck plan (the turn timer)
CAST_FAILS_MAX = 2  # a card whose cast failed this often in a fight isn't tried again in it
LOW_HP_HIT_MARGIN = 1.2  # our health this close to the next hit: the brain's hit isn't overridden
TEAM_PLAN_EVERY = 60.0  # a team fight: the stream's plan card rewritten at most this often (it takes seconds)
KILL_KEEP_ROUNDS = 3.0  # health under this many rounds of the damage we take: a hit that kills stands
POWER_CHANCE_DEFAULT = 0.8  # the power pip chance when the stat can't be read (the player: mostly power pips)
_POWER_CHANCE = POWER_CHANCE_DEFAULT  # the last read (the overlay's draws use it)
ROLLOUT_BUDGET = 20.0  # seconds into a round after which the brain's move stands (no rollouts)
ROLLOUT_GRACE = 8.0  # ... and no rollout runs past budget + this (time left for the clicks)
PLAN_FILE = Path("state") / "battle_plan.json"
BEST_DRAW_HORIZON = 12  # rounds the Best Draw panel looks ahead (bosses take more than the brain's 6)
BEST_DRAW_SECONDS = 1.5  # ... within this long (the turn timer)


def _write_plan(battle, action, strategy, discards: int, gone=None, discarded=None) -> None:
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
                "school": battle.school_pips, "myschool": (battle.me.school or "").lower(),
                "me": {"hp": battle.me.health, "max": battle.me.max_health},
                "now": {"kind": category(action), "spell": action.card.name if action.card else "",
                        "target": action.target.name if action.target else "", "why": action.reason},
                "deck": deck_tracker(battle, gone or {}, discarded or {}),
                "improve": improve_odds(battle, action),
                "crit": _plan_crits(battle, action),
                "gamble": _gamble_odds(action),
            })
        PLAN_FILE.write_text(json.dumps(data), encoding="utf-8")
    except Exception as exc:
        logger.debug(f"battle plan not written: {exc!r}")


PET_ART = Path("docs") / "spell_images" / "pet"  # the player's art for the pet's cards
PET_WORDS = ("pet", "gryphon")  # in an item card's template name: the pet's card
_ITEM_NAMES_SEEN: set[str] = set()


def _item_art(card) -> str | None:
    """The pet's own art for a card the pet gives (docs/spell_images/pet/
    <name>_spell.png), when its template name says it's the pet's. Item cards'
    template names are logged once each (to tell the pet's from the amulet's)."""
    if not getattr(card, "item", False):
        return None
    tname = (card.template_name or "").lower()
    if tname and tname not in _ITEM_NAMES_SEEN:
        _ITEM_NAMES_SEEN.add(tname)
        values = ", ".join(f"{e.kind.name.lower()} {e.value:+g}%" for e in card.effects)
        logger.info(f"item card {card.name!r}: template {card.template_name!r} ({values})")
    if not any(w in tname for w in PET_WORDS):
        return None
    f = PET_ART / f"{card.name.lower().replace(' ', '_')}_spell.png"
    return f"pet/{f.name}" if f.is_file() else None


def deck_tracker(battle, gone, discarded=None) -> list[dict]:
    """The deck this fight: per card, copies still to draw, in hand, played
    and discarded (and used: both), for the stream page; a deck that isn't
    known: []."""
    discarded = discarded or {}
    if not battle.deck_known:
        return []
    rows: dict[str, dict] = {}

    def row(card) -> dict:
        key = _deck_name(card)
        art = _item_art(card)
        if art:
            key = f"{key} ({art})"  # (the pet's Feint apart from the trained one)
        used, tossed = int(gone.get(key, 0)), int(discarded.get(key, 0))
        r = rows.setdefault(key, {"name": card.name, "left": 0, "hand": 0, "used": used,
                                  "played": max(0, used - tossed), "discarded": tossed})
        if art:
            r["art"] = art
        return r

    for c in battle.upcoming:
        row(c)["left"] += 1
    for c in battle.cards:  # (gear and treasure cards too: they're in the hand)
        row(c)["hand"] += 1
    for key, n in gone.items():
        if key not in rows and n:
            tossed = int(discarded.get(key, 0))
            rows[key] = {"name": key, "left": 0, "hand": 0, "used": int(n),
                         "played": max(0, int(n) - tossed), "discarded": tossed}
    return sorted(rows.values(), key=lambda r: (-r["left"], r["name"]))


def draw_chance(left: int, wanted: int, draws: int) -> float:
    """Chance of at least one of `wanted` cards among `draws` from `left`."""
    from math import comb

    if wanted <= 0 or draws <= 0 or left <= 0:
        return 0.0
    draws = min(draws, left)
    return 1.0 - comb(left - wanted, draws) / comb(left, draws) if left - wanted >= draws else 1.0


def _plan_crits(battle, action) -> dict:
    """{enemy index: [crit %, block %, damage, damage on a crit]} for the move
    (or, on a setup round, the next big hit): the plan card's bars."""
    from dataclasses import replace as _replace

    from .brain import predicted_crits

    try:
        if action.card is not None and "prism" in action.card.name.lower() and action.target:
            battle = _replace(battle, prismed=set(battle.prismed) | {action.target.name})  # (cast now)
        shown = action if action.card is not None and action.card.is_damage else (_next_hit(battle) or action)
        return {str(i): list(v) for i, v in predicted_crits(battle, shown).items()}
    except Exception:
        return {}


def _save_item_cards(seen: dict) -> None:
    """The item cards a fight showed (state/item_cards.pkl): the simulator's
    decks get them as they are, each its own spell (the player: the pet's,
    the amulet's and the trained Feint all stack)."""
    if not seen:
        return
    try:
        import pickle

        from .sim import ITEM_CARDS

        cards = [dataclasses.replace(c, index=-1, castable=True, enchanted=False) for c in seen.values()]
        ITEM_CARDS.write_bytes(pickle.dumps(cards))
    except Exception as exc:
        logger.debug(f"item cards not saved: {exc!r}")


def _gamble_odds(action) -> int | None:
    """The odds when the move is a crit gamble ("ends the fight on a crit: 57%")."""
    import re

    m = re.search(r"ends the fight on a crit: (\d+)%", action.reason or "")
    return int(m.group(1)) if m else None


def _next_hit(battle):
    """The biggest hit in hand (castable or not) as an attack on the boss or
    the toughest enemy, for the stream's bars on a setup round. None without one."""
    from .brain import hit_damage
    from .model import Action

    hits = [c for c in battle.cards if c.is_damage and not c.is_heal]
    live = battle.live_enemies
    if not hits or not live:
        return None
    focus = max(live, key=lambda e: (e.is_boss, e.health))
    card = max(hits, key=lambda c: hit_damage(c, battle.me, focus) * (len(live) if c.is_aoe else 1))
    return Action(ActionKind.CAST, card, None if card.is_aoe else focus, reason="the next big hit")


def improve_odds(battle, action) -> dict:
    """Cards still in the deck that would shorten the win if drawn (the
    stream's deck tracker): {"base": rounds now, "draws": next round's draws,
    "cards": {name: {"rounds": with it, "odds": % to draw}}, "any": %}."""
    from .lookahead import draw_values

    # The whole-deck planner's numbers (sampled draws and power pips): the
    # expected rounds now, and with each card that would cut them if drawn.
    try:
        exp, better_f = draw_values(battle, _POWER_CHANCE, budget=BEST_DRAW_SECONDS)
    except Exception:
        return {}
    base = round(exp)
    # Whole rounds saved, from the unrounded expectations (the overlay showed
    # "-0.0999999 rounds": a rounded base minus a card's 0.1-rounded figure);
    # only cards that save a full round.
    better = {n: base - saved for n, r in better_f.items() if (saved := round(exp - r)) >= 1}
    if not better:
        out = {"base": base}
        if action.kind is ActionKind.DISCARD and action.card is not None:
            out["toss"] = action.card.name
        return out
    tossed = 1 if action.kind in (ActionKind.CAST, ActionKind.DISCARD) else 0
    draws = max(1, HAND_MAX - len(battle.cards) + tossed)
    left = len(battle.upcoming)
    counts = {n: sum(1 for c in battle.upcoming if c.name == n) for n in better}
    cards = {n: {"rounds": r, "odds": round(100 * draw_chance(left, counts[n], draws))}
             for n, r in better.items()}
    out = {"base": base, "draws": draws, "cards": cards,
           "any": round(100 * draw_chance(left, sum(counts.values()), draws))}
    if action.kind is ActionKind.DISCARD and action.card is not None:
        out["toss"] = action.card.name  # (the panel lists the round's discards)
    return out


def _note_unknown_cards(battle) -> None:
    """A card in hand that the stored deck lacks (a spell the game put in
    when it was learned: Orthrus): the deck keeper re-reads the spellbook."""
    from ..deck import load_deck_counts
    from ..deck_keeper import STALE_FILE
    from .brain import is_reshuffle

    try:
        deck = load_deck_counts()
        if not deck or STALE_FILE.exists():
            return
        # (By the deck list's name: in a fight it's "Stone Colossus", in the
        # deck "ColossusStone_Trainable", and every fight asked for a re-read.)
        odd = [c.name for c in battle.cards
               if not c.treasure and not c.item and not is_reshuffle(c) and " - " not in c.name
               and _deck_name(c) not in deck and c.name not in deck]
        if odd:
            STALE_FILE.parent.mkdir(exist_ok=True)
            STALE_FILE.write_text(", ".join(odd), encoding="utf-8")
            logger.info(f"deck: {', '.join(odd)} in hand but not in the stored deck; "
                        "re-reading it after the fight")
    except Exception:
        pass


def _save_my_stats(me) -> None:
    """Our wizard as the game reads it (max health, gear's damage bonus and
    resists), for the simulator's fights from the start (sim.simulate)."""
    try:
        data = {"max_health": me.max_health, "damage_bonus": dict(me.damage_bonus), "level": me.level,
                "crit": dict(me.crit or {}), "block": dict(me.block or {}),
                "resist": dict(me.resist or {}), "school": me.school}
        MY_STATS.write_text(json.dumps(data), encoding="utf-8")
    except Exception:
        pass


class Fighter(CombatHandler):
    def __init__(self, client, strategy: Strategy, *, max_discards: int = 99, flee_below: float = 0.0,
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
        self.boss_fights = 0  # fights that had a boss in them
        self.combat_ended_at = 0.0  # monotonic time the last fight ended
        self.fled = False  # the last fight ended by fleeing (not a defeat)
        self.last_boss_names: list[str] = []  # bosses in the current/last fight (farm runs end on one)
        self.last_enemy_names: list[str] = []  # enemies of the current/last fight
        self.last_bosses: set[str] = set()  # which of them the game marks as bosses
        self.may_flee = None  # async () -> bool: whether fleeing is allowed here
        self._had_boss = False
        self._items_seen: dict = {}  # template name -> an item card seen in this fight
        self._no_discard: set[str] = set()  # cards whose discard didn't take this fight
        self._discard_pending = None
        self._unusable: set[str] = set()  # cards whose cast didn't register this round
        self._fails: dict[str, int] = {}  # failed casts per card this fight
        self._prismed: set[str] = set()  # enemies prismed this fight
        self._summons = 0  # minions summoned this fight
        self._pp_chance: float | None = None  # power pip chance, read once a fight
        self._hp_seen: list[tuple[int, int]] = []
        self._gone: Counter[str] = Counter()
        self._enchant_tried: dict[int, set[str]] = {}  # round -> cards an enchant was tried on
        self._discarded: Counter[str] = Counter()  # (of _gone: the discarded ones; the rest were played)
        self._plan_toss_round = -1  # the round a plan discard was made in (one a round)
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

    async def _cast_at(self, live_card, target, fx: float, target_window=None):
        """Like CombatCard.cast, but clicks the card at `fx` of its width (and
        the target at `target_window`, else its usual spot)."""
        try:
            r = await live_card._spell_window.scale_to_client()
            x = int(r.x1 + (r.x2 - r.x1) * fx)
            y = int((r.y1 + r.y2) / 2)
            await self.client.mouse_handler.click(x, y)
            if target is not None:
                await asyncio.sleep(1.0)
                spot = target_window or await self._target_window(target)
                await self.client.mouse_handler.click_window(spot)
        except (ValueError, AttributeError) as exc:
            # The round ended (or the target died) while we were clicking.
            logger.debug(f"cast click failed: {exc!r}")
            return False
        return True

    async def _target_windows(self, target) -> list:
        """Every spot that picks `target`: health text, name, whole nameplate."""
        out = []
        for get in ("get_health_text_window", "get_name_text_window"):
            try:
                out.append(await getattr(target, get)())
            except (ValueError, AttributeError):
                pass
        control = getattr(target, "_combatant_control", None)
        if control is not None:
            out.append(control)
        return out

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
            from ..dungeons import note_zone_boss

            for e in battle.enemies:
                if e.is_boss:
                    note_zone_boss(zone, e.name)  # (every zone: a locked door's guard, Gurtok)
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
        from ..deck import _spellbook_open, close_spellbook

        if await _spellbook_open(self.client):
            # The quest book left open over the battle (a quest-book read when
            # the fight began): every card click and Pass went to the book, and
            # the wizard sat out the rounds (Night Weavers, Monquistans).
            logger.warning("the spellbook is open over the battle: closing it first")
            await close_spellbook(self.client)

        # No minions at all (the player: slow, and their worth is hard to
        # judge): never summoned, and a minion card drawn is discarded.
        self.strategy.no_minions = True
        # A card that failed last round may sit in a working slot now; one that
        # failed twice this fight stays out (Basilisk "blade up" on ourselves
        # failed eight rounds running against Young Morganthe, Orthrus unused).
        self._unusable = {n for n, k in getattr(self, "_fails", {}).items() if k >= CAST_FAILS_MAX}
        from ..teamup import close_waiting_window, load_queue

        if load_queue():
            # Queued for a team: its Waiting-for-players window can sit over
            # the cards (every cast missed against two Fomori Giants).
            try:
                await close_waiting_window(self.client)
            except Exception as exc:
                logger.debug(f"waiting window: {exc!r}")
        self._flee_tried_this_round = False
        round_started = time.monotonic()
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
                # (Even in a dungeon with no mark: passing until we die gains
                # nothing, the player's rule; fleeing keeps us in the room.)
                fled = await self._flee()
                self.fled = self.fled or fled
                if fled:
                    return

            if not self._judged_fight:
                self._had_boss = self._had_boss or any(e.is_boss for e in battle.enemies)
            for c in battle.cards:  # (the item cards of this deck item, pet and amulet: for the simulator)
                if c.item and not c.treasure and c.template_name:
                    self._items_seen.setdefault(c.template_name, c)
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

            pending = getattr(self, "_discard_pending", None)
            if pending is not None:
                self._discard_pending = None
                rnd, name, had = pending
                if rnd == battle.round and sum(1 for c in battle.cards if c.name == name) >= had:
                    # The discard didn't take (an enchanted Humongofrog against
                    # Belloq: "discarded" 40 times over five rounds, nothing cast).
                    self._no_discard.add(name)
                    discards_left = 0
                    logger.warning(f"discarding {name} didn't work; no more discards this round, "
                                   f"and never {name} again this fight")

            _note_unknown_cards(battle)
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
            # An enchant tried on a card this round that didn't take (Giant on
            # an Orthrus short of pips: tried 8 times, the turn wasted): that
            # card counts as enchanted for the rest of the round.
            tried = self._enchant_tried.get(battle.round, set())
            if tried:
                battle.cards = [dataclasses.replace(c, enchanted=True) if c.name in tried else c
                                for c in battle.cards]
            # One plan discard a round at most (four in a round ran the deck dry).
            action = decide(battle, self.strategy, discards_left=discards_left,
                            plan_discards=self._plan_toss_round != battle.round, odds_discards=True)
            reshuffling = "reshuffle" in (action.reason or "").lower() or (
                action.card is not None and action.card.name.strip().lower() == "reshuffle")
            # Rollouts weigh the move that ends the turn, within a time budget:
            # run before each plan discard too, round 1 took 30 s and the timer
            # ran out (the discards are the brain's own free moves).
            plan_toss = action.kind is ActionKind.DISCARD and (action.reason or "").startswith("not in the")
            if plan_toss:
                self._plan_toss_round = battle.round
            late = time.monotonic() - round_started > ROLLOUT_BUDGET
            if self.planner is not None and not reshuffling and not plan_toss and not late:
                from .rollout import TIME_LIMIT

                left = ROLLOUT_BUDGET + ROLLOUT_GRACE - (time.monotonic() - round_started)
                self.planner.time_limit = max(2.0, min(TIME_LIMIT, left))
                # (The simulator knows nothing of Reshuffle: its plays and the
                # pips saved for it are the brain's.)
                action = await self.planner.choose(battle, action, self.strategy, discards_left)
            # With teammates the turn is short and shared (the player: it put
            # Gargantuan on, thought 12 s more and ran out of time): no
            # whole-deck search; the brain's move goes at once.
            team = bool(battle.allies)
            if not reshuffling and not team and time.monotonic() - round_started < LOOKAHEAD_LATE:
                action = await self._lookahead(battle, action, discards_left)
            action = prism_first(battle, action)  # never a big hit into a resist a prism in hand turns
            from .brain import luska_guard

            action = luska_guard(battle, action)
            if action.kind is ActionKind.PASS and not action.plan_cards:
                # Waiting never wastes the turn when a 0-pip trap or blade is in
                # hand: it costs nothing and the pips build all the same (the
                # rollouts passed against Gurtok holding Myth Trap; the brain's
                # wait for Reshuffle did too).
                from .brain import Strategy, _free_setup, _no_single_target

                # Under the boss rules too (a blade in Sylster's light round
                # went out this way, past the rule that had made it a pass).
                free = _free_setup(_no_single_target(battle), self.strategy or Strategy())
                if free is not None:
                    free.reason = f"{free.reason}, instead of a plain pass ({action.reason})"
                    action = free
            if action.kind is ActionKind.DISCARD and action.card is not None and (
                    discards_left <= 0 or action.card.name in self._no_discard or action.card.enchanted):
                # (No discards left, a card that wouldn't go, or an enchanted
                # one: the move is decided again without discarding.)
                action = decide(battle, self.strategy, discards_left=0,
                                plan_discards=False, odds_discards=False)
                if action.kind is ActionKind.DISCARD:
                    action = Action(ActionKind.PASS, reason=f"no discard ({action.reason})")
            if not team or time.monotonic() - getattr(self, "_plan_written", 0.0) > TEAM_PLAN_EVERY:
                # (The stream's plan card: a whole simulation; in a team fight
                # once in a while, not before every move.)
                self._plan_written = time.monotonic()
                _write_plan(battle, action, self.strategy, discards_left, self._gone, self._discarded)
            foes = ", ".join(
                f"{e.name}{'*' if e.is_boss else ''} {e.health}/{e.max_health}{' dead' if e.is_dead else ''}"
                for e in battle.enemies
            )
            logger.info(
                f"[round {battle.round}] pips={battle.pips}+{battle.power_pips}P "
                f"hp={battle.me.health}/{battle.me.max_health}{_minion_text(battle)} vs {foes} -> "
                f"{action.describe()}"
            )
            shown = action
            predicted = predicted_damage(battle, action)
            if not predicted:
                # A setup round: the bars show the biggest hit in hand, the one
                # the blades are for (the player: no crit numbers on those rounds).
                # A prism cast now counts already (it showed 167, unprismed).
                if action.card is not None and "prism" in action.card.name.lower() and action.target:
                    battle.prismed = set(battle.prismed) | {action.target.name}
                shown = _next_hit(battle) or action
                predicted = predicted_damage(battle, shown)
            if predicted:  # for the stream page's health bars
                tag = "" if shown is action else " (next hit)"
                logger.info("predict: " + ", ".join(f"{i}={d}" for i, d in predicted.items()) + tag)
                from .brain import predicted_crits

                crits = predicted_crits(battle, shown)
                if any(c[0] for c in crits.values()):  # (crit 0%: nothing to show)
                    text = ", ".join(f"{i}={c}/{b}/{n}/{x}" for i, (c, b, n, x) in crits.items())
                    logger.info(f"crit: {text}")

            if action.kind is ActionKind.PASS or action.card is None:
                await self.pass_button()
                return

            live_card = snap.cards[action.card.index]

            if action.kind is ActionKind.ENCHANT:
                self._enchant_tried = {battle.round: self._enchant_tried.get(battle.round, set())
                                       | {action.target_card.name}}
                self._gone[_deck_name(action.card)] += 1
                await live_card.cast(snap.cards[action.target_card.index])
                await asyncio.sleep(0.3)
                continue

            if action.kind is ActionKind.DISCARD:
                self._discard_pending = (battle.round, action.card.name,
                                         sum(1 for c in battle.cards if c.name == action.card.name))
                self._gone[_deck_name(action.card)] += 1
                self._discarded[_deck_name(action.card)] += 1
                r = await live_card._spell_window.scale_to_client()
                x = int(r.x1 + (r.x2 - r.x1) * self._card_click_x)
                await self.client.mouse_handler.click(x, int((r.y1 + r.y2) / 2), right_click=True)
                await asyncio.sleep(1.0)
                discards_left -= 1
                continue

            target = snap.members.get(id(action.target)) if action.target else None
            if "prism" in action.card.name.lower() and action.target:
                self._prismed.add(action.target.name)  # waits on them for our next myth hit
            elif "prism" in action.card.name.lower() and not action.card.is_damage:
                self._prismed.update(e.name for e in battle.live_enemies)  # (a mass prism: on all of them)
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
            stray = await ui.close_stray_windows(self.client)
            if stray:
                logger.warning(f"closed a window over the battle: {stray}")
            # A window over the cards (the pet level-up) eats every click: close it.
            from ..upkeep import clear_popups

            try:
                await clear_popups(self.client)
            except Exception as exc:
                logger.debug(f"clearing popups failed: {exc!r}")
            # A target that didn't take (Sylster's fight: every single-target
            # cast failed, the hits on all and the enchants went through): its
            # other spots (name, nameplate), with where each one is.
            if target is not None:
                for w in (await self._target_windows(target))[1:]:
                    try:
                        logger.info(f"retrying {action.card.name} on {action.target.name} by another spot: "
                                    f"{await w.scale_to_client()}")
                    except Exception:
                        pass
                    if not await self._cast_at(live_card, target, self._card_click_x, w):
                        return
                    if await self._committed(before):
                        logger.warning("the target took at its other spot")
                        return
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
            logger.warning(f"giving up on {action.card.name} this round; passing (not waiting out the timer)")
            self._unusable.add(action.card.name)
            self._fails[action.card.name] = self._fails.get(action.card.name, 0) + 1
            # (The player: Pass rather than sit out the clock; a second card
            # tried after a failed one took the rest of Sylster's first round.)
            await self.pass_button()
            return

        logger.warning("too many steps this round, passing")
        await self.pass_button()

    async def _lookahead(self, battle, action, discards_left: int):
        """The whole-deck planner (lookahead.py) on top of the brain's move:
        the round's discards (digging for a better card) and first move, by
        their odds over sampled draws. Never over an enchant, a heal or a
        shield (the brain's urgent moves), nor in fights the boss rules play
        (Luska, Sylster...)."""
        from . import lookahead
        from .brain import _no_single_target, scripted_fight

        if not getattr(self.strategy, "lookahead", LOOKAHEAD_ON):
            return action
        card = action.card
        urgent = card is not None and (card.is_heal or EffectKind.SHIELD in card.kinds)
        if action.kind is ActionKind.ENCHANT or urgent:
            return action
        if scripted_fight(battle) or not battle.deck_known:
            return action
        from .brain import hit_size

        if (action.kind is ActionKind.CAST and card is not None and card.is_damage
                and battle.me.health <= LOW_HP_HIT_MARGIN * hit_size(battle)):
            # The next hit may well end us: the brain's hit stands (the planner
            # added Feints at 975 health against the Bog Witch, "100% win",
            # with Orthrus ready, and lost).
            return action
        rules = _no_single_target(battle)  # (boss bans and the like: what may be cast)
        # The damage this fight has done to us per round, last 3 rounds.
        hist = self._hp_seen
        if not hist or hist[-1][0] != battle.round:
            hist.append((battle.round, battle.me.health))
        recent = hist[-4:]
        lost = [a[1] - b[1] for a, b in zip(recent, recent[1:], strict=False) if a[1] > b[1]]
        lookahead.OBSERVED_HIT = sum(lost) / len(lost) if lost else 0.0
        if action.kind is ActionKind.CAST and card is not None and card.is_damage and lost:
            from .brain import hit_damage

            struck = battle.live_enemies if card.is_aoe else [action.target] if action.target else []
            kills = sum(1 for e in struck if e is not None and hit_damage(card, battle.me, e) >= e.health)
            if kills and battle.me.health <= KILL_KEEP_ROUNDS * max(lost):
                # Taking a lot each round, and the brain's hit takes attackers
                # out: it stands (Blue Agnes and three Night Weavers: the planner
                # put Feints on her at 1739 health instead of the Orthrus that
                # killed the three, "69% win", and lost).
                return action
        try:
            chance = await self._power_chance()
            choice = await asyncio.to_thread(lookahead.choose, rules, action, chance, discards_left)
        except Exception as exc:
            logger.debug(f"lookahead failed: {exc!r}")
            return action
        if choice is None:
            return action
        new = choice.action
        # The planner's card is the hand's own (the rules' copy has the same index).
        if new.card is not None:
            new.card = next((c for c in battle.cards if c.index == new.card.index), new.card)
        if new.target is not None and new.target not in battle.enemies and new.target is not battle.me:
            new.target = next((e for e in battle.enemies if e.name == new.target.name), None)
        logger.info(f"whole-deck plan: {new.describe()} instead of {action.describe()}")
        return new

    async def _power_chance(self) -> float:
        """The wizard's power pip chance (0-1), read once a fight."""
        if self._pp_chance is None:
            chance = POWER_CHANCE_DEFAULT
            try:
                base = await self.client.stats.power_pip_base()
                bonus = await self.client.stats.power_pip_bonus_percent_all()
                raw = (base or 0) + (bonus or 0)
                chance = raw / 100 if raw > 1.5 else raw
                logger.info(f"power pip chance: {chance:.0%} (base {base}, bonus {bonus})")
            except Exception as exc:
                logger.debug(f"power pip chance unreadable: {exc!r}")
            self._pp_chance = max(0.0, min(1.0, chance))
            global _POWER_CHANCE
            _POWER_CHANCE = self._pp_chance
        return self._pp_chance

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
        self._fails: dict[str, int] = {}  # failed casts per card this fight
        self._prismed: set[str] = set()
        self._pp_chance = None  # read once a fight
        self._hp_seen: list[tuple[int, int]] = []  # (round, our health) for the planner's damage estimate
        self._summons = 0
        self._gone: Counter[str] = Counter()  # deck cards cast or discarded this fight
        self._discarded = Counter()
        self._deck = load_deck_counts()
        self._judged_fight = False
        self._fleeing = False
        self._want_flee = False
        self._last_plan = ""
        self._had_boss = False
        self._no_discard = set()
        self._discard_pending = None
        self.fled = False
        with contextlib.suppress(Exception):  # where we fought (a boss's walk-in starts there)
            from ..dungeons import note_last_fight

            p = await self.client.body.position()
            note_last_fight(await self.client.zone_name() or "", (p.x, p.y, p.z))
        await super().handle_combat()
        self.fights += 1
        self.combat_ended_at = time.monotonic()
        self.last_had_boss = self._had_boss  # (the deck keeper: no boss, the everyday deck again)
        _save_item_cards(self._items_seen)
        self._items_seen = {}
        if self._had_boss:
            self.boss_fights += 1
        logger.success(f"combat over (fights so far: {self.fights})")
        _write_plan(None, None, None, 0)
