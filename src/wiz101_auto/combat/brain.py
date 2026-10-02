"""Turn decision logic.

`decide()` returns the *next single action* for the current hand. Enchants and
discards do not end the turn, so the fighter executes them, re-reads the
battle and calls `decide()` again until it gets a CAST or PASS.

The strategy is a greedy heuristic tuned for PvE questing:
  1. If a spell can finish the last enemy, cast it (the fight ends).
  2. Heal if we (or an ally) are in danger; if the heal needs one more pip,
     pass to save for it.
  3. Finish off any enemy we can (one fewer attacker), discard off-school gear
     cards (room for deck spells), then summon a minion.
  4. If an attack is available, enchant it if possible, then pick the target
     and spell that removes the most enemy health, weakest target first.
  5. Against bosses/targets that survive our best hit, trap first; hold a 2+
     pip hit (Troll) until the target is trapped, unless pips pile up.
  6. While waiting for pips, set up blades/traps/shields.
  7. Otherwise discard dead cards (only when the hand is full) and pass.
Instead of a pass, a 0-pip card is played when there is one: a hit (a wand's
Super Strike) that breaks a shield on an enemy, else a blade or trap, else a
hit that wastes no trap or blade of ours. 0-pip hits also finish enemies off.
Damage counts blades, traps, shields, weaknesses and school resistances.
"""

from __future__ import annotations

import dataclasses
from dataclasses import dataclass, replace

from ..deck_plan import minion_rank
from .model import (
    DAMAGE_KINDS,
    Action,
    ActionKind,
    Battle,
    Card,
    Combatant,
    Effect,
    EffectKind,
    Target,
)


@dataclass
class Strategy:
    heal_threshold: float = 0.35  # heal self below this health ratio
    boss_heal_threshold: float = 0.45  # bosses hit harder: heal a little sooner
    # A cast that ends the fight always wins over healing; one that kills at
    # least one enemy (one fewer attacker) does above this health.
    partial_kill_health: float = 0.25
    ally_heal_threshold: float = 0.35
    shield_threshold: float = 0.6  # shield self below this if no heal is available
    max_blades: int = 2
    max_traps: int = 2
    kill_bonus: float = 400.0  # value of removing an enemy from the fight
    boss_setup: bool = True  # stack a blade/trap before hitting a boss
    setup_health_multiplier: float = 1.0  # also trap/blade first when the target survives our best hit
    max_hand_size: int = 7
    allow_discard: bool = True
    summon_minions: bool = True  # keep a minion out: it soaks hits and adds damage
    # A 4-player dungeon (Mount Olympus): the side fills up with players, so a
    # minion is useless there: never summon, discard minion cards.
    no_minions: bool = False
    focus_bonus: float = 60.0  # value of taking a target's whole remaining health
    # Pass a round for an attack one pip away when the best castable one does
    # less than this share of its damage (two 1-pip hits < one 2-pip Troll).
    save_for_stronger: float = 0.6
    discard_junk: bool = True  # bin off-school gear attack cards to draw deck spells
    hold_big_hit_until_pips: int = 4  # wait for a trap before a 2+ pip hit, up to this many pips
    quick_fight_rounds: int = 3  # no boss and done within this many rounds: don't summon
    aoe_max_setups: int = 4  # blades/traps played before the hit-all (tipping it into kills)
    stun_health: float = 0.7  # stun the worst hitter once our health is below this
    prism_early_gain: float = 2.0  # a prism multiplying our hit this much is played first


# Without readable stats, assume the usual pattern: a monster resists its own
# school and is weak to the opposite one.
OPPOSITE = {"fire": "ice", "ice": "fire", "storm": "myth", "myth": "storm", "life": "death", "death": "life"}
DEFAULT_SAME_SCHOOL_RESIST = 0.3
DEFAULT_OPPOSITE_BOOST = 0.3


def school_multiplier(card: Card, attacker: Combatant, target: Combatant) -> float:
    """Damage multiplier from the target's resistance/weakness to the card's school
    and the attacker's damage bonus for it."""
    school = card.school.lower()
    if not school:
        return 1.0
    if target.resist is not None:
        resist = target.resist.get(school, 0.0)
    elif target.school and school == target.school:
        resist = DEFAULT_SAME_SCHOOL_RESIST
    elif target.school and OPPOSITE.get(target.school) == school:
        resist = -DEFAULT_OPPOSITE_BOOST
    else:
        resist = 0.0
    return max(0.0, 1 - resist) * (1 + attacker.damage_bonus.get(school, 0.0))


def effect_multiplier(effects: list[tuple[str, str, float]], fallback: float, school: str) -> float:
    """A hit uses one of each distinct trap/blade (copies of the same spell
    don't stack) whose school matches the spell's; different ones multiply."""
    if not effects:
        return 1 + fallback
    school = school.lower()
    mult, seen = 1.0, set()
    for key, eff_school, value in effects:
        if key in seen or (eff_school and eff_school != school):
            continue
        seen.add(key)
        mult *= 1 + value
    return mult


CRIT_SURE = 0.85  # a crit this likely is counted on (Deimos's threshold)


def _resist(school: str, target: Combatant) -> float:
    if target.resist is not None:
        return target.resist.get(school, 0.0)
    if target.school and school == target.school:
        return DEFAULT_SAME_SCHOOL_RESIST
    if target.school and OPPOSITE.get(target.school) == school:
        return -DEFAULT_OPPOSITE_BOOST
    return 0.0


def _wards(target: Combatant, school: str, pierce: float) -> tuple[float, float]:
    """(multiplier, pierce left) through the target's traps and shields, in
    the order they hang (newest first): pierce breaks through shields and is
    used up doing it (a 30% shield against 20% pierce: -10%, no pierce left)."""
    if not target.incoming_effects:
        boost = target.incoming_boost
        if boost < 0:
            through = min(0.0, boost + pierce)
            pierce = max(0.0, pierce + boost)
            boost = through
        return 1 + boost, pierce
    mult, seen = 1.0, set()
    for key, eff_school, value in target.incoming_effects:
        if key in seen or (eff_school and eff_school != school):
            continue
        seen.add(key)
        if value < 0:
            through = min(0.0, value + pierce)
            pierce = max(0.0, pierce + value)
            value = through
        mult *= 1 + value
    return mult, pierce


def _flat(effects: list[tuple[str, str, float]], school: str) -> float:
    seen, total = set(), 0.0
    for key, eff_school, value in effects:
        if key in seen or (eff_school and eff_school != school):
            continue
        seen.add(key)
        total += value
    return total


def crit_chance(attacker: Combatant, target: Combatant, school: str) -> float:
    """The game's crit chance from critical and block ratings (Deimos:
    0.03 x level x crit / (3 x crit + block))."""
    crit = attacker.crit.get(school, 0.0)
    if crit <= 0 or not attacker.level:
        return 0.0
    return 0.03 * min(attacker.level, 100) * crit / (3 * crit + target.block.get(school, 0.0))


def _one_hit(base: float, school: str, attacker: Combatant, target: Combatant, wards: bool) -> float:
    """One hit's damage, in the game's order (Deimos's combat_math): damage
    stat and flat damage, blades and auras, the target's traps and shields
    (pierce breaking shields), a near-sure crit, flat resist, then resist
    less what pierce is left (a negative resist is a boost)."""
    dmg = base * (1 + attacker.damage_bonus.get(school, 0.0)) + attacker.damage_flat.get(school, 0.0)
    dmg *= effect_multiplier(attacker.outgoing_effects, attacker.outgoing_boost, school)
    dmg *= (1 + attacker.aura.get(school, 0.0)) * (1 + attacker.aura.get("", 0.0))
    dmg += _flat(attacker.outgoing_flat, school)
    pierce = attacker.pierce.get(school, 0.0) + target.incoming_pierce
    if wards:
        mult, pierce = _wards(target, school, pierce)
        dmg = dmg * mult + _flat(target.incoming_flat, school)
    if crit_chance(attacker, target, school) >= CRIT_SURE:
        crit, block = attacker.crit.get(school, 0.0), target.block.get(school, 0.0)
        dmg *= 2 - block / (crit / 3 + block)
    dmg = max(0.0, dmg - target.resist_flat.get(school, 0.0))
    resist = _resist(school, target)
    if resist > 0:
        resist -= pierce
        return dmg * (1 - resist) if resist > 0 else dmg
    return dmg * (1 - resist)


def hit_damage(card: Card, attacker: Combatant, target: Combatant) -> float:
    """Damage if the spell lands (`_one_hit` per hit). A spell that hits twice
    (Minotaur: 50, then 445): our blades boost both hits, but the target's
    traps and shields break on the first (a Feint under a Minotaur boosts
    only the 50)."""
    school = card.school.lower()
    hits = [e.value for e in card.effects if e.kind in DAMAGE_KINDS]
    if len(hits) <= 1:
        return _one_hit(card.base_damage(), school, attacker, target, wards=True)
    return sum(_one_hit(h, school, attacker, target, wards=i == 0) for i, h in enumerate(hits))


def damage_breakdown(attacker: Combatant, target: Combatant, card: Card) -> str:
    """The multipliers behind a damage estimate, for the log."""
    trap = effect_multiplier(target.incoming_effects, target.incoming_boost, card.school)
    blade = effect_multiplier(attacker.outgoing_effects, attacker.outgoing_boost, card.school)
    school = school_multiplier(card, attacker, target)
    return f"x{trap:.2f} trap/shield, x{blade:.2f} blade, x{school:.2f} school"


def expected_damage(card: Card, attacker: Combatant, target: Combatant) -> float:
    """hit_damage weighted by the chance to land (for comparing spells)."""
    return hit_damage(card, attacker, target) * (card.accuracy / 100.0)


