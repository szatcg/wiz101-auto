"""The whole-deck planner: choosing this round's move with the deck in mind.

The player (2026-10-06): the bot thought only with its hand. Against Porrich
it set up Humongofrog for rounds when digging for the Orthrus still in the
deck would have killed much sooner; it passed with a full hand and drew
nothing.

Here every option for this round is played forward against sampled futures:
`SAMPLES` random orders of the cards left in the deck, each with its own
pips (a power pip each round with the wizard's power pip chance). For each
future the existing exact search (brain._kill_search) finds the quickest
kill with those draws, the hand refilling to HAND_SIZE each round (so a
discard is one more card next round, and a pass with a full hand draws
none). An option is scored by its chance to win before we fall (the
enemies' damage per round from the fight logs) and then its expected
rounds. Options: each discard set worth trying (0-2 cards the plans don't
use), then each first move (a card on an enemy, or pass).

The same futures give the overlay's "draws that cut rounds": for each card
still in the deck, the expected rounds if it came next.
"""

from __future__ import annotations

import random
import time
from dataclasses import dataclass, replace

from .model import Action, ActionKind, Battle, Card, Combatant

HAND_SIZE = 7
SAMPLES = 16  # draw orders per option (the same ones for every option)
HORIZON = 7  # rounds looked ahead per enemy (past it: the damage done so far, extrapolated)
CAP = 20  # an expected-rounds ceiling
BUDGET = 5.0  # seconds for the round's plan (the turn timer is ~30 s; later steps reuse it)
DISCARD_RESERVE = 6  # fewer cards than this left to draw: no discards for a draw
OVERRIDE_ROUNDS = 0.5  # the brain's move is replaced when this many rounds faster ...
OVERRIDE_WIN = 0.08  # ... or this much likelier to win
DISCARD_ROUNDS = 0.25  # a discard (free: only the deck's cards) when this many rounds faster ...
DISCARD_WIN = 0.04  # ... or this much likelier to win


class OutOfTime(Exception):
    pass


@dataclass
class Score:
    win: float  # share of futures where the enemies die before we do
    rounds: float  # expected rounds to end the fight (CAP when no kill)

    def key(self):
        return (-round(self.win, 3), round(self.rounds, 2))

    def better_than(self, other: Score, rounds: float = OVERRIDE_ROUNDS, win: float = OVERRIDE_WIN) -> bool:
        return (self.win > other.win + win
                or (self.win >= other.win - 1e-9 and self.rounds < other.rounds - rounds))

    def __str__(self):
        return f"{self.win:.0%} win, ~{self.rounds:.1f} rounds"


def _futures(battle: Battle, n: int, power_chance: float, seed: int) -> list[tuple[list[Card], list[bool]]]:
    rng = random.Random(seed)
    out = []
    for _ in range(n):
        order = list(battle.upcoming)
        rng.shuffle(order)
        gains = [rng.random() < power_chance for _ in range(HORIZON * 3)]
        out.append((order, gains))
    return out


