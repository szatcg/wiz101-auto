"""Turn decision logic.

`decide()` returns the *next single action* for the current hand. Enchants and
discards do not end the turn, so the fighter executes them, re-reads the
battle and calls `decide()` again until it gets a CAST or PASS.

The strategy is a greedy heuristic tuned for PvE questing:
  1. Heal if we (or an ally) are in danger.
  2. If an attack is available, enchant it if possible, then pick the target
     and spell that removes the most enemy health (kills weighted heavily).
  3. Against bosses/high-health targets, stack a blade or trap first.
  4. While waiting for pips, set up blades/traps/shields.
  5. Otherwise discard dead cards (only when the hand is full) and pass.
"""

from __future__ import annotations

from dataclasses import dataclass

from .model import (
    Action,
    ActionKind,
    Battle,
    Card,
    Combatant,
    EffectKind,
    Target,
)


@dataclass
class Strategy:
    heal_threshold: float = 0.45  # heal self below this health ratio
    ally_heal_threshold: float = 0.35
    shield_threshold: float = 0.6  # shield self below this if no heal is available
    max_blades: int = 2
    max_traps: int = 2
    kill_bonus: float = 400.0  # value of removing an enemy from the fight
    boss_setup: bool = True  # stack a blade/trap before hitting a boss
    setup_health_multiplier: float = 2.0  # also buff first if target hp > best hit * this
    max_hand_size: int = 7
    allow_discard: bool = True
    summon_minions: bool = True  # keep a minion out: it soaks hits and adds damage


def expected_damage(card: Card, attacker: Combatant, target: Combatant) -> float:
    mult = (1 + attacker.outgoing_boost) * (1 + target.incoming_boost)
    return max(0.0, card.base_damage() * mult * (card.accuracy / 100.0))


def attack_value(card: Card, battle: Battle, target: Combatant | None, strat: Strategy) -> float:
    """Health removed (capped at remaining hp) plus a bonus per expected kill."""
    targets = battle.live_enemies if card.is_aoe else ([target] if target else [])
    value = 0.0
    for t in targets:
        dmg = expected_damage(card, battle.me, t)
        value += min(dmg, t.health)
        if dmg >= t.health:
            value += strat.kill_bonus
    # Prefer cheaper spells for the same result so we keep pips for later.
    return value - card.pip_cost * 5


def _castable(cards: list[Card]) -> list[Card]:
    return [c for c in cards if c.castable]


def _best_heal(battle: Battle, strat: Strategy) -> Action | None:
    heals = [c for c in _castable(battle.cards) if c.is_heal and not c.is_enchant]
    if not heals:
        return None

    me = battle.me
    if me.health_ratio < strat.heal_threshold:
        missing = me.max_health - me.health
        # Biggest heal that doesn't massively overheal; otherwise the biggest.
        fitting = [c for c in heals if c.heal_amount() <= missing * 1.25]
        card = max(fitting or heals, key=lambda c: c.heal_amount())
        target = None if card.target in (Target.SELF, Target.ALLY_ALL, Target.NONE) else me
        return Action(ActionKind.CAST, card, target, reason=f"health {me.health}/{me.max_health}")

    hurt = [a for a in battle.allies if not a.is_dead and a.health_ratio < strat.ally_heal_threshold]
    ally_heals = [c for c in heals if c.target in (Target.ALLY_SINGLE, Target.ALLY_ALL)]
    if hurt and ally_heals:
        ally = min(hurt, key=lambda a: a.health_ratio)
        card = max(ally_heals, key=lambda c: c.heal_amount())
        target = ally if card.target is Target.ALLY_SINGLE else None
        return Action(ActionKind.CAST, card, target, reason=f"ally {ally.name} low")
    return None


def _has_minion(battle: Battle) -> bool:
    return any(a.is_minion and not a.is_dead and a.health > 0 for a in battle.allies)


def _summon_action(battle: Battle, strat: Strategy) -> Action | None:
    if not strat.summon_minions or _has_minion(battle):
        return None
    summons = [c for c in _castable(battle.cards) if EffectKind.SUMMON in c.kinds]
    if not summons:
        return None
    card = max(summons, key=lambda c: c.pip_cost)  # the costlier minion is the stronger one
    return Action(ActionKind.CAST, card, None, reason="summon minion")


def _best_attack(battle: Battle, strat: Strategy) -> tuple[Card, Combatant | None, float] | None:
    best: tuple[Card, Combatant | None, float] | None = None
    for card in _castable(battle.cards):
        if not card.is_damage:
            continue
        options = [None] if card.is_aoe else battle.live_enemies
        for target in options:
            v = attack_value(card, battle, target, strat)
            if best is None or v > best[2]:
                best = (card, target, v)
    return best


