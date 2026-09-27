"""Choose which known spells go into the deck. Pure logic, unit tested.

The planner ranks every known spell using the same effect model the combat
brain uses, then fills the deck in priority order:

  pass 1: one copy of every chosen spell (so each role is covered)
  pass 2: up to `core_copies` of each
  pass 3: top up to the per-role copy targets

The result keeps the add steps in pass order. When they're applied, cards
beyond the deck's capacity are simply refused by the game, so the order is
what decides priority.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from .combat.model import Card, EffectKind


@dataclass
class SpellInfo:
    card: Card  # name, school, pip_cost and effects
    max_copies: int = 4

    @property
    def name(self) -> str:
        return self.card.name


@dataclass
class DeckPlan:
    totals: dict[str, int]  # final copies per spell
    steps: list[tuple[str, int]]  # add operations, in priority order

    def describe(self) -> str:
        return ", ".join(f"{n} x{c}" for n, c in self.totals.items()) or "(empty)"


@dataclass
class DeckPolicy:
    attack_spells: int = 4  # distinct attack spells to carry
    attack_copies: int = 4
    heal_copies: int = 3
    blade_copies: int = 2
    trap_copies: int = 2
    shield_copies: int = 1
    core_copies: int = 2
    capacity: int = 0  # 0 = let the game enforce its own limit
    exclude: list[str] = field(default_factory=list)
    include: dict[str, int] = field(default_factory=dict)  # always add these


def attack_score(card: Card, my_school: str) -> float:
    dmg = card.base_damage()
    per_pip = dmg / max(1, card.pip_cost)
    score = per_pip * (card.accuracy / 100)
    if card.is_aoe:
        score *= 1.3
    if my_school and card.school.lower() == my_school.lower():
        score *= 1.1  # power pips count double on school spells
    return score + dmg * 0.05


def _role(card: Card) -> str | None:
    if card.is_enchant:
        return None  # enchants are treasure/side-deck items, not trained spells
    if card.is_damage:
        return "attack"
    if card.is_heal:
        return "heal"
    kinds = card.kinds
    if EffectKind.BLADE in kinds:
        return "blade"
    if EffectKind.TRAP in kinds:
        return "trap"
    if EffectKind.SHIELD in kinds:
        return "shield"
    return None  # minions, utility, unknown: the combat brain can't use them yet


def plan_deck(spells: list[SpellInfo], my_school: str, policy: DeckPolicy | None = None) -> DeckPlan:
    policy = policy or DeckPolicy()
    excluded = {n.lower() for n in policy.exclude}
    by_name: dict[str, SpellInfo] = {}
    for s in spells:
        if s.name.lower() not in excluded and s.max_copies > 0:
            by_name.setdefault(s.name, s)
    pool = list(by_name.values())

    roles: dict[str, list[SpellInfo]] = {"attack": [], "heal": [], "blade": [], "trap": [], "shield": []}
    for s in pool:
        r = _role(s.card)
        if r:
            roles[r].append(s)

    # Attacks: always keep the cheapest one so there is something to cast on
    # round one, then the best by score.
    attacks = sorted(roles["attack"], key=lambda s: attack_score(s.card, my_school), reverse=True)
    chosen_attacks: list[SpellInfo] = []
    if attacks:
        cheapest = min(attacks, key=lambda s: (s.card.pip_cost, -attack_score(s.card, my_school)))
        chosen_attacks.append(cheapest)
        for s in attacks:
            if len(chosen_attacks) >= policy.attack_spells:
                break
            if s is not cheapest:
                chosen_attacks.append(s)
        # Strongest first so they win the capacity race.
        chosen_attacks.sort(key=lambda s: attack_score(s.card, my_school), reverse=True)

    def best(role: str, key) -> list[SpellInfo]:
        return sorted(roles[role], key=key, reverse=True)[:1]

    value = lambda s: sum(e.value for e in s.card.effects)  # noqa: E731
    groups: list[tuple[list[SpellInfo], int]] = [
        (chosen_attacks, policy.attack_copies),
        (best("heal", lambda s: s.card.heal_amount() / max(1, s.card.pip_cost)), policy.heal_copies),
        (best("blade", value), policy.blade_copies),
        (best("trap", value), policy.trap_copies),
        (best("shield", lambda s: -value(s)), policy.shield_copies),
    ]

    targets: dict[str, int] = {}
    order: list[str] = []
    for name, copies in policy.include.items():
        if name in by_name:
            targets[name] = min(copies, by_name[name].max_copies)
            order.append(name)
    for members, copies in groups:
        for s in members:
            if s.name not in targets and copies > 0:
                targets[s.name] = min(copies, s.max_copies)
                order.append(s.name)

    # Two-pass fill against capacity.
    counts: dict[str, int] = dict.fromkeys(order, 0)
    steps: list[tuple[str, int]] = []
    total = 0
    cap = policy.capacity or 10**6
    for limit in (1, policy.core_copies, None):
        for name in order:
            want = targets[name] if limit is None else min(limit, targets[name])
            add = min(want - counts[name], cap - total)
            if add > 0:
                counts[name] += add
                total += add
                steps.append((name, add))
    return DeckPlan({n: c for n, c in counts.items() if c > 0}, steps)
