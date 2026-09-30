"""Monte Carlo fights: the bot's own brain (`decide`) against a model of an
encounter, to compare decks and strategies before trying them for real.

The model is calibrated from logged fights (Meowiarty in Big Ben): each enemy
attacks about half the rounds for its typical damage, our hits use
`hit_damage` (blades, traps, school resist), blades and traps are used up by
the next hit of their school, a prism converts the next single-target hit of
its school on its target, a minion hits a random enemy and soaks some attacks,
and spells fizzle now and then. Rough, but it ranks strategies.

    python -m wiz101_auto.combat.sim            # Meowiarty: the decks compared
"""

from __future__ import annotations

import dataclasses
import random
from dataclasses import dataclass, field

from .brain import Strategy, _is_prism, _pay, decide, hit_damage
from .model import ActionKind, Battle, Card, Combatant, Effect, EffectKind, Target

HAND = 7
ACCURACY = 0.85
POWER_PIP_CHANCE = 0.35
MAX_PIPS = 7
MAX_ROUNDS = 40


def card(name, school, pips, *effects, target=Target.ENEMY_SINGLE, item=False) -> Card:
    return Card(0, name, school=school, pip_cost=pips, effects=list(effects), item=item)


def hit(value, target=Target.ENEMY_SINGLE, kind=EffectKind.DAMAGE):
    return Effect(kind, target, value)


# The cards (values from the deck reads and the fights).
CARDS = {
    "Pixie": lambda: card("Pixie", "life", 2, Effect(EffectKind.HEAL, Target.SELF, 420)),
    "Cyclops": lambda: card("Cyclops", "myth", 3, hit(295)),
    "Troll": lambda: card("Troll", "myth", 2, hit(190)),
    "Ether Shield": lambda: card("Ether Shield", "myth", 0, *(
        Effect(EffectKind.SHIELD, Target.SELF, -70, school=s) for s in ("life", "death"))),
    "Minotaur": lambda: card("Minotaur", "myth", 4, hit(380)),
    "Humongofrog": lambda: card("Humongofrog", "myth", 4, hit(295, Target.ENEMY_ALL)),
    "Troll Minion": lambda: card("Troll Minion", "myth", 2, Effect(EffectKind.SUMMON, Target.SELF, 0)),
    "Myth Prism": lambda: card("Myth Prism", "myth", 0, Effect(EffectKind.OTHER, Target.ENEMY_SINGLE, 0)),
    "Myth Trap": lambda: card("Myth Trap", "myth", 0,
                              Effect(EffectKind.TRAP, Target.ENEMY_SINGLE, 40, school="myth")),
    "Mythblade": lambda: card("Mythblade", "myth", 0,
                              Effect(EffectKind.BLADE, Target.ALLY_SINGLE, 35, school="myth")),
    "Feint": lambda: card("Feint", "death", 1, Effect(EffectKind.TRAP, Target.ENEMY_SINGLE, 70)),
    "Spirit Blade": lambda: card("Spirit Blade", "balance", 1, *(
        Effect(EffectKind.BLADE, Target.ALLY_SINGLE, 35, school=s) for s in ("myth", "life", "death"))),
    "Minor Fire Scorch": lambda: card("Minor Fire Scorch", "fire", 0, hit(90), item=True),
    "Stun": lambda: card("Stun", "myth", 1, Effect(EffectKind.STUN, Target.ENEMY_SINGLE, 1), item=True),
}


@dataclass
class Foe:
    name: str
    health: int
    school: str
    resist: dict[str, float]
    damage: int  # a typical hit
    attack_chance: float = 0.5
    boss: bool = False


MEOWIARTY = [
    # Calibrated: ~220 a round with all three up, ~160 with Meowiarty alone (the logs).
    Foe("Meowiarty", 2000, "myth", {"myth": 0.8, "storm": -0.5}, 330, attack_chance=0.45, boss=True),
    Foe("Agony Wraith", 1280, "death", {"death": 0.4, "life": -0.4}, 150, attack_chance=0.45, boss=True),
    Foe("Clockwork Wizard", 560, "life", {"life": 0.3, "death": -0.3}, 100, attack_chance=0.45),
]