def attack_value(card: Card, battle: Battle, target: Combatant | None, strat: Strategy) -> float:
    """Health removed (capped at remaining hp) plus a bonus per expected kill."""
    targets = battle.live_enemies if card.is_aoe else ([target] if target else [])
    value = 0.0
    for t in targets:
        dmg = expected_damage(card, battle.me, t)
        value += min(dmg, t.health)
        if dmg >= t.health:
            value += strat.kill_bonus
        # Focus fire: the bigger the share of a target's remaining health we take,
        # the sooner it stops attacking (so the weakest enemy goes first).
        value += strat.focus_bonus * min(1.0, dmg / max(1, t.health))
    # Prefer cheaper spells for the same result so we keep pips for later.
    return value - card.pip_cost * 5


def predicted_damage(battle: Battle, action: Action) -> dict[int, int]:
    """Damage the move should do, by position in battle.enemies (an AoE hits
    every live enemy). Empty for anything but an attack."""
    card = action.card
    if action.kind is not ActionKind.CAST or card is None or not card.is_damage:
        return {}
    out = {}
    for i, e in enumerate(battle.enemies):
        if e.is_dead or e.health <= 0:
            continue
        if card.is_aoe or e is action.target:
            out[i] = round(hit_damage(card, battle.me, e))
    return out


def _castable(cards: list[Card]) -> list[Card]:
    return [c for c in cards if c.castable]


def heal_threshold(battle: Battle, strat: Strategy) -> float:
    boss = any(e.is_boss for e in battle.live_enemies)
    return max(strat.heal_threshold, strat.boss_heal_threshold) if boss else strat.heal_threshold


def _best_heal(battle: Battle, strat: Strategy) -> Action | None:
    heals = [c for c in _castable(battle.cards) if c.is_heal and not c.is_enchant]
    if not heals:
        return None

    me = battle.me
    if me.health_ratio < heal_threshold(battle, strat):
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


def _drain_heal(battle: Battle, strat: Strategy) -> Action | None:
    """Low with no heal to cast: a drain (Vampire) heals by half the damage
    it deals, so it's the heal (on the enemy it hurts most)."""
    if battle.me.health_ratio >= heal_threshold(battle, strat):
        return None
    drains = [c for c in _castable(battle.cards) if EffectKind.STEAL in c.kinds and not c.is_enchant]
    live = battle.live_enemies
    if not drains or not live:
        return None
    card, target = max(((c, e) for c in drains for e in live),
                       key=lambda ce: min(hit_damage(ce[0], battle.me, ce[1]), ce[1].health))
    return Action(ActionKind.CAST, card, target,
                  reason=f"health {battle.me.health}/{battle.me.max_health}: draining to heal")


PARTY_SIZE = 4  # places on our side of the duel circle


def party_full(battle: Battle) -> bool:
    """Our side's places all taken (four players): a minion has nowhere to
    stand, so summoning one does nothing."""
    return 1 + len(battle.allies) >= PARTY_SIZE


def _has_minion(battle: Battle) -> bool:
    return any(a.is_minion and not a.is_dead and a.health > 0 for a in battle.allies)


SUMMON_ROUNDS_ONE_ENEMY = 2  # with a single enemy, a minion only pays off from the opening rounds


def _summon_action(battle: Battle, strat: Strategy) -> Action | None:
    """A minion once per fight: early on it soaks hits and traps for us; later,
    or a second one, costs all our pips when blade, trap and a big hit end it
    sooner (Itennu Sokkwi: a second Troll Minion with one enemy left)."""
    if (
        not strat.summon_minions or strat.no_minions or party_full(battle)
        # Only against a boss: in everyday fights blades, traps and one big
        # Humongofrog are faster (the player's rule).
        or not any(e.is_boss for e in battle.live_enemies)
        or _has_minion(battle) or battle.summoned >= 1
        or battle.me.health_ratio < DESPERATE_HEALTH  # all our pips while a hit away from dead
    ):
        return None
    if len(battle.live_enemies) == 1 and battle.round > SUMMON_ROUNDS_ONE_ENEMY:
        return None
    summons = [c for c in _castable(battle.cards) if EffectKind.SUMMON in c.kinds]
    if not summons:
        return None
    # Minion spells are X-pip: they spend every pip we have. If a trapped target
    # can be hit with a big spell now (or next round), that hit is worth more.
    pips = battle.pips + battle.power_pips
    for target in battle.live_enemies:
        for c in battle.cards:
            if not c.is_damage or c.pip_cost < 2 or c.pip_cost > pips + 1:
                continue
            if effect_multiplier(target.incoming_effects, target.incoming_boost, c.school) > 1.0:
                return None
    card = max(summons, key=lambda c: minion_rank(c.template_name or c.name))  # newest minion is the best one
    return Action(ActionKind.CAST, card, None, reason="summon minion")


OVERKILL = 2.5  # a big single hit doing this many times a target's health is wasted on it...
OVERKILL_PIPS = 4  # ...when it costs this much and a tougher enemy is still up


def _overkill(card: Card, target: Combatant, battle: Battle) -> bool:
    """Stone Colossus (~2064) to finish a 700-health Kakeda Shadow while
    Tomugawa had 1357 left: that was the boss's hit."""
    if card.pip_cost < OVERKILL_PIPS:
        return False
    tougher = any(e is not target and e.health > target.health for e in battle.live_enemies)
    return tougher and hit_damage(card, battle.me, target) > OVERKILL * target.health


def _kill_action(battle: Battle) -> Action | None:
    """A cast that finishes off at least one enemy if it lands: most kills first,
    then the cheapest spell (keep pips), then the weakest target."""
    best: tuple[tuple, Action] | None = None
    for card in _castable(battle.cards):
        if not card.is_damage:
            continue
        options = [None] if card.is_aoe else battle.live_enemies
        for target in options:
            victims = battle.live_enemies if target is None else [target]
            kills = [t for t in victims if hit_damage(card, battle.me, t) >= t.health]
            if not kills:
                continue
            if target is not None and _overkill(card, target, battle):
                continue  # the boss's big hit isn't spent on a small enemy
            # Same number of kills: the one that also hurts the survivors most
            # (Humongofrog over Ether Golem), then the cheapest.
            spill = sum(min(hit_damage(card, battle.me, t), t.health) for t in victims if t not in kills)
            key = (len(kills), round(spill), -card.pip_cost, -min(t.health for t in kills))
            if best is None or key > best[0]:
                name = kills[0].name if len(kills) == 1 else f"{len(kills)} enemies"
                dmg = hit_damage(card, battle.me, kills[0])
                best = (key, Action(ActionKind.CAST, card, target, reason=f"finish {name}: ~{dmg:.0f} dmg"))
    return best[1] if best else None


def _kills_all(battle: Battle, action: Action) -> bool:
    """Does this cast finish every enemy left (ending the fight)?"""
    card = action.card
    if card is None:
        return False
    victims = battle.live_enemies if action.target is None else [action.target]
    return len(victims) == len(battle.live_enemies) and all(
        hit_damage(card, battle.me, t) >= t.health for t in victims
    )


def _save_for_heal(battle: Battle, strat: Strategy) -> Action | None:
    """Low on health with a heal in hand we can't afford yet: spend nothing, so
    it's castable next round (a pip comes in every round)."""
    if battle.me.health_ratio >= heal_threshold(battle, strat):
        return None
    pips = battle.pips + battle.power_pips
    waiting = [c for c in battle.cards if c.is_heal and not c.is_enchant and pips < c.pip_cost <= pips + 1]
    if not waiting:
        return None
    card = max(waiting, key=lambda c: c.heal_amount())
    return Action(ActionKind.PASS, reason=f"saving pips to cast {card.name} next round")


def _trap_coming(battle: Battle) -> bool:
    """Could a trap land on an enemy soon: one in our hand, or our minion alive?"""
    if any(EffectKind.TRAP in c.kinds and not c.is_enchant for c in battle.cards):
        return True
    return any(a.is_minion and not a.is_dead for a in battle.allies)


def _stronger_next_round(battle: Battle, focus: Combatant, dmg_now: float, strat: Strategy) -> Card | None:
    """An attack one pip out of reach that hits much harder than the best we can
    cast now (Troll vs a 1-pip wand card): worth passing a round for it."""
    pips = battle.pips + battle.power_pips
    waiting = [
        c for c in battle.cards
        if c.is_damage and not c.castable and not c.treasure and pips < c.pip_cost <= pips + 1
    ]
    if not waiting:
        return None
    best = max(waiting, key=lambda c: expected_damage(c, battle.me, focus))
    if dmg_now < expected_damage(best, battle.me, focus) * strat.save_for_stronger:
        return best
    return None


KEEP_CHEAP_HITS = 2  # 1-pip damage cards kept in hand to finish enemies off


def _finish_in_reach(battle: Battle, rounds: int = 2) -> bool:
    """Can a card in hand kill every enemy left once we have the pips (a pip
    comes each round)? Then a minion (all our pips) would only delay that."""
    pips = battle.pips + battle.power_pips + rounds
    enemies = battle.live_enemies
    if not enemies:
        return False
    for c in battle.cards:
        if not c.is_damage or c.pip_cost > pips:
            continue
        victims = enemies if c.is_aoe else enemies[:1] if len(enemies) == 1 else []
        if victims and all(hit_damage(c, battle.me, e) >= e.health for e in victims):
            return True
    return False


KEEP_FREE_HITS = 1  # 0-pip hits (Super Strike) kept in hand; extra copies are discarded


