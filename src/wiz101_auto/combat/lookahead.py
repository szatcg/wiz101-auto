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

A future also rolls each card's miss (the logs' hit rates) and crit (the
game's odds), and a hit takes the best damage enchant in hand, as the brain
plays it. (Known rolls are a little clairvoyant: a line can wait for the
cast that crits. Rolling only this round's cast, ROLL_DEPTH 1, lost the
planner's whole edge in the simulator, so every round rolls.)

The same futures give the overlay's "draws that cut rounds": for each card
still in the deck, the expected rounds if it came next.
"""

from __future__ import annotations

import random
import time
from dataclasses import dataclass, field, replace

from .model import Action, ActionKind, Battle, Card, Combatant, EffectKind

HAND_SIZE = 7
SAMPLES = 16  # draw orders per option (the same ones for every option)
HORIZON = 7  # rounds looked ahead per enemy (past it: the damage done so far, extrapolated)
CAP = 20  # an expected-rounds ceiling
BUDGET = 5.0  # seconds for the round's plan (the turn timer is ~30 s; later steps reuse it)
DISCARD_RESERVE = 6  # fewer cards than this left to draw: no discards for a draw
OVERRIDE_ROUNDS = 0.5  # the brain's move is replaced when this many rounds faster ...
OVERRIDE_WIN = 0.08  # ... or this much likelier to win
# A pass instead of the brain's cast: this many rounds faster (the estimates
# past the horizon favour saving pips).
PASS_ROUNDS = 1.0
SPARE_COPIES = False  # a 2nd copy of a big hit may be discarded (off: a miss then left the deck short)
LOSING = 0.2  # every option below this chance to win: the brain's move stands
SEEN_ENOUGH = 0.5  # overrides and discards only when this share of futures sees the kill
DISCARD_ROUNDS = 0.25  # a discard (free: only the deck's cards) when this many rounds faster ...
DISCARD_WIN = 0.04  # ... or this much likelier to win


ENCHANTS = True  # a hit takes the best damage enchant in hand, as the brain plays it


def _enchant_value(card: Card) -> float:
    return sum(e.value for e in card.effects if e.kind is EffectKind.ENCHANT_DAMAGE)


def _with_enchant(card: Card, enchant: Card) -> Card:
    """`card` with `enchant`'s damage on each of its hits (Gargantuan)."""
    extra = _enchant_value(enchant)
    effects = [replace(e, value=e.value + extra) if e.kind in (EffectKind.DAMAGE, EffectKind.DOT) else e
               for e in card.effects]
    return replace(card, effects=effects, enchanted=True)


class OutOfTime(Exception):
    pass


class Unplayable(Exception):
    """The forced first move isn't one the search can play out."""


@dataclass
class Score:
    win: float  # share of futures where the enemies die before we do
    rounds: float  # expected rounds to end the fight (CAP when no kill)
    seen: float = 1.0  # share of futures where the kill is within the horizon (not an estimate)

    def key(self):
        return (-round(self.win, 3), round(self.rounds, 2))

    def better_than(self, other: Score, rounds: float = OVERRIDE_ROUNDS, win: float = OVERRIDE_WIN) -> bool:
        # Trusted only where the kill is seen (the estimates past the horizon
        # played big bosses slower than the brain in the simulator).
        if min(self.seen, other.seen) < SEEN_ENOUGH:
            return False
        if max(self.win, other.win) < LOSING:
            # Every option looks lost: its rounds aren't worth trusting, and the
            # brain's habits (kill the adds: less damage to take) are the
            # better bet (it passed instead of an Orthrus that killed a Storm
            # Elemental, at 0% win either way).
            return False
        return (self.win > other.win + win
                or (self.win >= other.win - 1e-9 and self.rounds < other.rounds - rounds))

    def __str__(self):
        return f"{self.win:.0%} win, ~{self.rounds:.1f} rounds"


CRITS = True  # each future rolls our crits (and the enemies' blocks) by the game's odds
# Rounds whose casts take the future's miss and crit rolls; later ones land
# and crit only when sure. (Rolls known in advance let a line wait for the
# cast that crits: a pass outscored the brain's 75% Humongofrog crit to end
# the fight.)
ROLL_DEPTH = 99
MISSES = True  # each future also says which cards fizzle (their hit rates)
_RATES: list = [0.0, {}]  # (file time, {spell: hit rate}) of state/enemy_stats.json


def hit_rate(card: Card) -> float:
    """The chance `card` lands: its hit rate in the fight logs
    (enemy_stats.json, by name or the name before " - ": "Orthrus - T02 - A"),
    else the card's accuracy."""
    from pathlib import Path

    path = Path("state") / "enemy_stats.json"
    try:
        mtime = path.stat().st_mtime
        if mtime != _RATES[0]:
            import json

            _RATES[:] = [mtime, json.loads(path.read_text(encoding="utf-8")).get("hit_rate", {})]
    except (OSError, ValueError):
        pass
    rates = _RATES[1]
    rate = rates.get(card.name, rates.get(card.name.split(" - ")[0]))
    if rate is None:
        rate = card.accuracy / 100.0
    return max(0.0, min(1.0, float(rate)))


@dataclass
class Future:
    draws: list[Card]
    gains: list[bool]
    misses: frozenset = frozenset()  # id() of the cards that fizzle in this future
    rolls: dict = field(default_factory=dict)  # id() -> (crit roll, block roll) of each card in this future


def _futures(battle: Battle, n: int, power_chance: float, seed: int) -> list[Future]:
    rng = random.Random(seed)
    out = []
    cards = [*battle.cards, *battle.upcoming]
    for _ in range(n):
        order = list(battle.upcoming)
        rng.shuffle(order)
        gains = [rng.random() < power_chance for _ in range(HORIZON * 3)]
        misses = frozenset(id(c) for c in cards if rng.random() >= hit_rate(c)) if MISSES else frozenset()
        rolls = {id(c): (rng.random(), rng.random()) for c in cards} if CRITS and battle.me.crit else {}
        out.append(Future(order, gains, misses, rolls))
    return out


OBSERVED_HIT = 0.0  # damage we took per round in this fight so far (the fighter sets it each round)


def _survive_rounds(battle: Battle) -> int:
    """Rounds we last at the enemies' damage per round: their logged
    rounds, or what this fight has shown, whichever is more (Maudit Soulban
    and his Giant, never logged, hit for ~900 a round while the planner
    counted on far less and called a near-loss a sure win)."""
    from .brain import incoming_per_round

    hit = max(incoming_per_round(battle), OBSERVED_HIT)
    if hit <= 0:
        return CAP
    return max(1, int(battle.me.health // hit) + 1)


# Our health along a line: each enemy still up hits us for its damage per
# round, so a line that kills an add early loses less (a fixed rate for the
# whole fight made killing an add look worthless). "line": the quickest
# kill's line is won when we live through it (past the horizon: at the rate
# the enemies left up then hit); "prune": lines we don't live through are
# dropped from the search; "": rounds to kill vs our health at today's rate.
# Off: in the simulator "prune" was slower (p95 7.5 s on Porrich) and no
# better; "line" was even without crits and worse with them (2 mobs +0.8
# rounds, 4 mobs -7 wins: a lost-looking future after a cast that didn't
# crit makes waiting look safer, the rolls being known in advance).
SURVIVAL = ""


def _hits_per_round(battle: Battle, enemies: list[Combatant]) -> list[float]:
    """Each enemy's damage to us per round (the logs', as incoming_per_round
    counts it), scaled up to what this fight has shown when that's more."""
    from .brain import _STATS, UNKNOWN_HIT_SHARE, incoming_per_round

    total = incoming_per_round(battle)  # (also reads the logs' stats)
    out = []
    for e in enemies:
        st = _STATS[1].get(e.name, {})
        rounds = [*st.get("alone", []), *st.get("shared", [])]
        out.append(sum(rounds) / len(rounds) if rounds else UNKNOWN_HIT_SHARE * battle.me.max_health)
    if OBSERVED_HIT > total > 0:
        out = [d * OBSERVED_HIT / total for d in out]
    return out


def _group_search(battle: Battle, draws: list[Card], gains: list[bool], deadline: float,
                  force=None, force_target: str | None = None,
                  misses: frozenset = frozenset(),
                  rolls: dict | None = None) -> tuple[float, bool, bool | None]:
    """(rounds to kill every enemy, exact or an estimate past the horizon,
    won: None when it's for the caller to judge from the rounds)
    with this draw order: every enemy's health and traps followed at once,
    so a hit-all spell (Orthrus) lands on each through its own traps, and a
    single hit or trap goes on the enemy chosen. The hand refills to
    HAND_SIZE each round, a pip comes each round (a power pip per `gains`);
    blades are spent by the next hit, traps by the next hit on that enemy.
    Past HORIZON: the health left, as the setups up by then see it, at the
    damage rate so far. `force`/`force_target`: the first move (a
    Card.index or "pass", and the enemy's name). `misses`: id() of the
    cards that fizzle (pips and the card spent, nothing done). `rolls`: each
    card's crit and block rolls (else a crit counts only when near sure)."""
    from .brain import (
        PRISM_GAIN,
        PRISM_SCHOOL,
        _is_duplicate,
        _is_prism,
        _pay,
        _prism_gain,
        _use_up,
        block_chance,
        crit_chance,
        hit_damage,
        prism_view,
    )
    from .model import Target

    me = battle.me
    mine = (me.school or "").lower()
    enemies = [e for e in battle.live_enemies]
    names = [e.name for e in enemies]
    pool = [c for c in battle.cards if ENCHANTS or not c.is_enchant] + list(draws)
    in_hand = len(pool) - len(draws)
    held_other = len(battle.cards) - in_hand
    # Damage enchants (Gargantuan): the brain puts one on the hit it casts,
    # so a hit takes the best one in hand (and frees its slot).
    enchants = [j for j, c in enumerate(pool)
                if ENCHANTS and c.is_enchant and EffectKind.ENCHANT_DAMAGE in c.kinds and not c.banned]
    enchants.sort(key=lambda j: -_enchant_value(pool[j]))
    boosted: dict = {}
    start_hp = tuple(float(e.health) for e in enemies)
    # A prism on an enemy turns our next myth hit on it into storm (Porrich:
    # 80% myth resist, weak to storm), then it's used up.
    a_prism = next((c for c in pool if _is_prism(c)), None)
    prism_worth = [a_prism is not None and _prism_gain(a_prism, me, e, battle.cards) >= PRISM_GAIN
                   for e in enemies]
    start_prisms = tuple(e.name in battle.prismed or e.myth_prism for e in enemies)
    total_hp = sum(start_hp)
    best = [HORIZON + 1]  # rounds of the quickest kill found
    took = [force is None]  # the forced first move was playable (else it can't be scored)
    partial = [total_hp]  # the least effective health left at the horizon
    seen_at: dict = {}
    hits = _hits_per_round(battle, enemies) if SURVIVAL else None
    prune = SURVIVAL == "prune"
    life = float(me.health)
    at_horizon = [False]  # a line we live through reached the horizon
    best_taken = [0.0]  # damage to us along the quickest kill's line
    partial_taken = [0.0, 0.0]  # (damage to us by the horizon, the enemies' rate then) on the partial's line

    def gain(depth, n, p):
        return (n, p + 1) if depth < len(gains) and gains[depth] else (n + 1, p)

    def refill(drawn, used_n):
        held = in_hand + drawn - used_n + held_other
        return min(len(draws), drawn + max(0, HAND_SIZE - held))

    def search(depth, normal, power, used, out_fx, in_fx, hps, drawn, prisms, taken):
        if time.monotonic() > deadline:
            raise OutOfTime
        if all(h <= 0 for h in hps):
            if depth < best[0] or (depth == best[0] and taken < best_taken[0]):
                best[0], best_taken[0] = depth, taken
            return
        if hits is not None and depth > 0:
            # The enemies still up hit back after our move.
            taken += sum(d for d, h in zip(hits, hps, strict=True) if h > 0)
            if prune and taken >= life:
                return  # (a line we don't live through is no line)
        if depth >= HORIZON:
            boost = 1 + sum(v for _k, _s, v in out_fx if v > 0)
            eff = sum(h / (boost * (1 + sum(v for _k, _s, v in fx if v > 0)))
                      for h, fx in zip(hps, in_fx, strict=True) if h > 0)
            if eff < partial[0] and hits is not None:
                partial_taken[:] = [taken, sum(d for d, h in zip(hits, hps, strict=True) if h > 0)]
            partial[0] = min(partial[0], eff)
            at_horizon[0] = True
            return
        if depth + 1 >= best[0]:
            return
        where = (used, normal, power, tuple(round(h) for h in hps), drawn, len(out_fx),
                 tuple(len(x) for x in in_fx), prisms)
        prev = seen_at.get(where)
        if prev is not None and prev[0] <= depth and prev[1] <= taken:
            return
        seen_at[where] = (depth, taken)
        live = [k for k, h in enumerate(hps) if h > 0]
        tried = set()
        named: dict = {}
        for i, c in enumerate(pool):
            if i in used:
                continue
            if i >= in_hand and i - in_hand >= drawn:
                continue
            if depth == 0 and i < in_hand and not c.castable:
                continue
            if depth == 0 and force is not None and (force == "pass" or c.index != force):
                continue
            if c.banned:
                continue  # (a boss forbids it: never planned)
            if _is_prism(c):
                kind = "prism"
            elif c.is_damage:
                kind = "hit"
            elif EffectKind.TRAP in c.kinds and c.target is not Target.ALLY_SINGLE:
                kind = "trap"
            elif EffectKind.BLADE in c.kinds:
                kind = "blade"
            else:
                continue
            paid = _pay(c, mine, normal, power)
            if paid is None:
                continue
            if named.setdefault(c.name, i) != i:
                continue  # (copies alike: the first one is the one cast, whether it lands or not)
            if depth == 0:
                took[0] = True
            nxt = gain(depth, *paid)
            nused = used | {i}
            missed = depth < ROLL_DEPTH and id(c) in misses
            if enchants and kind == "hit" and not c.enchanted:
                j = next((j for j in enchants if j not in used and (j < in_hand or j - in_hand < drawn)),
                         None)
                if j is not None:
                    nused = nused | {j}
                    if (i, j) not in boosted:
                        boosted[i, j] = _with_enchant(c, pool[j])
                    c = boosted[i, j]
            ndrawn = refill(drawn, len(nused))
            roll = rolls.get(id(pool[i])) if rolls and depth < ROLL_DEPTH else None
            if missed:
                if ("miss", c.name) not in tried:
                    tried.add(("miss", c.name))
                    search(depth + 1, *nxt, nused, out_fx, in_fx, hps, ndrawn, prisms, taken)
                continue
            school = c.school.lower()
            everyone = c.target is Target.ENEMY_ALL
            if kind == "blade":
                up = any(k == f"plan:{c.template_id or c.name}" for k, _s, _v in out_fx)
                dup = _is_duplicate(c, EffectKind.BLADE, me.outgoing_effects, mine)
                if (kind, c.name) in tried or up or dup:
                    continue
                tried.add((kind, c.name))
                fx = [(f"plan:{c.template_id or c.name}", (e.school or "").lower(), e.value / 100)
                      for e in c.effects if e.kind is EffectKind.BLADE]
                search(depth + 1, *nxt, nused, out_fx + fx, in_fx, hps, ndrawn, prisms, taken)
                continue
            if kind == "prism":
                for k in live:
                    if prisms[k] or not prism_worth[k] or ("prism", k) in tried:
                        continue
                    if depth == 0 and force_target not in (None, names[k]):
                        continue
                    tried.add(("prism", k))
                    np = list(prisms)
                    np[k] = True
                    search(depth + 1, *nxt, nused, out_fx, in_fx, hps, ndrawn, tuple(np), taken)
                continue
            targets = live if everyone else [k for k in live if depth > 0 or force_target in (None, names[k])]
            for k in ([None] if everyone else targets):
                key = (kind, c.name, k)
                if key in tried:
                    continue
                tried.add(key)
                hit = live if k is None else [k]
                if kind == "trap":
                    fx = [(f"plan:{c.template_id or c.name}", (e.school or "").lower(), e.value / 100)
                          for e in c.effects if e.kind is EffectKind.TRAP]
                    new_in = list(in_fx)
                    for j in hit:
                        if not any(kk == fx[0][0] for kk, _s, _v in in_fx[j]):
                            new_in[j] = in_fx[j] + fx
                    search(depth + 1, *nxt, nused, out_fx, tuple(new_in), hps, ndrawn, prisms, taken)
                    continue
                attacker = Combatant(**{**me.__dict__, "outgoing_effects": out_fx})
                new_hp, new_in, np = list(hps), list(in_fx), list(prisms)
                for j in hit:
                    victim = Combatant(**{**enemies[j].__dict__, "incoming_effects": in_fx[j],
                                          "health": hps[j]})
                    if prisms[j] and school == PRISM_SCHOOL:
                        victim = prism_view(victim)
                        np[j] = False
                    crit = None
                    if roll is not None:
                        crit = (roll[0] < crit_chance(attacker, victim, school)
                                and roll[1] >= block_chance(attacker, victim, school))
                    new_hp[j] = hps[j] - hit_damage(c, attacker, victim, crit=crit)
                    new_in[j] = _use_up(in_fx[j], school)
                search(depth + 1, *nxt, nused, _use_up(out_fx, school), tuple(new_in), tuple(new_hp), ndrawn,
                       tuple(np), taken)
        if depth == 0 and force is not None and force != "pass":
            return
        if depth == 0:
            took[0] = True
        search(depth + 1, *gain(depth, normal, power), used, out_fx, in_fx, hps, refill(drawn, len(used)),
               prisms, taken)

    search(0, battle.pips, battle.power_pips, frozenset(), list(me.outgoing_effects),
           tuple(list(e.incoming_effects) for e in enemies), start_hp, 0, start_prisms, 0.0)
    if not took[0]:
        raise Unplayable  # (a second copy of a blade that's up, a card it doesn't model)
    if best[0] <= HORIZON:
        return float(best[0]), True, (best_taken[0] < life) if hits is not None and not prune else True
    if prune and not at_horizon[0]:
        return float(CAP), True, False  # (every line has us down first)
    done = total_hp - partial[0]
    if done <= 0:
        return float(CAP), False, False if hits is not None else None
    r = min(float(CAP), HORIZON * total_hp / done)
    if hits is None or prune:
        return r, False, None
    return r, False, partial_taken[0] + (r - HORIZON) * partial_taken[1] < life


def _score(battle: Battle, futures, deadline: float, force=None, first=None) -> Score | None:
    """None when the forced move can't be played out by the search."""
    alive = _survive_rounds(battle)
    wins, rounds, seen = 0, 0.0, 0
    for fut in futures:
        if time.monotonic() > deadline:
            raise OutOfTime
        try:
            r, exact, won = _group_search(battle, fut.draws, fut.gains, deadline, force,
                                          first.name if first else None, fut.misses, fut.rolls)
        except Unplayable:
            return None
        seen += exact
        rounds += r
        wins += (r <= alive) if won is None else won
    k = max(1, len(futures))
    return Score(wins / k, rounds / k, seen / k)


def modeled(card: Card) -> bool:
    """A card the search plays: a hit, a blade or a trap. Others (prisms,
    heals, shields, stuns) it can't value, so they're the brain's to play."""
    from .brain import _is_prism

    kinds = card.kinds
    if _is_prism(card):
        return True
    if card.is_heal or EffectKind.SHIELD in kinds or EffectKind.STUN in kinds:
        return False
    return card.is_damage or bool({EffectKind.TRAP, EffectKind.BLADE} & kinds)


def _droppable(battle: Battle) -> list[Card]:
    """Hand cards a discard may take: those the search plays (a card it
    can't value, a prism or a heal, it would always find spare: it binned
    the Myth Prism Porrich needs), and prisms no enemy here needs. Never
    Reshuffle, treasure, item or enchant cards."""
    from .brain import _is_prism, _prism_useless, card_strength, is_reshuffle, keep_from_discard

    def spare_copy(c: Card) -> bool:  # a second copy of a big hit (the best one stays)
        return c.is_damage and any(o is not c and o.name == c.name and card_strength(o) >= card_strength(c)
                                   for o in battle.cards)

    # (Big hits, blades and traps are kept as the brain keeps them: noisy
    # early odds binned an Orthrus in round 1; a spare copy may go.)
    return [c for c in battle.cards
            if not c.treasure and not c.item and not is_reshuffle(c) and not c.is_enchant
            and ((modeled(c) and not _is_prism(c)
                  and (not keep_from_discard(c) or (spare_copy(c) and SPARE_COPIES)))
                 or (_is_prism(c) and _prism_useless(c, battle)))]


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
    if brain_action is None or brain_action.kind not in (ActionKind.CAST, ActionKind.PASS):
        return None  # (the brain's discard or enchant: free moves of its own, then it decides again)
    if (brain_action.kind is ActionKind.CAST and brain_action.card is not None
            and not modeled(brain_action.card)):
        return None  # (a prism, heal, shield or stun: the brain knows why; the search can't value it)
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
                if brain_score is None:
                    plan["move"] = (None, None)  # (its move can't be played out here: the brain's stands)
            top = None
            for force, tgt, card in ([] if "move" in plan else _moves(battle)):
                s = _score(battle, futures, deadline, force, tgt)
                if s is not None and (top is None or s.key() < top[0].key()):
                    top = (s, force, tgt, card)
        except OutOfTime:
            return None
        if "move" not in plan:
            plan["move"] = (top, brain_score)
    top, brain_score = plan["move"]
    if top is None or brain_score is None:
        # (Never over a move it couldn't score: a brain's discard read as
        # "no move" was replaced by a pass every round, 20 in a row.)
        return None
    s, force, tgt, card = top
    margin = PASS_ROUNDS if force == "pass" else OVERRIDE_ROUNDS
    if brain_score is not None and not s.better_than(brain_score, margin):
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
            front = [replace(f, draws=[card] + [c for c in f.draws if c is not card]) for f in futures]
            r = _score(battle, front, deadline).rounds
            if r < base - 0.4:
                out[name] = r
    except OutOfTime:
        return CAP, {}
    return base, out