ITEMS = ["Minor Fire Scorch", "Minor Fire Scorch", "Stun", "Stun"]
DECKS = {  # the player's deck (they choose it: Feint is the only death spell)
    "current": {"Pixie": 2, "Cyclops": 1, "Humongofrog": 2, "Troll Minion": 1, "Minotaur": 1, "Myth Prism": 1,
                "Myth Trap": 2, "Mythblade": 2, "Feint": 2, "Spirit Blade": 2},
}

@dataclass
class Fight:
    me: Combatant
    enemies: list[Combatant]
    foes: dict[str, Foe]
    deck: list[Card]
    hand: list[Card] = field(default_factory=list)
    pips: int = 1
    power: int = 1
    minion: Combatant | None = None
    prism_on: set[str] = field(default_factory=set)
    prismed: set[str] = field(default_factory=set)
    stunned: set[str] = field(default_factory=set)
    summoned: int = 0


def _use_up(effects: list, school: str) -> list:
    """A hit of `school` uses each matching blade/trap once."""
    return [e for e in effects if e[1] not in ("", school)]


def _apply_hit(f: Fight, c: Card, targets: list[Combatant], rng: random.Random):
    for t in targets:
        school = c.school
        spell = c
        if t.name in f.prism_on and school == "myth" and len(targets) == 1:
            spell = dataclasses.replace(c, school="storm")
            f.prism_on.discard(t.name)
        dmg = hit_damage(spell, f.me, t)
        t.health = max(0, t.health - int(dmg))
        t.is_dead = t.health <= 0
        t.incoming_effects = _use_up(t.incoming_effects, spell.school)
        if EffectKind.STEAL in c.kinds:
            f.me.health = min(f.me.max_health, f.me.health + int(dmg / 2))
    f.me.outgoing_effects = _use_up(f.me.outgoing_effects, c.school)


def _cast(f: Fight, action, rng: random.Random):
    c = action.card
    f.hand = [h for h in f.hand if h is not c]
    kinds = set(c.kinds)
    if rng.random() > ACCURACY and not (kinds & {EffectKind.HEAL}):
        return  # fizzled
    if c.is_damage:
        targets = f.enemies if c.is_aoe else [action.target]
        _apply_hit(f, c, [t for t in targets if t and not t.is_dead], rng)
    elif EffectKind.HEAL in kinds:
        f.me.health = min(f.me.max_health, f.me.health + int(sum(e.value for e in c.effects)))
    elif EffectKind.BLADE in kinds:
        for e in c.effects:
            if e.kind is EffectKind.BLADE:
                f.me.outgoing_effects.append((f"sim:{c.name}", e.school, e.value / 100))
    elif EffectKind.TRAP in kinds and action.target:
        for e in c.effects:
            if e.kind is EffectKind.TRAP:
                action.target.incoming_effects.append((f"sim:{c.name}", e.school, e.value / 100))
        if c.name == "Feint":
            f.me.incoming_effects.append(("sim:Feint self", "", 0.3))
    elif EffectKind.SHIELD in kinds:
        for e in c.effects:
            if e.kind is EffectKind.SHIELD:
                f.me.incoming_effects.append((f"sim:{c.name}:{e.school}", e.school, e.value / 100))
    elif EffectKind.SUMMON in kinds:
        f.minion = Combatant("Troll Guardian", 900, 900, is_minion=True, school="myth")
        f.summoned += 1
    elif EffectKind.STUN in kinds and action.target:
        f.stunned.add(action.target.name)
    elif _is_prism(c) and action.target:
        f.prism_on.add(action.target.name)
        f.prismed.add(action.target.name)