def _junk_discard(battle: Battle, strat: Strategy) -> Action | None:
    """Off-school attack cards from gear (a starter wand's Fire Cat, Dark Sprite...)
    only take hand slots our own spells and traps could fill. Extra copies of a
    0-pip hit go too (one stays for shields and finishing blows): discarding
    draws the deck's stronger cards sooner."""
    if not strat.discard_junk:
        return None
    if strat.no_minions or party_full(battle):
        minions = [c for c in battle.cards if EffectKind.SUMMON in c.kinds and not c.treasure]
        if minions:
            return Action(ActionKind.DISCARD, minions[0], reason="a 4-player fight: no use for a minion")
    school = battle.me.school.lower()
    junk = [
        c for c in battle.cards
        if school and c.item and c.is_damage and not c.treasure
        and c.pip_cost > 0 and c.school.lower() != school
    ]
    # Cheap hits finish off a nearly dead enemy without waiting rounds for pips:
    # keep a couple in hand, off-school gear cards included.
    cheap = [c for c in battle.cards if c.is_damage and not c.treasure and c.pip_cost <= 1]
    if len(cheap) <= KEEP_CHEAP_HITS:
        junk = [c for c in junk if c.pip_cost > 1]
    # Never one the fight plan needs (Fire Elf finishing Krokopatra).
    junk = [c for c in junk if not _plan_needs(battle, c)]
    if junk:
        card = min(junk, key=lambda c: c.base_damage())
        return Action(ActionKind.DISCARD, card, reason="off-school gear card; making room for deck spells")
    snakes = [c for c in battle.cards if junk_gear_hit(c, battle)]
    if snakes:
        why = "a gear card hitting off-school; its trap fits nothing"
        return Action(ActionKind.DISCARD, snakes[0], reason=why)
    # Setup that boosts none of our hits (the Frost Snake pet's ice trap),
    # gear cards the plan never plays (the amulet's Spirit Armor): they only
    # keep the blade/trap/big-hit cards out of the hand.
    unfit = [c for c in battle.cards if not c.treasure and not c.is_damage
             and (EffectKind.BLADE in c.kinds or EffectKind.TRAP in c.kinds) and not setup_fits(c, battle)]
    if unfit:
        return Action(ActionKind.DISCARD, unfit[0], reason="a trap/blade for a school none of our hits use")
    gear = [c for c in battle.cards if c.item and not c.treasure and not c.is_damage and not c.is_heal
            and EffectKind.BLADE not in c.kinds and EffectKind.TRAP not in c.kinds]
    if gear:
        return Action(ActionKind.DISCARD, gear[0], reason="a gear card the plan never plays")
    useless = [
        c for c in battle.cards
        if not c.treasure and not c.is_enchant and not c.is_damage and EffectKind.SHIELD in c.kinds
        and _shield_useless(c, battle.live_enemies)
    ]
    if useless:
        return Action(ActionKind.DISCARD, useless[0], reason="shield for schools none of these enemies use")
    prisms = [c for c in battle.cards if _is_prism(c) and not c.treasure and _prism_useless(c, battle)]
    if prisms:
        why = "prism: no enemy here takes more from the other school"
        return Action(ActionKind.DISCARD, prisms[0], reason=why)
    free = [c for c in battle.cards if c.pip_cost == 0 and c.is_damage and not c.treasure]
    if len(free) > KEEP_FREE_HITS:
        card = min(free, key=lambda c: c.base_damage())
        why = f"spare 0-pip hit (keeping {KEEP_FREE_HITS}); drawing for stronger cards"
        return Action(ActionKind.DISCARD, card, reason=why)
    return None


def _is_trapped(target: Combatant) -> bool:
    return target.trap_count > 0 or target.incoming_boost > 0


def _best_attack(battle: Battle, strat: Strategy) -> tuple[Card, Combatant | None, float] | None:
    best: tuple[Card, Combatant | None, float] | None = None
    for card in _castable(battle.cards):
        # 0-pip hits are played by the free-hit rules (or to finish an enemy),
        # not as the turn's attack: a chip hit would use up our traps and blades.
        if not card.is_damage or card.pip_cost == 0:
            continue
        options = [None] if card.is_aoe else battle.live_enemies
        for target in options:
            v = attack_value(card, battle, target, strat)
            if best is None or v > best[2]:
                best = (card, target, v)
    return best


AOE_MIN_ENEMIES = 2  # a hit-all spell (Humongofrog) is the plan from this many enemies


AOE_BLADE_WAIT_HEALTH = 0.4  # above this, the hit-all spell waits for a blade still in the deck


def _group_aoe(battle: Battle) -> Card | None:
    """Against a group: the hit-all spell in hand, else one still in the deck."""
    if len(battle.live_enemies) < AOE_MIN_ENEMIES:
        return None
    for pool in (battle.cards, battle.upcoming):
        aoes = [c for c in pool if c.is_damage and c.is_aoe and c.pip_cost >= 2]
        if aoes:
            return max(aoes, key=lambda c: c.base_damage())
    return None


HAND_SIZE = 7  # cards in a full hand
DESPERATE_HEALTH = 0.25  # below this: no minions or prisms, only what keeps us alive or hits
BOSS_SUMMON_ROUNDS = 3  # against a boss, the minion comes in these first rounds
GROUP_SUMMON_ROUNDS = 2  # against a group, the minion comes in these first rounds
AOE_SAVE_HEALTH = 0.5  # above this, pips go to the hit-all spell rather than a single kill
AOE_SAVE_HEALTH_KILL_ALL = 0.3  # ... down to this when the hit-all (in hand) kills them all next round


def _saving_for_aoe(battle: Battle, kill: Action) -> bool:
    """Hold a 2+ pip single-target kill for the hit-all spell (in hand or deck)."""
    card = kill.card
    if card is None or card.is_aoe or card.pip_cost < 2:
        return False
    aoe = _group_aoe(battle)
    if aoe is None:
        return False
    if battle.me.health_ratio >= AOE_SAVE_HEALTH:
        return True
    # Lower health, but the hit-all is in hand, affordable next round and
    # kills them all then: still worth the round (Cyclops took one of two
    # Scurriers at 48% health; Humongofrog a round later would take both).
    have = battle.pips + 2 * battle.power_pips + 1
    return (
        battle.me.health_ratio >= AOE_SAVE_HEALTH_KILL_ALL
        and aoe in battle.cards and have >= aoe.pip_cost
        and all(hit_damage(aoe, battle.me, e) >= e.health for e in battle.live_enemies)
    )


def _aoe_trap_target(battle: Battle, card: Card) -> Combatant:
    """Where the next trap helps the hit-all spell most: an enemy it would then
    kill outright (the Napper at 525 with Humongofrog doing ~400), else the
    one with the fewest traps, then the toughest. Spread out, not stacked."""
    me = battle.me
    traps = [c for c in battle.cards if EffectKind.TRAP in c.kinds and not c.is_enchant]
    boost = 1 + (max(_power(c) for c in traps) if traps else 30) / 100

    def tipped(e: Combatant) -> bool:
        dmg = hit_damage(card, me, e)
        return dmg < e.health <= dmg * boost

    def gain(e: Combatant) -> float:
        # What a trap there adds to the hit: little on an enemy that resists it
        # (Meowiarty resists myth 80%: traps on him were wasted on the frog).
        dmg = hit_damage(card, me, e)
        return min(dmg * boost, e.health) - min(dmg, e.health)

    return min(
        battle.live_enemies,
        key=lambda e: (not tipped(e), e.trap_count, -gain(e)),
    )


def _dig_for_setup(battle: Battle, strat: Strategy) -> Action | None:
    """Blades and traps win fights; a Troll or Cyclops without them doesn't.
    While a blade or trap slot is open and one is still in the deck (and
    none in hand), or against a group while the hit-all spell is still to
    come, discard single-target hits to draw them. Against one enemy the
    best hit stays in hand; against a group none has to."""
    enemies = battle.live_enemies
    if not enemies:
        return None
    me = battle.me
    focus = max(enemies, key=lambda e: e.health)
    upcoming = battle.upcoming

    def in_hand(kind: EffectKind) -> bool:
        return any(kind in c.kinds and not c.is_enchant for c in battle.cards)

    def to_come(kind: EffectKind) -> bool:
        return any(kind in c.kinds and not c.is_enchant for c in upcoming)

    want = []
    if me.blade_count < strat.max_blades and not in_hand(EffectKind.BLADE) and to_come(EffectKind.BLADE):
        want.append("blade")
    if focus.trap_count < strat.max_traps and not in_hand(EffectKind.TRAP) and to_come(EffectKind.TRAP):
        want.append("trap")
    prisms_to_come = [c for c in upcoming if _is_prism(c)]
    # Only against a boss (Meowiarty): in an everyday fight it binned a
    # Minotaur to dig for a prism against two bandits.
    boss_fight = any(e.is_boss for e in enemies)
    if boss_fight and prisms_to_come and not any(_is_prism(c) for c in battle.cards):
        prism = prisms_to_come[0]
        gain_needed = strat.prism_early_gain
        if any(e.name not in battle.prismed and _prism_gain(prism, me, e, battle.cards) >= gain_needed
               for e in enemies):
            want.append("prism")
    group = len(enemies) >= AOE_MIN_ENEMIES
    frog_in_hand = any(c.is_aoe and c.is_damage for c in battle.cards)
    if group and not frog_in_hand and any(c.is_aoe and c.is_damage for c in upcoming):
        want.append("hit-all spell")
    if not want:
        return None
    # A hit that kills someone now is no junk (Cyclops with enemies at 114/150).
    singles = [
        c for c in battle.cards
        if c.is_damage and not c.is_aoe and all(hit_damage(c, me, e) < e.health for e in enemies)
    ]
    if group and ("blade" in want or "trap" in want):
        # Against a group everything but the plan's cards may go: minions, prisms.
        singles += [
            c for c in battle.cards
            if not c.is_damage and not c.is_heal and not c.is_enchant
            and not ({EffectKind.BLADE, EffectKind.TRAP, EffectKind.SHIELD} & set(c.kinds))
        ]
    if any(e.is_boss for e in enemies):
        # A boss has a lot of health: the deck runs dry before it does (it
        # passed with 5+2 pips and nothing to cast against Meowiarty after
        # digging away Cyclops, Minotaur and the Troll Minion). Big hits and
        # the minion stay; only cheap hits may go.
        minions_ok = not (strat.no_minions or party_full(battle))
        singles = [
            c for c in singles
            if not (c.is_damage and c.pip_cost >= 2)
            and not (EffectKind.SUMMON in c.kinds and minions_ok)
        ]
    if not group:
        if len(singles) < 2:
            return None
        best = max(singles, key=lambda c: expected_damage(c, me, focus))
        singles = [c for c in singles if c is not best]
    if not singles:
        return None
    card = min(singles, key=lambda c: expected_damage(c, me, focus))
    return Action(ActionKind.DISCARD, card, reason=f"digging for a {' / '.join(want)}")