def _survive_rounds(battle: Battle) -> int:
    """Rounds we last at the enemies' logged damage per round."""
    from .brain import incoming_per_round

    hit = incoming_per_round(battle)
    if hit <= 0:
        return CAP
    return max(1, int(battle.me.health // hit) + 1)


def _fight_rounds(battle: Battle, draws: list[Card], gains: list[bool], deadline: float,
                  force=None, first: Combatant | None = None) -> tuple[float, bool]:
    """(rounds to kill every enemy one after the other, the forced move's
    target first, with this draw order; whether that's an exact plan). Past
    the horizon the rounds are estimated from the damage the best line did
    by then (a boss with 8000 health and the Orthrus deep in the deck)."""
    from .brain import SearchTimeout, _kill_search

    enemies = sorted(battle.live_enemies, key=lambda e: e.health)
    if first is not None:
        enemies = [first] + [e for e in enemies if e is not first]
    total, state, draws, gains = 0, battle, list(draws), list(gains)
    for k, e in enumerate(enemies):
        partial = [float(e.health)]
        try:
            found = _kill_search(state, e, HORIZON, draws, deadline, gains=gains,
                                 hand_size=HAND_SIZE, force=force if k == 0 else None, partial=partial)
        except SearchTimeout:
            raise OutOfTime from None
        if found is None:
            done = e.health - partial[0]
            if done <= 0:
                return float(CAP), False
            rest = sum(o.health for o in enemies[k + 1:])
            return min(float(CAP), total + HORIZON * (e.health + rest) / done), False
        n, spent, _action, _steps, used = found
        total += n
        # The next enemy starts with what's left: the cards not played, the
        # draws that came, the pips not spent (power ones counted as two).
        came = min(len(draws), n + max(0, HAND_SIZE - len(state.cards)))
        cards = [c for c in state.cards if c.index not in used and not c.is_enchant] + draws[:came]
        left = max(0, state.pips + 2 * state.power_pips + n - spent)
        state = replace(state, cards=cards, pips=left, power_pips=0, upcoming=draws[came:])
        draws, gains = draws[came:], gains[n:]
    return float(total), True


def _score(battle: Battle, futures, deadline: float, force=None, first=None) -> Score:
    alive = _survive_rounds(battle)
    wins, rounds = 0, 0.0
    for draws, gains in futures:
        if time.monotonic() > deadline:
            raise OutOfTime
        r, _exact = _fight_rounds(battle, draws, gains, deadline, force, first)
        rounds += r
        wins += r <= alive
    k = max(1, len(futures))
    return Score(wins / k, rounds / k)


def _droppable(battle: Battle) -> list[Card]:
    """Hand cards a discard may take (never Reshuffle, treasure, item or
    enchant cards: the player's own, or free to play anyway)."""
    from .brain import is_reshuffle

    return [c for c in battle.cards
            if not c.treasure and not c.item and not is_reshuffle(c) and not c.is_enchant]


def _without(battle: Battle, cards) -> Battle:
    ids = {id(c) for c in cards}
    return replace(battle, cards=[c for c in battle.cards if id(c) not in ids])


def _moves(battle: Battle) -> list[tuple[object, Combatant | None, Card | None]]:
    """First moves: each castable hand card (by name) on each live enemy it
    can go on, and pass. (force value, enemy to kill first, card)."""
    from .model import Target

    out: list[tuple[object, Combatant | None, Card | None]] = [("pass", None, None)]
    seen = set()
    enemies = sorted(battle.live_enemies, key=lambda e: e.health)
    for c in battle.cards:
        if not c.castable or c.is_enchant or c.name in seen:
            continue
        seen.add(c.name)
        on_enemy = c.target in (Target.ENEMY_SINGLE,)
        for e in (enemies if on_enemy else enemies[:1]):
            out.append((c.index, e, c))
    return out


@dataclass
class Choice:
    action: Action
    score: Score
    brain: Score | None


_CACHE: dict = {}  # (round, hand, enemies' health) -> the round's plan: [discards, (score, move)]


def _hand_key(battle: Battle):
    return (battle.round, tuple(sorted(c.name for c in battle.cards)),
            tuple(e.health for e in battle.live_enemies), battle.pips, battle.power_pips)


def plan_round(battle: Battle, power_chance: float, discards_left: int, budget: float,
               seed: int = 0) -> tuple[list[Card], Score, Score] | None:
    """The round's discards (one at a time, each kept only when it makes the
    fight clearly better: a card in the way of a better draw) and the scores
    (with them, and keeping the hand). None when out of time."""
    deadline = time.monotonic() + budget
    futures = _futures(battle, SAMPLES, power_chance, seed)
    try:
        base = _score(battle, futures, deadline)
        drops: list[Card] = []
        current, current_score = battle, base
        while len(drops) < discards_left and len(current.upcoming) >= DISCARD_RESERVE:
            best = None
            for c in _droppable(current):
                s = _score(_without(current, [c]), futures, deadline)
                if (s.better_than(current_score, DISCARD_ROUNDS, DISCARD_WIN)
                        and (best is None or s.key() < best[0].key())):
                    best = (s, c)
            if best is None:
                break
            current_score, card = best
            drops.append(card)
            current = _without(current, [card])
    except OutOfTime:
        return None
    return drops, current_score, base


def choose(battle: Battle, brain_action: Action | None, power_chance: float, discards_left: int = 2,
           budget: float = BUDGET, seed: int = 0) -> Choice | None:
    """The best move this round with the whole deck counted, or None when
    the deck isn't known, out of time, or nothing beats the brain's move.
    The round's plan is worked out once (the first step of the round) and
    reused while the hand is as planned."""
    if not battle.deck_known or not battle.live_enemies or not battle.upcoming:
        return None
    key = _hand_key(battle)
    plan = _CACHE.get(key)
    if plan is None:
        got = plan_round(battle, power_chance, discards_left, budget, seed)
        if got is None:
            return None
        drops, with_drops, base = got
        plan = _CACHE[key] = {"drops": drops, "score": with_drops, "base": base}
        for k in range(1, len(drops) + 1):
            # (After each planned discard the hand is another: the same plan.)
            _CACHE[_hand_key(_without(battle, drops[:k]))] = plan
        while len(_CACHE) > 64:
            _CACHE.pop(next(iter(_CACHE)))
    drops = [c for c in plan["drops"] if any(c is h for h in battle.cards)]
    if drops:
        names = ", ".join(c.name for c in drops)
        why = f"digging (whole deck): without {names} {plan['score']} (keeping all: {plan['base']})"
        return Choice(Action(ActionKind.DISCARD, drops[0], reason=why), plan["score"], plan["base"])
    if "move" not in plan:
        deadline = time.monotonic() + budget
        futures = _futures(battle, SAMPLES, power_chance, seed + 1)
        try:
            brain_score = None
            if brain_action is not None and brain_action.kind is ActionKind.PASS:
                brain_score = _score(battle, futures, deadline, "pass")
            elif (brain_action is not None and brain_action.kind is ActionKind.CAST
                  and brain_action.card is not None):
                tgt = brain_action.target if brain_action.target in battle.live_enemies else None
                brain_score = _score(battle, futures, deadline, brain_action.card.index, tgt)
            top = None
            for force, tgt, card in _moves(battle):
                s = _score(battle, futures, deadline, force, tgt)
                if top is None or s.key() < top[0].key():
                    top = (s, force, tgt, card)
        except OutOfTime:
            return None
        plan["move"] = (top, brain_score)
    top, brain_score = plan["move"]
    if top is None:
        return None
    s, force, tgt, card = top
    if brain_score is not None and not s.better_than(brain_score):
        return None
    if force == "pass":
        action = Action(ActionKind.PASS, reason=f"lookahead: pass ({s})")
    else:
        from .model import Target

        target = None if card.is_aoe else tgt
        if card.target is Target.ALLY_SINGLE:
            target = battle.me
        action = Action(ActionKind.CAST, card, target, reason=f"lookahead: {card.name} ({s})")
    if brain_score is not None:
        action.reason += f"; the brain's pick: {brain_score}"
    return Choice(action, s, brain_score)


def draw_values(battle: Battle, power_chance: float, budget: float = 2.0,
                seed: int = 0) -> tuple[float, dict[str, float]]:
    """(expected rounds now, {card still in the deck: expected rounds if it
    came next}) for the cards that cut the expected rounds (the overlay)."""
    if not battle.upcoming or not battle.live_enemies:
        return CAP, {}
    deadline = time.monotonic() + budget
    futures = _futures(battle, SAMPLES // 2, power_chance, seed)
    try:
        base = _score(battle, futures, deadline).rounds
        out: dict[str, float] = {}
        for name in sorted({c.name for c in battle.upcoming}):
            card = next(c for c in battle.upcoming if c.name == name)
            front = [([card] + [c for c in d if c is not card], g) for d, g in futures]
            r = _score(battle, front, deadline).rounds
            if r < base - 0.4:
                out[name] = r
    except OutOfTime:
        return CAP, {}
    return base, out