def _enemy_turn(f: Fight, rng: random.Random):
    if f.minion and f.minion.health > 0 and rng.random() < 0.7:
        live = [e for e in f.enemies if not e.is_dead]
        if live:
            t = rng.choice(live)
            dmg = 180 * (1 - (t.resist or {}).get("myth", 0))
            t.health = max(0, t.health - int(dmg))
            t.is_dead = t.health <= 0
    for e in f.enemies:
        if e.is_dead:
            continue
        if e.name in f.stunned:
            f.stunned.discard(e.name)
            continue
        if rng.random() > f.foes[e.name].attack_chance:
            continue
        side = [f.me] + ([f.minion] if f.minion and f.minion.health > 0 else [])
        victim = rng.choice(side)
        school = f.foes[e.name].school
        used = [x for x in victim.incoming_effects if x[1] in ("", school)]
        mult = max(0.0, 1 + sum(v for _k, _s, v in used))
        victim.health -= int(f.foes[e.name].damage * mult * rng.uniform(0.8, 1.2))
        victim.incoming_effects = [x for x in victim.incoming_effects if x not in used]


def simulate(deck: dict[str, int], foes: list[Foe], strat: Strategy | None = None, seed: int = 0,
             items: list[str] = ITEMS, hp: int = 1778) -> tuple[bool, int]:
    """One fight. (won, rounds)."""
    rng = random.Random(seed)
    cards = [CARDS[n]() for n, k in deck.items() for _ in range(k)] + [CARDS[n]() for n in items]
    rng.shuffle(cards)
    me = Combatant("Me", hp, hp, is_client=True, school="myth", resist={})
    enemies = [Combatant(x.name, x.health, x.health, is_enemy=True, is_boss=x.boss, school=x.school,
                         resist=dict(x.resist)) for x in foes]
    f = Fight(me, enemies, {x.name: x for x in foes}, cards)
    f.pips, f.power = (1, 1) if rng.random() < 0.5 else (0, 2)
    for rnd in range(1, MAX_ROUNDS + 1):
        while len(f.hand) < HAND and f.deck:
            f.hand.append(f.deck.pop())
        for i, c in enumerate(f.hand):
            c.index = i
            c.castable = _pay(c, "myth", f.pips, f.power) is not None
        discards = 2
        while True:
            allies = [f.minion] if f.minion and f.minion.health > 0 else []
            b = Battle(me=f.me, allies=allies, enemies=f.enemies, cards=f.hand, pips=f.pips,
                       power_pips=f.power, round=rnd, prismed=set(f.prismed), summoned=f.summoned,
                       upcoming=list(f.deck), deck_known=True)
            if not b.live_enemies:
                return True, rnd
            action = decide(b, strat, discards_left=discards)
            if action.kind is ActionKind.DISCARD and discards > 0:
                f.hand = [h for h in f.hand if h is not action.card]
                discards -= 1
                continue
            if action.kind is ActionKind.CAST and action.card is not None:
                paid = _pay(action.card, "myth", f.pips, f.power)
                if paid is not None:
                    f.pips, f.power = paid
                    _cast(f, action, rng)
            break
        if not [e for e in f.enemies if not e.is_dead]:
            return True, rnd
        _enemy_turn(f, rng)
        if f.me.health <= 0:
            return False, rnd
        gained = 1
        if rng.random() < POWER_PIP_CHANCE:
            f.power += gained
        else:
            f.pips += gained
        while f.pips + f.power > MAX_PIPS:
            if f.pips:
                f.pips -= 1
            else:
                f.power -= 1
    return False, MAX_ROUNDS


def win_rate(deck, foes, strat=None, n=400) -> tuple[float, float]:
    results = [simulate(deck, foes, strat, seed=s) for s in range(n)]
    wins = [r for ok, r in results if ok]
    return len(wins) / n, (sum(wins) / len(wins) if wins else 0.0)


if __name__ == "__main__":
    for name, deck in DECKS.items():
        rate, rounds = win_rate(deck, MEOWIARTY)
        print(f"{name:22s} win {rate:5.1%}  (wins take {rounds:.1f} rounds)")