AOE_PLAN_MAX_SETUPS = 4  # blades/traps looked at together before one hit-all


def _setup_fx(card: Card, kind: EffectKind, school: str) -> tuple[str, str, float] | None:
    """The blade/trap `card` would hang for a `school` spell, as an effect
    tuple (key, school, value); None if it doesn't boost that school. Uses the
    effect's own school: Spirit Blade is a Balance card with Myth/Life/Death
    blades, Feint a Death card whose trap boosts every school."""
    fits = [e for e in card.effects if e.kind is kind and e.school in ("", school)]
    if not fits:
        return None
    best = max(fits, key=lambda e: e.value)
    return (f"plan:{card.name}", best.school, best.value / 100)


def _hit_all_setup(battle: Battle, card: Card, strat: Strategy | None = None) -> Action | None:
    """Humongofrog as it stands vs. with blades/traps from the hand: if some
    of them make it kill enemies it wouldn't now, play one of the smallest such
    set (every blade boosts the whole hit, each trap only its enemy; a pip
    comes each round and the hit-all must still be affordable after). None
    when no set adds a kill (then the usual set-up/cast rules apply)."""
    import itertools

    me = battle.me
    enemies = battle.live_enemies
    school = card.school.lower()

    def outcome(out_fx: list, in_fx: dict[int, list]) -> tuple[int, float]:
        attacker = Combatant(**{**me.__dict__, "outgoing_effects": out_fx})
        kills, total = 0, 0.0
        for e in enemies:
            victim = Combatant(**{**e.__dict__, "incoming_effects": in_fx[id(e)]})
            dmg = hit_damage(card, attacker, victim)
            kills += dmg >= e.health
            total += min(dmg, e.health)
        return kills, total

    base_in = {id(e): list(e.incoming_effects) for e in enemies}
    now_kills, _ = outcome(list(me.outgoing_effects), base_in)
    if now_kills == len(enemies):
        return None
    moves = []  # (card, target, kind, fx, hits)
    for c in battle.cards:
        if c.is_enchant or c is card:
            continue
        if EffectKind.BLADE in c.kinds:
            fx = _setup_fx(c, EffectKind.BLADE, school)
            if fx and not any(s == fx[1] and abs(v - fx[2]) < 0.005 for _k, s, v in me.outgoing_effects):
                moves.append((c, me if c.target is Target.ALLY_SINGLE else None, "blade", fx, ()))
        if EffectKind.TRAP in c.kinds:
            fx = _setup_fx(c, EffectKind.TRAP, school)
            if not fx:
                continue
            if c.target is Target.ENEMY_ALL:
                moves.append((c, None, "trap", fx, tuple(id(e) for e in enemies)))
                continue
            keep_for = prism_target(battle) if not fx[1] else None
            for e in enemies:
                if keep_for is not None and e.name != keep_for.name:
                    continue  # Feint (any school) is for the prismed boss, not the adds
                if not any(s == fx[1] and abs(v - fx[2]) < 0.005 for _k, s, v in e.incoming_effects):
                    moves.append((c, e, "trap", fx, (id(e),)))
    if not moves:
        return None
    have = battle.pips + 2 * battle.power_pips
    best = None
    most = strat.aoe_max_setups if strat else AOE_PLAN_MAX_SETUPS
    for n in range(1, min(most, len(moves)) + 1):
        for combo in itertools.combinations(moves, n):
            if len({id(m[0]) for m in combo}) < n:
                continue  # one card, one use
            order = sorted(combo, key=lambda m: m[0].pip_cost)
            pips, ok = have, True
            for m in order:
                if m[0].pip_cost > pips:
                    ok = False
                    break
                pips = pips - m[0].pip_cost + 1
            if not ok or pips < card.pip_cost:
                continue  # the hit-all wouldn't be affordable right after
            out_fx = list(me.outgoing_effects)
            in_fx = {k: list(v) for k, v in base_in.items()}
            for _c, _t, kind, fx, hits in order:
                if kind == "blade":
                    if fx[0] not in {k for k, _s, _v in out_fx}:
                        out_fx.append(fx)
                else:
                    for h in hits:
                        if fx[0] not in {k for k, _s, _v in in_fx[h]}:
                            in_fx[h].append(fx)
            kills, total = outcome(out_fx, in_fx)
            key = (kills, -n, -sum(m[0].pip_cost for m in combo), total)
            if best is None or key > best[0]:
                best = (key, order)
    if best is None or best[0][0] <= now_kills:
        return None
    (kills, n, _c, _t), order = best
    first = order[0]
    c, target, kind, _fx, _hits = first
    if not c.castable:
        return None
    why = f"{kind} so {card.name} kills {kills} of {len(enemies)} (now {now_kills}; {-n} set-up card(s))"
    return Action(ActionKind.CAST, c, target, reason=why)


def _aoe_plan(battle: Battle, strat: Strategy) -> Action | None:
    """Several enemies and a hit-all spell in hand (Humongofrog): blade
    ourselves, trap the enemies, then one hit clears the board. Pips are
    saved for it (nothing else is cast) until it's affordable; blades and
    traps come first while there are any left to hang."""
    enemies = battle.live_enemies
    if len(enemies) < AOE_MIN_ENEMIES:
        return None
    aoes = [c for c in battle.cards if c.is_damage and c.is_aoe and c.pip_cost >= 2]
    if not aoes:
        coming = _group_aoe(battle)
        if coming is None or battle.me.health_ratio < AOE_SAVE_HEALTH:
            return None
        # Still in the deck: set up for it (a blade, a trap on each enemy),
        # with 0-pip cards only, and keep the pips for when it's drawn.
        setup = _setup_action(battle, strat, _aoe_trap_target(battle, coming))
        if setup and setup.card is not None and setup.card.pip_cost == 0:
            return setup
        # Pips to spare beyond what the frog will need: use them on a hit
        # (it passed three rounds on 4+3P pips against the Klaw brothers).
        have = battle.pips + 2 * battle.power_pips
        spare = [c for c in _castable(battle.cards) if c.is_damage and have - c.pip_cost >= coming.pip_cost]
        if spare:
            best = _best_attack(Battle(**{**battle.__dict__, "cards": spare}), strat)
            if best:
                card, target, _ = best
                why = f"spare pips; {coming.name} still affordable after"
                return Action(ActionKind.CAST, card, target, reason=why)
        return Action(ActionKind.PASS, reason=f"saving pips for {coming.name} (still in the deck)")
    card = max(aoes, key=lambda c: sum(min(hit_damage(c, battle.me, e), e.health) for e in enemies))
    if card.castable and all(hit_damage(card, battle.me, e) >= e.health for e in enemies):
        total = sum(e.health for e in enemies)
        why = f"{card.name} kills all {len(enemies)} (~{total:.0f})"
        return Action(ActionKind.CAST, card, None, reason=why)
    # Blades/traps that make the hit-all kill enemies it wouldn't now: those first.
    if battle.me.health_ratio >= AOE_BLADE_WAIT_HEALTH or not card.castable:
        tipping = _hit_all_setup(battle, card, strat)
        if tipping:
            return tipping
    setup = _break_shield(battle) or _setup_action(battle, strat, _aoe_trap_target(battle, card))
    if setup and (not card.castable or setup.card is None or setup.card.pip_cost == 0):
        return setup
    if setup and setup.card is not None and battle.me.health_ratio >= AOE_BLADE_WAIT_HEALTH:
        # A 1-pip blade or trap (Spirit Blade, Feint) before the hit-all, as
        # long as it's still affordable next round (a pip comes each round).
        have = battle.pips + 2 * battle.power_pips
        if have - setup.card.pip_cost + 1 >= card.pip_cost:
            return setup
    if not card.castable:
        return Action(ActionKind.PASS, reason=f"saving pips for {card.name} (hits all {len(enemies)})")
    # A pass draws nothing with a full hand (cards come only for cards used):
    # waiting on a blade still in the deck then waits forever (two passes at
    # 7 pips against Meowiarty's trio while it hit us for 400).
    blade_to_come = len(battle.cards) < HAND_SIZE and any(
        EffectKind.BLADE in c.kinds and not c.is_enchant for c in battle.upcoming
    )
    if (
        battle.me.blade_count == 0 and blade_to_come
        and battle.me.health_ratio >= AOE_BLADE_WAIT_HEALTH
    ):
        return Action(ActionKind.PASS, reason=f"holding {card.name} until a blade is up")
    enchant = _pick_enchant(battle, card)
    if enchant:
        return Action(ActionKind.ENCHANT, enchant, target_card=card, reason=f"boost {card.name}")
    total = sum(min(hit_damage(card, battle.me, e), e.health) for e in enemies)
    return Action(ActionKind.CAST, card, None, reason=f"{card.name} on all {len(enemies)}: ~{total:.0f} dmg")


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


def _is_duplicate(card: Card, kind: EffectKind, effects: list[tuple[str, str, float]],
                  mine: str = "") -> bool:
    """Is this blade/trap already hanging (same school and size)? Copies of one
    spell don't stack: a second one adds nothing to the next hit. Each part is
    judged by its own school (Spirit Blade is a Balance card with myth, life
    and death blades), and only parts that boost `mine` (our school) count."""
    for e in card.effects:
        if e.kind is not kind:
            continue
        if mine and e.school and e.school.lower() != mine:
            continue  # boosts another school: irrelevant to our hits
        # A part without a school boosts every school (Feint); hanging, it may
        # show as "all" or under the card's school.
        schools = {e.school.lower()} if e.school else {"", card.school.lower()}
        if any(s in schools and abs(v - e.value / 100) < 0.005 for _k, s, v in effects):
            return True
    return False


