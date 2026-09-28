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

from dataclasses import dataclass

from ..deck_plan import minion_rank
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
    boss_heal_threshold: float = 0.6  # bosses can take half our health in one round
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
    focus_bonus: float = 60.0  # value of taking a target's whole remaining health
    # Pass a round for an attack one pip away when the best castable one does
    # less than this share of its damage (two 1-pip hits < one 2-pip Troll).
    save_for_stronger: float = 0.6
    discard_junk: bool = True  # bin off-school gear attack cards to draw deck spells
    hold_big_hit_until_pips: int = 4  # wait for a trap before a 2+ pip hit, up to this many pips
    quick_fight_rounds: int = 3  # no boss and done within this many rounds: don't summon


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


def hit_damage(card: Card, attacker: Combatant, target: Combatant) -> float:
    """Damage if the spell lands: base damage with blades/weaknesses on the
    attacker, traps/shields on the target, and school resist/bonus."""
    blade = effect_multiplier(attacker.outgoing_effects, attacker.outgoing_boost, card.school)
    trap = effect_multiplier(target.incoming_effects, target.incoming_boost, card.school)
    mult = blade * trap
    mult *= school_multiplier(card, attacker, target)
    return max(0.0, card.base_damage() * mult)


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


def _has_minion(battle: Battle) -> bool:
    return any(a.is_minion and not a.is_dead and a.health > 0 for a in battle.allies)


def _summon_action(battle: Battle, strat: Strategy) -> Action | None:
    if not strat.summon_minions or _has_minion(battle):
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
            key = (len(kills), -card.pip_cost, -min(t.health for t in kills))
            if best is None or key > best[0]:
                name = kills[0].name if len(kills) == 1 else f"{len(kills)} enemies"
                dmg = hit_damage(card, battle.me, kills[0])
                best = (key, Action(ActionKind.CAST, card, target, reason=f"finish {name}: ~{dmg:.0f} dmg"))
    return best[1] if best else None


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
    if junk:
        card = min(junk, key=lambda c: c.base_damage())
        return Action(ActionKind.DISCARD, card, reason="off-school gear card; making room for deck spells")
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


@dataclass
class FightPlan:
    rounds: int  # estimated rounds of our casts to end the fight
    text: str  # human-readable plan, logged by the fighter
    skip_summon: bool