def _pick_enchant(battle: Battle, attack_card: Card) -> Card | None:
    if attack_card.enchanted:
        return None
    enchants = [
        c
        for c in _castable(battle.cards)
        if c.is_enchant and EffectKind.ENCHANT_DAMAGE in c.kinds and c is not attack_card
    ]
    if not enchants:
        return None
    return max(enchants, key=lambda c: sum(e.value for e in c.effects))


def _setup_action(battle: Battle, strat: Strategy, focus: Combatant | None) -> Action | None:
    """Blade self or trap an enemy, respecting stack limits."""
    castable = _castable(battle.cards)
    me = battle.me

    blades = [c for c in castable if EffectKind.BLADE in c.kinds and not c.is_enchant]
    if blades and me.blade_count < strat.max_blades:
        card = max(blades, key=lambda c: sum(e.value for e in c.effects))
        target = me if card.target in (Target.ALLY_SINGLE,) else None
        return Action(ActionKind.CAST, card, target, reason="blade up")

    traps = [c for c in castable if EffectKind.TRAP in c.kinds and not c.is_enchant]
    if traps and battle.live_enemies:
        target = focus or max(battle.live_enemies, key=lambda e: (e.is_boss, e.health))
        if target.trap_count < strat.max_traps:
            card = max(traps, key=lambda c: sum(e.value for e in c.effects))
            t = None if card.target is Target.ENEMY_ALL else target
            return Action(ActionKind.CAST, card, t, reason=f"trap {target.name}")
    return None


def _shield_action(battle: Battle, strat: Strategy) -> Action | None:
    me = battle.me
    if me.health_ratio >= strat.shield_threshold or me.shield_count >= 2:
        return None
    shields = [c for c in _castable(battle.cards) if EffectKind.SHIELD in c.kinds and not c.is_enchant]
    if not shields:
        return None
    card = shields[0]
    target = me if card.target is Target.ALLY_SINGLE else None
    return Action(ActionKind.CAST, card, target, reason="shield while hurt")


def _discard_action(battle: Battle, strat: Strategy) -> Action | None:
    """When the hand is full and nothing useful can be cast, bin a dead card."""
    if not strat.allow_discard or len(battle.cards) < strat.max_hand_size:
        return None
    me = battle.me

    def uselessness(c: Card) -> float:
        if c.treasure:
            return -100  # never waste treasure cards
        score = 0.0
        if EffectKind.OTHER in c.kinds and len(c.kinds) == 1:
            score += 50
        if EffectKind.SUMMON in c.kinds:
            score += 40 if _has_minion(battle) else -100
        if c.is_heal and me.health_ratio > 0.9:
            score += 20
        if EffectKind.BLADE in c.kinds and me.blade_count >= strat.max_blades:
            score += 30
        if c.is_enchant:
            score += 5
        score += c.pip_cost  # expensive, uncastable cards clog the hand
        if c.is_damage:
            score -= 100
        return score

    candidate = max(battle.cards, key=uselessness)
    if uselessness(candidate) <= 0:
        return None
    return Action(ActionKind.DISCARD, candidate, reason="hand full")


def decide(battle: Battle, strat: Strategy | None = None, *, discards_left: int = 2) -> Action:
    strat = strat or Strategy()

    if not battle.live_enemies:
        return Action(ActionKind.PASS, reason="no enemies")

    heal = _best_heal(battle, strat)
    if heal:
        return heal

    summon = _summon_action(battle, strat)
    if summon:
        return summon

    attack = _best_attack(battle, strat)
    if attack:
        card, target, _ = attack
        focus = target or max(battle.live_enemies, key=lambda e: e.health)
        dmg = expected_damage(card, battle.me, focus)

        # Against something we can't kill quickly, buff first.
        if (
            strat.boss_setup
            and (focus.is_boss or focus.health > dmg * strat.setup_health_multiplier)
            and dmg < focus.health
        ):
            setup = _setup_action(battle, strat, focus)
            if setup:
                return setup

        enchant = _pick_enchant(battle, card)
        if enchant:
            return Action(ActionKind.ENCHANT, enchant, target_card=card, reason="boost attack")
        return Action(ActionKind.CAST, card, target, reason=f"~{dmg:.0f} dmg")

    setup = _setup_action(battle, strat, None)
    if setup:
        return setup

    shield = _shield_action(battle, strat)
    if shield:
        return shield

    if discards_left > 0:
        discard = _discard_action(battle, strat)
        if discard:
            return discard

    return Action(ActionKind.PASS, reason="saving pips")