def _power(card: Card) -> float:
    return sum(e.value for e in card.effects)


def _hit_schools(battle: Battle, besides: str = "") -> set[str]:
    """Schools of the damage spells in hand and still in the deck (other
    than `besides`: a card's own hit doesn't make its trap useful)."""
    out = set()
    for c in [*battle.cards, *battle.upcoming]:
        if c.is_damage and c.name != besides:
            out |= {(e.school or c.school).lower() for e in c.effects if e.kind in DAMAGE_KINDS}
    return out


def setup_fits(card: Card, battle: Battle) -> bool:
    """A blade or trap that boosts a hit we hold: any school (Feint), or the
    school of a damage spell in hand or deck. The Frost Snake pet's ice trap
    boosted nothing (no ice hits)."""
    schools = {e.school.lower() for e in card.effects if e.kind in (EffectKind.BLADE, EffectKind.TRAP)}
    if not schools or "" in schools:
        return True
    # (The pet's 'Thunder Snake Ice' hits for 80 ice itself: that hit isn't
    # one its ice trap is for.)
    return bool(schools & _hit_schools(battle, besides=card.name))


def junk_gear_hit(card: Card, battle: Battle) -> bool:
    """A gear/pet card hitting off-school whose trap/blade fits none of our
    other hits (the Frost Snake): never worth a turn."""
    school = battle.me.school.lower()
    has_setup = any(e.kind in (EffectKind.BLADE, EffectKind.TRAP) for e in card.effects)
    return (card.item and not card.treasure and card.is_damage and bool(school)
            and card.school.lower() != school and has_setup and not setup_fits(card, battle))


def _setup_action(battle: Battle, strat: Strategy, focus: Combatant | None) -> Action | None:
    """Blade self or trap an enemy, respecting stack limits. Never a copy of a
    blade/trap that is already up: before an attack it adds nothing, so a
    different buff (or the attack itself) is better."""
    castable = _castable(battle.cards)
    me = battle.me

    blades = [
        c for c in castable
        if EffectKind.BLADE in c.kinds and not c.is_enchant and setup_fits(c, battle)
        and not _is_duplicate(c, EffectKind.BLADE, me.outgoing_effects, battle.me.school.lower())
    ]
    if blades and me.blade_count < strat.max_blades:
        card = max(blades, key=_power)
        target = me if card.target in (Target.ALLY_SINGLE,) else None
        return Action(ActionKind.CAST, card, target, reason="blade up")

    if battle.live_enemies:
        target = focus or max(battle.live_enemies, key=lambda e: (e.is_boss, e.health))
        traps = [
            c for c in castable
            if EffectKind.TRAP in c.kinds and not c.is_enchant and setup_fits(c, battle)
        and not junk_gear_hit(c, battle)
            and not _is_duplicate(c, EffectKind.TRAP, target.incoming_effects, battle.me.school.lower())
        ]
        if traps and target.trap_count < strat.max_traps:
            card = max(traps, key=_power)
            t = None if card.target is Target.ENEMY_ALL else target
            return Action(ActionKind.CAST, card, t, reason=f"trap {target.name}")
    return None


def _shield_schools(card: Card) -> set[str]:
    """Schools a shield card blocks ("" = every school)."""
    return {e.school for e in card.effects if e.kind is EffectKind.SHIELD}


def _shield_fits(card: Card, enemies: list[Combatant]) -> bool:
    """A shield worth having against these enemies: it blocks every school, or
    the school of an enemy here (Ether Shield: life and death enemies)."""
    schools = _shield_schools(card)
    return "" in schools or any(e.school and e.school in schools for e in enemies)


def _shield_useless(card: Card, enemies: list[Combatant]) -> bool:
    """A school shield none of these enemies can hit through (their schools
    all known and none it blocks): dead weight in the hand."""
    schools = _shield_schools(card)
    return (
        bool(schools) and "" not in schools and bool(enemies)
        and all(e.school for e in enemies) and not any(e.school in schools for e in enemies)
    )


def _relevant_shield(battle: Battle) -> Action | None:
    """A 0-pip school shield against an enemy of that school (Ether Shield vs
    a death or life enemy), unless one blocking that school is already up."""
    me = battle.me
    for card in _castable(battle.cards):
        if card.pip_cost or card.is_enchant or card.is_damage or EffectKind.SHIELD not in card.kinds:
            continue
        schools = _shield_schools(card) - {""}
        foes = [e for e in battle.live_enemies if e.school in schools]
        if not foes:
            continue
        if any(v < 0 and (sch in schools or not sch) for _k, sch, v in me.incoming_effects):
            continue  # already shielded against them
        target = me if card.target is Target.ALLY_SINGLE else None
        why = f"shield against {foes[0].name} ({foes[0].school})"
        return Action(ActionKind.CAST, card, target, reason=why)
    return None


STUN_HEALTH = 0.7  # stun only when our health is below this
PRISM_EARLY_GAIN = 2.0  # a prism this good (7x on Meowiarty) is played first, and dug for
PRISM_GAIN = 1.25  # the converted school must hit this much harder to be worth a prism


def _is_prism(card: Card) -> bool:
    return "prism" in card.name.lower() and not card.is_damage


PRISM_SCHOOL = "myth"  # our prisms (Myth Prism): a myth hit on the target lands as storm


def prism_view(target: Combatant, src: str = PRISM_SCHOOL) -> Combatant:
    """`target` as our `src` hits see it while our prism waits on it: the hit
    lands as the opposite school, so the target's resist, traps and shields
    for that school count, and the `src` ones don't (Meowiarty's Storm Shield
    cuts the converted hit; our Myth Trap on him doesn't add). Our blades are
    ours: they still count (they boost the spell as it's cast)."""
    dst = OPPOSITE.get(src)
    if not dst:
        return target
    swap = {src: dst, dst: src}
    resist = None
    if target.resist is not None:
        resist = {**target.resist, src: target.resist.get(dst, 0.0), dst: target.resist.get(src, 0.0)}
    effects = [(k, swap.get(s, s), v) for k, s, v in target.incoming_effects]
    return dataclasses.replace(target, resist=resist, incoming_effects=effects,
                               school=swap.get(target.school, target.school), unprismed=target)


def _prism_gain(card: Card, me: Combatant, target: Combatant, hits: list[Card] = ()) -> float:
    """How much harder our best hit lands on `target` once the prism converts
    it, blades and traps included (prism_view: myth traps on it stop counting,
    storm shields start to)."""
    src = card.school.lower()
    if not OPPOSITE.get(src):
        return 1.0
    target = target.unprismed or target  # a prism on top of ours: judged on the enemy itself
    own = [c for c in hits if c.is_damage and c.school.lower() == src]
    hit = max(own, key=lambda c: c.base_damage()) if own else Card(
        0, "", school=src, effects=[Effect(EffectKind.DAMAGE, Target.ENEMY_SINGLE, 100)]
    )
    before = hit_damage(hit, me, target)
    after = hit_damage(hit, me, prism_view(target, src))
    return after / max(0.01, before)


def prism_target(battle: Battle) -> Combatant | None:
    """The boss our prisms are for (Meowiarty: storm hits him 7x harder than
    myth), while a prism is in hand or still to come; None otherwise."""
    prisms = [c for c in [*battle.cards, *battle.upcoming] if _is_prism(c)]
    if not prisms:
        return None
    best, best_gain = None, PRISM_EARLY_GAIN
    for e in battle.live_enemies:
        if not e.is_boss:
            continue
        gain = _prism_gain(prisms[0], battle.me, e, battle.cards)
        if gain >= best_gain:
            best, best_gain = e, gain
    return best


def _prism_useless(card: Card, battle: Battle) -> bool:
    """No enemy in this fight (all of them known) takes enough extra from the
    converted school: the prism will never be played here."""
    enemies = [e.unprismed or e for e in battle.live_enemies]
    if not enemies or any(e.resist is None and not e.school for e in enemies):
        return False
    return all(_prism_gain(card, battle.me, e, battle.cards) < PRISM_GAIN for e in enemies)


def _stun_action(battle: Battle, strat: Strategy | None = None) -> Action | None:
    """Stun the most dangerous enemy (a boss first) that isn't stunned, once
    our health is below STUN_HEALTH (earlier it would only delay the
    hit-all). Not when the fight is about to end."""
    enemies = [e for e in battle.live_enemies if not e.is_stunned]
    stuns = [
        c for c in _castable(battle.cards)
        if EffectKind.STUN in c.kinds and not c.is_damage and not c.treasure
    ]
    if not stuns or not enemies:
        return None
    if battle.me.health_ratio >= (strat.stun_health if strat else STUN_HEALTH):
        return None
    if _finish_in_reach(battle):
        return None
    target = max(enemies, key=lambda e: (e.is_boss, e.max_health))
    card = min(stuns, key=lambda c: c.pip_cost)
    t = None if card.target is Target.ENEMY_ALL else target
    return Action(ActionKind.CAST, card, t, reason=f"stun {target.name} (a round of no damage from it)")


PRISM_FIRST_GAIN = 1.5  # a prism doubling-ish our big hit on the target comes before it
PRISM_FIRST_PIPS = 4  # 'big hit': at least this many pips


def prism_first(battle: Battle, action: Action) -> Action:
    """A big hit about to land on an enemy that resists it (Cyrus Drake:
    myth 80%, storm -50%), not prismed yet, a prism castable in hand: the
    prism first (the hit next round lands as storm). The player: 'the deck
    needs to use prisms before big hits in order to win'."""
    card, target = action.card, action.target
    if (action.kind is not ActionKind.CAST or card is None or target is None or not card.is_damage
            or card.is_aoe or card.pip_cost < PRISM_FIRST_PIPS or target.name in battle.prismed):
        return action
    prisms = [c for c in _castable(battle.cards) if _is_prism(c) and c.school.lower() == card.school.lower()]
    if not prisms:
        return action
    gain = _prism_gain(prisms[0], battle.me, target, [card])
    if gain < PRISM_FIRST_GAIN:
        return action
    why = f"prism before {card.name}: it lands x{gain:.1f} harder on {target.name}"
    return Action(ActionKind.CAST, prisms[0], target, reason=why)