def _rounds_to_kill(battle: Battle, target: Combatant) -> tuple[int, str]:
    """Fewest of our turns to kill `target` with the attacks in hand: now, trap
    then hit, or repeated best hits (waiting a round for pips when needed)."""
    me, pips = battle.me, battle.pips + battle.power_pips
    attacks = [c for c in battle.cards if c.is_damage and not c.is_enchant and not c.treasure]
    if not attacks:
        return 99, "no attack in hand"
    now = [c for c in attacks if c.castable]
    for c in sorted(now, key=lambda c: c.pip_cost):
        if hit_damage(c, me, target) >= target.health:
            why = damage_breakdown(me, target, c)
            return 1, f"{c.name} now (~{hit_damage(c, me, target):.0f}: {why})"
    traps = [c for c in battle.cards if c.castable and EffectKind.TRAP in c.kinds and not c.is_enchant]
    if traps and not _is_trapped(target):
        trap = max(traps, key=lambda c: sum(e.value for e in c.effects))
        boost = sum(e.value for e in trap.effects if e.kind is EffectKind.TRAP) / 100
        planned = (f"planned:{trap.name}", trap.school.lower(), boost)
        effects = target.incoming_effects
        if not effects and target.incoming_boost:
            effects = [("existing", "", target.incoming_boost)]
        trapped = Combatant(**{**target.__dict__, "incoming_effects": [*effects, planned]})
        for c in sorted(attacks, key=lambda c: c.pip_cost):
            if c.pip_cost <= pips + 1 and hit_damage(c, me, trapped) >= target.health:
                return 2, f"{trap.name}, then {c.name} (~{hit_damage(c, me, trapped):.0f})"
    usable = [c for c in attacks if c.pip_cost <= pips + 1]
    if not usable:
        return 99, "no affordable attack"
    best = max(usable, key=lambda c: expected_damage(c, me, target))
    per_round = max(1.0, expected_damage(best, me, target))
    wait = 0 if best.castable else 1
    rounds = wait + -(-target.health // int(per_round))
    return rounds, f"{best.name} x{rounds - wait} (~{per_round:.0f} each)"


def plan_fight(battle: Battle, strat: Strategy | None = None) -> FightPlan:
    """How we expect to end this fight, weakest enemy first. A quick fight
    (no boss, a few rounds) isn't worth a round summoning a minion."""
    strat = strat or Strategy()
    enemies = sorted(battle.live_enemies, key=lambda e: e.health)
    if not enemies:
        return FightPlan(0, "no enemies", True)
    parts, total = [], 0
    for e in enemies:
        n, how = _rounds_to_kill(battle, e)
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
    way of what we're saving for, and the next hits land harder."""
    free = [c for c in _castable(battle.cards) if c.pip_cost == 0 and not c.is_enchant]
    me = battle.me
    blades = [c for c in free if EffectKind.BLADE in c.kinds]
    if blades and me.blade_count < max(strat.max_blades, FREE_TRAP_LIMIT):
        card = max(blades, key=lambda c: sum(e.value for e in c.effects))
        target = me if card.target in (Target.ALLY_SINGLE,) else None
        return Action(ActionKind.CAST, card, target, reason="free blade while saving pips")
    traps = [c for c in free if EffectKind.TRAP in c.kinds]
    if traps and battle.live_enemies:
        focus = max(battle.live_enemies, key=lambda e: (e.is_boss, e.health))
        if focus.trap_count < max(strat.max_traps, FREE_TRAP_LIMIT):
            card = max(traps, key=lambda c: sum(e.value for e in c.effects))
            t = None if card.target is Target.ENEMY_ALL else focus
            return Action(ActionKind.CAST, card, t, reason=f"free trap on {focus.name} while saving pips")
    return None


FREE_HIT_SPARE_TRAPS = 3  # an enemy with this many traps can lose one to a 0-pip hit


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
    bladed = me.outgoing_boost > 0 and not me.outgoing_effects
    if bladed or _matching(me.outgoing_effects, school, shields=False):
        return None
    enemies = battle.live_enemies

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
    shield, so the trap and the real hit aren't wasted against it."""
    return _free_hit(battle, shields_only=True)


def decide(battle: Battle, strat: Strategy | None = None, *, discards_left: int = 2) -> Action:
    """The action for this step. Instead of a pass (saving pips, holding a hit)
    a 0-pip card is played, since it costs nothing we're saving: a hit that
    breaks an enemy's shield, else a blade or trap, else a harmless hit."""
    strat = strat or Strategy()
    action = _decide(battle, strat, discards_left=discards_left)
    if action.kind is ActionKind.PASS and battle.live_enemies:
        free = _free_hit(battle, shields_only=True) or _free_setup(battle, strat) or _free_hit(battle)
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
    heal = _best_heal(battle, strat)
    if kill and len(battle.live_enemies) == 1:
        if heal and battle.me.health_ratio < heal_threshold(battle, strat):
            return heal
        return kill

    if heal:
        return heal
    save = _save_for_heal(battle, strat)
    if save:
        return save

    # With several enemies, removing one means one fewer attacker every round.
    if kill:
        return kill

    if discards_left > 0:
        junk = _junk_discard(battle, strat)
        if junk:
            return junk

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

        stronger = _stronger_next_round(battle, focus, dmg, strat)
        if stronger:
            # shield breaks, traps and blades while we wait
            setup = _break_shield(battle) or _setup_action(battle, strat, focus)
            if setup:
                return setup
            return Action(ActionKind.PASS, reason=f"saving pips for {stronger.name} (~{dmg:.0f} now)")

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