def _prism_action(battle: Battle) -> Action | None:
    """A prism (Myth Prism: myth -> storm) on an enemy our hit would land much
    harder on once converted, counting blades and traps (myth traps don't work
    on the converted hit). Not on one whose last prism our hits haven't used
    yet (battle.prismed); after that hit, again (Meowiarty takes several).
    Not when nearly dead (a Myth Prism at 7 health against Meowiarty)."""
    me = battle.me
    if me.health_ratio < DESPERATE_HEALTH:
        return None
    for card in _castable(battle.cards):
        if not _is_prism(card) or card.pip_cost:
            continue
        src = card.school.lower()
        if not OPPOSITE.get(src) or not any(c.is_damage and c.school.lower() == src for c in battle.cards):
            continue
        best, best_gain = None, PRISM_GAIN
        keep_for = prism_target(battle)  # prisms are few: all of them for that boss
        for t in battle.live_enemies:
            if t.name in battle.prismed or (keep_for is not None and t.name != keep_for.name):
                continue
            gain = _prism_gain(card, me, t, battle.cards)
            if gain >= best_gain and t.health > 150:
                best, best_gain = t, gain
        if best:
            why = f"{OPPOSITE[src]} hits {best.name} x{best_gain:.2f} harder than {src} (buffs counted)"
            return Action(ActionKind.CAST, card, best, reason=why)
    return None

def _shield_action(battle: Battle, strat: Strategy) -> Action | None:
    me = battle.me
    if me.health_ratio >= strat.shield_threshold or me.shield_count >= 2:
        return None
    shields = [
        c for c in _castable(battle.cards)
        if EffectKind.SHIELD in c.kinds and not c.is_enchant and not c.is_damage
        and _shield_fits(c, battle.live_enemies)
    ]
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
        if c.is_heal and me.health_ratio > 0.9 and not any(e.is_boss for e in battle.live_enemies):
            score += 20  # (a boss can take most of our health in one hit: heals stay)
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


@dataclass
class FightPlan:
    rounds: int  # estimated rounds of our casts to end the fight
    text: str  # human-readable plan, logged by the fighter
    skip_summon: bool


PLAN_ROUNDS = 6  # how far the fight estimate looks (with the deck's remaining cards)


def _rounds_to_kill(battle: Battle, target: Combatant) -> tuple[int, str]:
    """Fewest rounds to kill `target`: the cards in hand, then one card a
    round from what's left of the deck (best first: the estimate when the
    draws go well), a pip a round. 99 when nothing kills within PLAN_ROUNDS."""
    draws = sorted(battle.upcoming, key=lambda c: -c.base_damage())
    found = _kill_search(battle, target, PLAN_ROUNDS, draws)
    if found is None:
        return 99, f"no kill within {PLAN_ROUNDS} rounds with the cards left"
    n, _spent, _action, steps, _used = found
    return n, " > ".join(steps)


def _plan_needs(battle: Battle, card: Card) -> bool:
    """Would the fight take longer without `card` (discarded now)?"""
    without = replace(battle, cards=[c for c in battle.cards if c is not card])
    return any(_rounds_to_kill(without, e)[0] > _rounds_to_kill(battle, e)[0] for e in battle.live_enemies)


def plan_fight(battle: Battle, strat: Strategy | None = None) -> FightPlan:
    """How we expect to end this fight, weakest enemy first. A quick fight
    (no boss, a few rounds) isn't worth a round summoning a minion."""
    strat = strat or Strategy()
    enemies = sorted(battle.live_enemies, key=lambda e: e.health)
    if not enemies:
        return FightPlan(0, "no enemies", True)
    # One enemy after the other: the next plan starts with the pips and cards
    # the one before left (both Nappers "Cyclops = 1 round" with 3 pips was
    # one Cyclops, not two).
    parts, total, state = [], 0, battle
    for e in enemies:
        draws = sorted(state.upcoming, key=lambda c: -c.base_damage())
        found = _kill_search(state, e, PLAN_ROUNDS, draws)
        if found is None:
            n, how = 99, f"no kill within {PLAN_ROUNDS} rounds with the cards left"
        else:
            n, spent, _action, steps, used = found
            how = " > ".join(steps)
            left = max(0, state.pips + 2 * state.power_pips + n - spent)
            cards = [c for c in state.cards if c.index not in used] + draws[:n]
            state = replace(state, pips=left, power_pips=0, cards=cards, upcoming=draws[n:])
        total += n
        parts.append(f"{e.name} {e.health}hp: {how} = {n} round{'s' if n != 1 else ''}")
    boss = any(e.is_boss for e in enemies)
    # A quick fight, or a boss fight that's nearly over: a minion (X-pip, all
    # our pips) would arrive too late to matter.
    skip = total <= (2 if boss else strat.quick_fight_rounds)
    note = "; skipping the minion (quick fight)" if skip else ""
    return FightPlan(total, f"plan (~{total} rounds): " + " | ".join(parts) + note, skip)


FREE_TRAP_LIMIT = 4  # traps worth stacking for free while waiting (one is used per hit)


def _free_setup(battle: Battle, strat: Strategy) -> Action | None:
    """A 0-pip blade or trap: casting it spends no pips, so it never gets in the
    way of what we're saving for, and the next hits land harder. A new effect
    first; a copy of one already up only as a last resort (it waits for the hit
    after)."""
    free = [c for c in _castable(battle.cards)
            if c.pip_cost == 0 and not c.is_enchant and setup_fits(c, battle)]
    me = battle.me
    focus = max(battle.live_enemies, key=lambda e: (e.is_boss, e.health)) if battle.live_enemies else None
    aoe = _group_aoe(battle)
    if aoe is not None:
        focus = _aoe_trap_target(battle, aoe)  # spread traps for the hit-all spell
    options: list[tuple[bool, int, Action]] = []
    if me.blade_count < max(strat.max_blades, FREE_TRAP_LIMIT):
        for c in free:
            if EffectKind.BLADE in c.kinds:
                dup = _is_duplicate(c, EffectKind.BLADE, me.outgoing_effects, battle.me.school.lower())
                target = me if c.target in (Target.ALLY_SINGLE,) else None
                action = Action(ActionKind.CAST, c, target, reason="free blade while saving pips")
                options.append((dup, 0, action))
    if focus and focus.trap_count < max(strat.max_traps, FREE_TRAP_LIMIT):
        for c in free:
            if EffectKind.TRAP in c.kinds:
                dup = _is_duplicate(c, EffectKind.TRAP, focus.incoming_effects, battle.me.school.lower())
                t = None if c.target is Target.ENEMY_ALL else focus
                why = f"free trap on {focus.name} while saving pips"
                options.append((dup, 1, Action(ActionKind.CAST, c, t, reason=why)))
    if not options:
        return None
    # New effects before copies; blades before traps; the strongest card.
    dup, _, action = min(options, key=lambda o: (o[0], o[1], -_power(o[2].card)))
    if dup:
        action.reason += " (a copy: kept for the hit after)"
    return action


AOE_SPILL_PER_PIP = 150.0  # damage to the other enemies worth one pip when comparing kill lines
KILL_LOOKAHEAD = 4  # rounds the kill search looks ahead (blade, trap, a pip, Cyclops)


def _use_up(effects: list[tuple[str, str, float]], school: str) -> list[tuple[str, str, float]]:
    """Effects left after a hit of `school`: one copy of each distinct
    matching trap/blade (or shield) is spent."""
    out, spent = [], set()
    for key, eff_school, value in effects:
        if key not in spent and (not eff_school or eff_school == school):
            spent.add(key)
            continue
        out.append((key, eff_school, value))
    return out


def _pay(card: Card, school: str, normal: int, power: int) -> tuple[int, int] | None:
    """Pips left after casting `card` (None if it can't be paid). A power pip
    is worth 2 for spells of our own school, 1 for others."""
    cost = card.pip_cost
    if card.school.lower() == school:
        while cost >= 2 and power:
            cost, power = cost - 2, power - 1
        if cost == 1 and normal == 0 and power:
            cost, power = 0, power - 1
        return (normal - cost, power) if normal >= cost else None
    take = min(cost, normal)
    cost, normal = cost - take, normal - take
    return (normal, power - cost) if power >= cost else None


def _kill_search(battle: Battle, target: Combatant, rounds: int, draws: list[Card] = ()):
    """The quickest line that kills `target` from here: cards in hand, plus
    (with `draws`) one card from the rest of the deck arriving each later
    round. Each round: cast an attack, a 0-pip blade or trap, or pass; one pip
    comes per round (power pips are worth 2 for our school's spells; a new
    one is used when it comes, as the plan is redone every round); every card
    is used once; blades and traps are spent by the hits they boost. Fewest
    rounds, then fewest pips. Returns (rounds, pips, first action, steps, used)
    or None if nothing kills within `rounds`."""
    me = battle.me
    my_school = (me.school or "").lower()
    pool = [c for c in battle.cards if not c.is_enchant] + list(draws)
    in_hand = len(pool) - len(draws)
    best: list = []

    def arrived(i: int, depth: int) -> bool:
        return i < in_hand or depth >= i - in_hand + 1

    def search(depth, normal, power, used, out_fx, in_fx, hp, spent, first, steps):
        if depth >= rounds or (best and (depth + 1, spent) > best[0][:2] and depth + 1 >= best[0][0]):
            return
        seen = set()
        for i, c in enumerate(pool):
            if i in used or not arrived(i, depth) or (depth == 0 and i < in_hand and not c.castable):
                continue
            if c.is_damage:
                kind = "hit"
            elif c.pip_cost == 0 and EffectKind.TRAP in c.kinds:
                kind = "trap"
            elif c.pip_cost == 0 and EffectKind.BLADE in c.kinds:
                kind = "blade"
            else:
                continue
            if (kind, c.name) in seen:
                continue  # copies play the same
            paid = _pay(c, my_school, normal, power)
            if paid is None:
                continue
            seen.add((kind, c.name))
            school = c.school.lower()
            act = first
            if kind == "hit":
                if act is None:
                    act = Action(ActionKind.CAST, c, None if c.is_aoe else target, reason="")
                attacker = Combatant(**{**me.__dict__, "outgoing_effects": out_fx})
                victim = Combatant(**{**target.__dict__, "incoming_effects": in_fx, "health": hp})
                dmg = hit_damage(c, attacker, victim)
                step = f"{c.name} (~{dmg:.0f})"
                if dmg >= hp:
                    # The shield it gives (Ether Golem) only helps against enemies
                    # still standing after this killing hit.
                    others = [e for e in battle.live_enemies if e is not target]
                    cost = spent + c.pip_cost - _shield_bonus(c, others)
                    if c.is_aoe and others:
                        # The same kill with a hit-all spell also hurts the rest
                        # (Humongofrog on Kettlehead hits Firegut too): count its
                        # damage to them as pips saved.
                        spill = sum(min(hit_damage(c, attacker, o), o.health) for o in others)
                        cost -= spill / AOE_SPILL_PER_PIP
                    key = (depth + 1, cost)
                    if not best or key < best[0][:2]:
                        best[:] = [(depth + 1, cost), act, steps + [step], used | {i}, dmg]
                    continue
                breaks = any(v < 0 and sch in ("", school) for _k, sch, v in in_fx)
                if c.pip_cost == 0 and not breaks and (
                    _use_up(out_fx, school) != out_fx or _use_up(in_fx, school) != in_fx
                ):
                    continue  # a free chip hit would spend our blade/traps (Super Strike): only as the kill
                search(depth + 1, paid[0] + 1, paid[1], used | {i}, _use_up(out_fx, school),
                       _use_up(in_fx, school), hp - dmg, spent + c.pip_cost, act, steps + [step])
            else:
                if act is None:
                    t = None if (kind == "blade" and c.target is not Target.ENEMY_SINGLE) else target
                    if kind == "blade" and c.target is Target.ALLY_SINGLE:
                        t = me
                    act = Action(ActionKind.CAST, c, t, reason="")
                value = sum(e.value for e in c.effects if e.kind in (EffectKind.TRAP, EffectKind.BLADE)) / 100
                existing = out_fx if kind == "blade" else in_fx
                same = next((k for k, sch, v in existing if sch == school and abs(v - value) < 0.005), None)
                fx = (same or f"plan:{c.name}", school, value)  # a copy of one that's up doesn't stack
                new_out = out_fx + [fx] if kind == "blade" else out_fx
                new_in = in_fx + [fx] if kind == "trap" else in_fx
                search(depth + 1, paid[0] + 1, paid[1], used | {i}, new_out, new_in, hp, spent, act,
                       steps + [c.name])
        # pass: keep the pips
        search(depth + 1, normal + 1, power, used, out_fx, in_fx, hp, spent,
               first or Action(ActionKind.PASS, reason=""), steps + ["pass"])

    search(0, battle.pips, battle.power_pips, frozenset(), list(me.outgoing_effects),
           list(target.incoming_effects), target.health, 0, None, [])
    if not best:
        return None
    (n, spent), action, steps, used, _dmg = best
    return n, spent, action, steps, {pool[i].index for i in used if i < in_hand}


def fastest_kill(battle: Battle, target: Combatant, rounds: int = KILL_LOOKAHEAD) -> Action | None:
    """The first move of the quickest line that kills `target` with the cards
    in hand (draws aren't counted on: they may not come). None if none kills
    within `rounds`."""
    found = _kill_search(battle, target, rounds)
    if found is None:
        return None
    n, _spent, action, steps, used = found
    why = f"kills {target.name} in {n} round(s): {' > '.join(steps)}"
    action.reason = why if action.kind is not ActionKind.PASS else f"waiting: {why}"
    action.plan_cards = used
    return action


def _shielded_for(card: Card, target: Combatant) -> bool:
    """A shield on `target` would cut this hit."""
    school = card.school.lower()
    return any(v < 0 and sch in ("", school) for _k, sch, v in target.incoming_effects)


def _shield_bonus(card: Card, enemies: list[Combatant]) -> float:
    """An attack that also shields us (Ether Golem: life and death) against
    enemies of those schools: worth about a pip and a half in the plan."""
    schools = {e.school for e in card.effects if e.kind is EffectKind.SHIELD}
    return 1.5 if card.is_damage and any(e.school in schools for e in enemies if e.school) else 0.0


WEAK_HIT_SHARE = 0.5  # a hit under this share of the target's health doesn't deserve our blade/trap
CHIP_HIT_SHARE = 1 / 3  # without buffs: a hit under this share is only worth it to finish


def _buffed_for(battle: Battle, card: Card, target: Combatant) -> bool:
    """Would this hit use up a blade of ours or a trap on the target?"""
    school = card.school.lower()
    return bool(
        _matching(battle.me.outgoing_effects, school, shields=False)
        or _matching(target.incoming_effects, school, shields=False)
    )


FREE_HIT_SPARE_TRAPS = 99  # never: a hit spends several traps at once (Crush took two off Kenedy the Klaw)


def _matching(effects: list[tuple[str, str, float]], school: str, shields: bool) -> list:
    """Hanging traps/blades (or shields/weaknesses) that a `school` hit would use up."""
    return [e for e in effects if e[1] in ("", school) and (e[2] < 0 if shields else e[2] > 0)]


def _free_hit(battle: Battle, shields_only: bool = False) -> Action | None:
    """A 0-pip hit (a wand's Super Strike) instead of passing. Every hit uses
    up one matching trap on the target and one blade of ours, so: break a
    shield first (the real hit then lands in full), else chip an enemy that has
    no trap to lose, else one with traps to spare. Never while a blade of
    ours would be spent on it."""
    hits = [c for c in _castable(battle.cards) if c.pip_cost == 0 and c.is_damage and not c.treasure]
    if not hits:
        return None
    card = max(hits, key=lambda c: c.base_damage())
    school = card.school.lower()
    me = battle.me
    enemies = battle.live_enemies
    bladed = me.outgoing_boost > 0 and not me.outgoing_effects
    if bladed or _matching(me.outgoing_effects, school, shields=False):
        # Our blade would go on it: only worth it to knock off a shield that
        # cuts more than the blade adds (a Tower Shield's -50% vs a +35% blade).
        blade = sum(v for _k, sch, v in me.outgoing_effects if v > 0 and sch in ("", school))
        worth = [
            e for e in enemies
            if -sum(v for _k, sch, v in e.incoming_effects if v < 0 and sch in ("", school)) > blade
        ]
        if not worth:
            return None
        t = max(worth, key=lambda e: (e.is_boss, e.health))
        return Action(ActionKind.CAST, card, t, reason=f"0 pips: breaking {t.name}'s shield early")

    def shielded(e: Combatant) -> bool:
        return bool(_matching(e.incoming_effects, school, shields=True)) or (
            e.incoming_boost < 0 and not e.incoming_effects
        )

    def trapped(e: Combatant) -> bool:
        return bool(_matching(e.incoming_effects, school, shields=False)) or (
            e.incoming_boost > 0 and not e.incoming_effects
        )

    def spares(e: Combatant) -> bool:
        return e.trap_count >= FREE_HIT_SPARE_TRAPS

    if card.is_aoe:
        harmless = all(shielded(e) or not trapped(e) or spares(e) for e in enemies)
        if harmless and any(shielded(e) for e in enemies):
            return Action(ActionKind.CAST, card, None, reason="0 pips: breaking shields")
        if shields_only or not all(not trapped(e) or spares(e) for e in enemies):
            return None
        return Action(ActionKind.CAST, card, None, reason="0 pips instead of passing")
    with_shield = [e for e in enemies if shielded(e)]
    if with_shield:
        t = max(with_shield, key=lambda e: (e.is_boss, e.health))
        return Action(ActionKind.CAST, card, t, reason=f"0 pips: breaking {t.name}'s shield early")
    if shields_only:
        return None
    clean = [e for e in enemies if not trapped(e)]
    if clean:
        t = min(clean, key=lambda e: e.health)
        return Action(ActionKind.CAST, card, t, reason="0 pips instead of passing (no trap on it to waste)")
    stacked = [e for e in enemies if spares(e)]
    if stacked:
        t = max(stacked, key=lambda e: e.trap_count)
        why = f"0 pips instead of passing ({t.trap_count} traps: one to spare)"
        return Action(ActionKind.CAST, card, t, reason=why)
    return None


def _break_shield(battle: Battle) -> Action | None:
    """Before setting up a trap or blade: a 0-pip hit that removes an enemy's
    shield, so the trap and the real hit aren't wasted against it; or a prism
    that turns our hits to the school the target is weak to."""
    return _free_hit(battle, shields_only=True) or _prism_action(battle)


DAMAGE_RESERVE = 1.5  # damage left in hand + deck must cover the enemies' health this many times
MIN_DECK_LEFT = 5  # below this many cards still to draw, nothing is discarded


def _damage_left(battle: Battle, without: Card | None = None) -> float:
    """Expected damage of every attack card still ours this fight (hand and
    deck), on the toughest enemy; hit-all spells count once per enemy."""
    enemies = battle.live_enemies
    if not enemies:
        return 0.0
    focus = max(enemies, key=lambda e: e.health)
    total = 0.0
    for c in [*battle.cards, *battle.upcoming]:
        if c is without or not c.is_damage:
            continue
        hits = len(enemies) if c.is_aoe else 1
        total += expected_damage(c, battle.me, focus) * hits
    return total


def can_spare(battle: Battle, card: Card) -> bool:
    """Discarding `card` still leaves enough to win: the deck isn't nearly
    empty, and (for an attack) the damage left covers the enemies' health
    with room to spare. Against Shakes O'Leary 8 discards (Troll, two
    Cyclops...) left nothing to cast from round 18 on."""
    if not battle.deck_known:
        return True  # nothing to judge by
    if len(battle.upcoming) < MIN_DECK_LEFT:
        return False
    if not card.is_damage:
        return True
    need = sum(e.health for e in battle.live_enemies) * DAMAGE_RESERVE
    return _damage_left(battle, without=card) >= need


def out_of_attacks(battle: Battle) -> bool:
    """No attack card in hand or still to draw, enemies still up: the fight
    can't be won from here."""
    return bool(battle.live_enemies) and not any(c.is_damage for c in [*battle.cards, *battle.upcoming])


def decide(battle: Battle, strat: Strategy | None = None, *, discards_left: int = 2) -> Action:
    """The action for this step, with enemies holding our unused prism seen
    as our hits will find them (prism_view): the big myth hit is worth its
    storm damage on Meowiarty. The target maps back to the real enemy."""
    # A pet/gear hit off our school whose trap fits nothing (the Frost Snake):
    # never cast (the discard rule still bins it).
    battle.cards = [replace(c, castable=False) if c.castable and junk_gear_hit(c, battle) else c
                    for c in battle.cards]
    if not battle.prismed:
        return prism_first(battle, _decide_step(battle, strat, discards_left=discards_left))
    real = list(battle.enemies)
    seen = [prism_view(e) if e.name in battle.prismed and not e.is_dead else e for e in real]
    action = _decide_step(replace(battle, enemies=seen), strat, discards_left=discards_left)
    for r, v in zip(real, seen, strict=True):
        if action.target is v:
            action = replace(action, target=r)
            break
    return action


def _decide_step(battle: Battle, strat: Strategy | None = None, *, discards_left: int = 2) -> Action:
    """The action for this step. Instead of a pass (saving pips, holding a hit)
    a 0-pip card is played, since it costs nothing we're saving: a hit that
    breaks an enemy's shield, else a blade or trap, else a harmless hit."""
    strat = strat or Strategy()
    action = _decide(battle, strat, discards_left=discards_left)
    wanted_prism = (
        action.kind is ActionKind.DISCARD and action.card is not None
        and _is_prism(action.card) and not _prism_useless(action.card, battle)
    )
    if wanted_prism or (
        action.kind is ActionKind.DISCARD and action.card is not None and not can_spare(battle, action.card)
    ):
        # Keep the cards the fight will need: decide again without discarding.
        action = _decide(battle, strat, discards_left=0)
    if action.kind is ActionKind.PASS and battle.live_enemies:
        free = (
            _free_hit(battle, shields_only=True)
            or _relevant_shield(battle)
            or _prism_action(battle)
            or _free_setup(battle, strat)
            or _free_hit(battle)
        )
        if free:
            return free
    return action


def _decide(battle: Battle, strat: Strategy, *, discards_left: int = 2) -> Action:

    if not battle.live_enemies:
        return Action(ActionKind.PASS, reason="no enemies")

    # Finishing the last enemy ends the fight, unless we're low enough to die
    # before our spell lands (the enemy may act first, and spells can fizzle):
    # then heal first and kill next round.
    kill = _kill_action(battle)
    heal = _best_heal(battle, strat) or _drain_heal(battle, strat)
    if kill and _kills_all(battle, kill):
        return kill  # ending the fight beats any heal, however low we are

    if heal:
        # Removing an attacker beats a 2-pip heal unless we're about to fall.
        if kill and battle.me.health_ratio >= strat.partial_kill_health:
            return kill
        return heal
    save = _save_for_heal(battle, strat)
    if save:
        return save

    # With several enemies, removing one means one fewer attacker every round;
    # but with the hit-all spell in hand or deck, 2+ pips on one kill leave
    # nothing for it (a Cyclops on one Napper, then nothing for the other).
    if kill and not _saving_for_aoe(battle, kill):
        return kill

    if discards_left > 0:
        junk = _junk_discard(battle, strat)
        if junk:
            return junk

    # A boss fight is long: the minion comes out first (the player's order:
    # minion, then Feint/blades/traps, then the big hits).
    if (
        any(e.is_boss for e in battle.live_enemies) and battle.round <= BOSS_SUMMON_ROUNDS
        and not (plan_fight(battle, strat).skip_summon or _finish_in_reach(battle))
    ):
        summon = _summon_action(battle, strat)
        if summon:
            return summon

    # A long fight that's hurting us: a Stun on the hardest hitter buys a
    # round of no damage from it (two Stuns sat unused against Meowiarty).
    stun = _stun_action(battle, strat)
    if stun:
        return stun

    # An enemy that shrugs off our school (Meowiarty resists myth 80%, takes
    # 50% more from storm): the prism comes first, before any hit on it.
    prism = _prism_action(battle)
    if prism and _prism_gain(prism.card, battle.me, prism.target, battle.cards) >= strat.prism_early_gain:
        return prism

    # Blades and traps (and the hit-all spell against groups) matter more than
    # another single hit: dig for them.
    if discards_left > 0:
        dig = _dig_for_setup(battle, strat)
        if dig:
            return dig

    # A group: the minion first (turn 1), then blades/traps, then the hit-all
    # spell (it came only in round 10 against the Klaw brothers).
    if len(battle.live_enemies) >= AOE_MIN_ENEMIES and battle.round <= GROUP_SUMMON_ROUNDS:
        summon = _summon_action(battle, strat)
        if summon:
            return summon

    # Several enemies: buff up and clear them all with one hit-all spell.
    aoe = _aoe_plan(battle, strat)
    if aoe:
        return aoe

    # One enemy left: play the line that kills it in the fewest rounds (then
    # the fewest pips). Troll with blade and traps doing 500 into 400 hp kills
    # now; waiting for a Cyclops only when that really is faster.
    if len(battle.live_enemies) == 1:
        line = fastest_kill(battle, battle.live_enemies[0])
        if line is not None:
            if line.kind is ActionKind.PASS and discards_left > 0:
                # Waiting on the line: bin chip hits it doesn't use, for new draws.
                chips = [
                    c for c in battle.cards
                    if c.is_damage and not c.treasure and c.pip_cost == 1
                    and c.index not in getattr(line, "plan_cards", set())
                ]
                if chips:
                    junk = min(chips, key=lambda c: c.base_damage())
                    return Action(ActionKind.DISCARD, junk, reason="not part of the killing line; drawing")
                dead = _discard_action(battle, strat)  # a full hand of dead cards blocks draws
                if dead and dead.card and dead.card.index not in getattr(line, "plan_cards", set()):
                    return dead
            return line

    skip_summon = plan_fight(battle, strat).skip_summon or _finish_in_reach(battle)
    summon = None if skip_summon else _summon_action(battle, strat)
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
            setup = _break_shield(battle) or _setup_action(battle, strat, focus)
            if setup:
                return setup

        # A 2+ pip hit is worth most on a trapped target: wait for a trap (ours or
        # the minion's) unless pips are piling up. Only on a bare target, and only
        # when a trap can actually come: waiting never removes shields.
        if (
            card.pip_cost >= 2
            and dmg < focus.health
            and not focus.incoming_effects
            and focus.trap_count == 0
            and _trap_coming(battle)
            and battle.pips + battle.power_pips < strat.hold_big_hit_until_pips
        ):
            return Action(ActionKind.PASS, reason=f"holding {card.name} until {focus.name} is trapped")

        # Blades and traps are spent by the next hit of their school. A weak hit
        # (Blood Bat: ~136 into a 435 hp Sand Stalker) wastes them: bin weak
        # cards to draw a Troll or Cyclops, or wait, until pips pile up.
        # Even unbuffed, a chip hit (under a third of the target's health) is
        # worth less than the Troll/Cyclops a few discards may bring.
        buffed = _buffed_for(battle, card, focus)
        if (
            card.pip_cost == 1  # chip hits (Blood Bat); Troll and Cyclops are the real hits
            and dmg < focus.health * (WEAK_HIT_SHARE if buffed else CHIP_HIT_SHARE)
            and battle.pips + battle.power_pips < strat.hold_big_hit_until_pips + 1
        ):
            share = WEAK_HIT_SHARE if buffed else CHIP_HIT_SHARE
            weak = [
                c for c in battle.cards
                if c.is_damage and not c.treasure and c.pip_cost == 1
                and expected_damage(c, battle.me, focus) < focus.health * share
            ]
            if discards_left > 0 and weak:
                junk = min(weak, key=lambda c: c.base_damage())
                why = (f"too weak to spend the blade/trap on {focus.name}" if buffed
                       else f"only chips {focus.name}") + "; drawing for a bigger hit"
                return Action(ActionKind.DISCARD, junk, reason=why)
            what = "keeping the blade/trap" if buffed else "waiting"
            return Action(ActionKind.PASS, reason=f"{what} for a bigger hit than {card.name}")

        stronger = _stronger_next_round(battle, focus, dmg, strat)
        if stronger:
            # shield breaks, traps and blades while we wait
            setup = _break_shield(battle) or _setup_action(battle, strat, focus)
            if setup:
                return setup
            return Action(ActionKind.PASS, reason=f"saving pips for {stronger.name} (~{dmg:.0f} now)")

        # A shield on the target (a Tower Shield: -50%) eats our big hit: knock
        # it off with a free hit first unless this hit kills anyway.
        if card.pip_cost > 0 and dmg < focus.health and _shielded_for(card, focus):
            breaker = _free_hit(battle, shields_only=True)
            if breaker is not None:
                return breaker

        enchant = _pick_enchant(battle, card)
        if enchant:
            return Action(ActionKind.ENCHANT, enchant, target_card=card, reason="boost attack")
        return Action(ActionKind.CAST, card, target, reason=f"~{dmg:.0f} dmg")

    setup = _break_shield(battle) or _setup_action(battle, strat, None)
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
