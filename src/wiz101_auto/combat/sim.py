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

import copy
import dataclasses
import json
import random
from dataclasses import dataclass, field
from pathlib import Path

from .brain import (
    Strategy,
    _is_prism,
    _overkill,
    _pay,
    _prism_gain,
    _prism_useless,
    decide,
    hit_damage,
    is_reshuffle,
    prism_view,
    setup_fits,
)
from .model import Action, ActionKind, Battle, Card, Combatant, Effect, EffectKind, Target

HAND = 7
ACCURACY = 0.8  # our spells' hit rate when the logs have too few casts of one
SHIELD_ON_QUIET_ROUND = 0.05  # a round without damage: chance it shields (Meowiarty: 2 in 80 rounds logged)
MIN_SAMPLES = 8  # rounds of an enemy needed before its own numbers are trusted
MAX_MINION_HIT = 1000  # a minion hit above this is a template id, not damage
MINION_SHARE_PRIOR = 0.2  # enemy hits going to our minion until enough rounds are measured
MINION_SHARE_ROUNDS = 10  # rounds with a minion out needed to trust the measured share

PRISM_WORTH = 1.25  # a prism move only where the converted hit lands this much harder
POWER_PIP_CHANCE = 0.35
MAX_PIPS = 7
MAX_ROUNDS = 40


def minion_share(stats: dict | None) -> float:
    """The share of enemy hits our minion takes: measured once enough rounds
    are logged, else the prior."""
    m = (stats or {}).get("minion", {})
    if m.get("rounds", 0) >= MINION_SHARE_ROUNDS:
        return m.get("share", MINION_SHARE_PRIOR)
    return MINION_SHARE_PRIOR


def card(name, school, pips, *effects, target=Target.ENEMY_SINGLE, item=False) -> Card:
    return Card(0, name, school=school, pip_cost=pips, effects=list(effects), item=item)


def hit(value, target=Target.ENEMY_SINGLE, kind=EffectKind.DAMAGE):
    return Effect(kind, target, value)


# The cards (values from the deck reads and the fights).
CARDS = {
    "Pixie": lambda: card("Pixie", "life", 2, Effect(EffectKind.HEAL, Target.SELF, 400)),
    "Bloodbat": lambda: card("Bloodbat", "myth", 1, hit(85)),
    "Cyclops": lambda: card("Cyclops", "myth", 3, hit(295)),
    "Troll": lambda: card("Troll", "myth", 2, hit(190)),
    "Ether Shield": lambda: card("Ether Shield", "myth", 0, *(
        Effect(EffectKind.SHIELD, Target.SELF, -70, school=s) for s in ("life", "death"))),
    "Minotaur": lambda: card("Minotaur", "myth", 5, hit(50), hit(445)),  # as the game reads it: two hits
    "Humongofrog": lambda: card("Humongofrog", "myth", 4, hit(295, Target.ENEMY_ALL)),
    "Troll Minion": lambda: card("Troll Minion", "myth", 2, Effect(EffectKind.SUMMON, Target.SELF, 180)),
    # A guess until it's been seen in a fight: a stronger minion than the Troll.
    "Cyclops Minion": lambda: card("Cyclops Minion", "myth", 3, Effect(EffectKind.SUMMON, Target.SELF, 260)),
    "Myth Prism": lambda: card("Myth Prism", "myth", 0, Effect(EffectKind.OTHER, Target.ENEMY_SINGLE, 0)),
    "Myth Trap": lambda: card("Myth Trap", "myth", 0,
                              Effect(EffectKind.TRAP, Target.ENEMY_SINGLE, 40, school="myth")),
    "Mythblade": lambda: card("Mythblade", "myth", 0,
                              Effect(EffectKind.BLADE, Target.ALLY_SINGLE, 35, school="myth")),
    "Feint": lambda: card("Feint", "death", 1, Effect(EffectKind.TRAP, Target.ENEMY_SINGLE, 70)),
    "Spirit Blade": lambda: card("Spirit Blade", "balance", 1, *(
        Effect(EffectKind.BLADE, Target.ALLY_SINGLE, 35, school=s) for s in ("myth", "life", "death"))),
    "Vampire": lambda: card("Vampire", "death", 4, hit(335, kind=EffectKind.STEAL)),
    "Banshee": lambda: card("Banshee", "death", 3, hit(275)),
    "Ghoul": lambda: card("Ghoul", "death", 2, hit(160, kind=EffectKind.STEAL)),
    "Dark Sprite": lambda: card("Dark Sprite", "death", 1, hit(85)),
    "Minor Fire Scorch": lambda: card("Minor Fire Scorch", "fire", 0, hit(85), item=True),
    "Stun": lambda: card("Stun", "myth", 0, Effect(EffectKind.STUN, Target.ENEMY_SINGLE, 1)),
}


# Spells with no values above (newly trained: Stone Colossus, Earthquake...)
# as the game reads them at the spellbook check (state/spell_cards.pkl):
# minions and spells with no effects aside.
SPELL_CARDS = Path("state") / "spell_cards.pkl"


def _game_cards() -> dict:
    import pickle

    try:
        read = pickle.loads(SPELL_CARDS.read_bytes())
    except Exception:
        return {}
    out = {}
    for name, c in read.items():
        if name in CARDS or not c.effects or EffectKind.SUMMON in c.kinds or c.item:
            continue
        if name.lower().startswith("collectessence"):
            continue  # (not a combat spell)
        c = dataclasses.replace(c, school=(c.school or "").lower())  # 'Myth' -> 'myth', as above
        out[name] = (lambda c=c: copy.copy(c))
    return out


GAME_CARDS = _game_cards()
CARDS.update(GAME_CARDS)


@dataclass
class Spell:
    """An enemy's spell: kind is hit, aoe, drain, dot (hit + damage over 3
    rounds), blade (itself), trap / weak (on us), shield (itself), heal (an ally)."""
    name: str
    pips: int
    kind: str
    school: str
    value: int
    extra: int = 0  # dot: damage over the next rounds


@dataclass
class Foe:
    name: str
    health: int
    school: str
    resist: dict[str, float]
    spells: list[Spell]
    boss: bool = False
    save_chance: float = 0.35  # able to afford only a cheaper attack: chance it saves up instead
    buff_chance: float = 0.55  # chance a round goes on a 0-pip blade/trap/weakness/shield
    accuracy: float = 0.85
    power: float = 1.0  # its damage bonus (bosses hit harder than the spell's base)
    # What it really did to us per round (state/enemy_stats.json, from the
    # logs; 0 = a round of buffing or saving pips). When set, its attacks are
    # drawn from these (`spells` still give its 0-pip buffs, e.g. Storm Shield).
    samples: list[int] = field(default_factory=list)


def _s(name, pips, kind, school, value, extra=0):
    return Spell(name, pips, kind, school, value, extra)


# The spells each carries (the player's list) with standard values.
MEOWIARTY = [
    Foe("Meowiarty", 2000, "myth", {"myth": 0.8, "storm": -0.5}, [
        _s("Storm Shield", 0, "shield", "storm", -80), _s("Weakness", 0, "weak", "", -25),
        _s("Mythblade", 0, "blade", "myth", 35), _s("Myth Trap", 0, "trap", "myth", 30),
        _s("Blood Bat", 1, "hit", "myth", 85), _s("Troll", 2, "hit", "myth", 200),
        _s("Cyclops", 3, "hit", "myth", 300), _s("Humongofrog", 4, "aoe", "myth", 300),
        _s("Snow Serpent", 2, "hit", "ice", 150), _s("Sunbird", 3, "hit", "fire", 250),
        _s("Storm Shark", 3, "hit", "storm", 210),
    ], boss=True, power=0.75),
    Foe("Agony Wraith", 1280, "death", {"death": 0.4, "life": -0.4}, [
        _s("Weakness", 0, "weak", "", -25), _s("Deathblade", 0, "blade", "death", 35),
        _s("Curse", 0, "trap", "death", 30), _s("Death Trap", 0, "trap", "death", 25),
        _s("Dark Sprite", 1, "hit", "death", 85), _s("Ghoul", 2, "drain", "death", 160),
        _s("Banshee", 3, "hit", "death", 275), _s("Vampire", 4, "drain", "death", 335),
        _s("Skeletal Pirate", 5, "hit", "death", 450), _s("Fire Elf", 2, "dot", "fire", 50, 210),
        _s("Cyclops", 3, "hit", "myth", 300), _s("Storm Shark", 3, "hit", "storm", 210),
    ], boss=True, power=0.55),
    Foe("Clockwork Wizard", 560, "life", {"life": 0.3, "death": -0.3}, [
        _s("Death Shield", 0, "shield", "death", -70), _s("Spirit Armor", 0, "shield", "myth", -25),
        _s("Weakness", 0, "weak", "", -25), _s("Sprite", 1, "heal", "life", 200),
        _s("Lifeblade", 0, "blade", "life", 35), _s("Life Trap", 0, "trap", "life", 30),
        _s("Imp", 1, "hit", "life", 85), _s("Leprechaun", 2, "hit", "life", 170),
        _s("Nature's Wrath", 3, "hit", "life", 280), _s("Seraph", 5, "hit", "life", 450),
        _s("Evil Snowman", 3, "hit", "ice", 270), _s("Sunbird", 3, "hit", "fire", 250),
        _s("Storm Shark", 3, "hit", "storm", 210),
    ], power=0.55),
]
DAMAGE_SCALE = 1.0  # powers and buff_chance set so fights last like the close one (death ~round 18)

ITEMS = ["Minor Fire Scorch", "Minor Fire Scorch"]  # from gear
DECKS = {  # the player's deck (they choose it: Feint is the only death spell)
    "current": {"Pixie": 3, "Cyclops": 1, "Humongofrog": 2, "Troll Minion": 1, "Minotaur": 1, "Myth Prism": 1,
                "Myth Trap": 2, "Mythblade": 2, "Stun": 3, "Feint": 2, "Spirit Blade": 2},
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
    foe_pips: dict[str, int] = field(default_factory=dict)
    dots: list = field(default_factory=list)  # our side's damage over time: [victim, per round, rounds left]
    hit_rate: dict[str, float] = field(default_factory=dict)  # per spell, from the logs (else ACCURACY)
    minion_share: float = 0.2  # share of enemy hits our minion takes (measured: stats["minion"])
    log: list | None = None  # a replay's steps (the visualizer); None: not recorded


def _note(f: Fight, **step):
    if f.log is not None:
        f.log.append({**step, "state": _snapshot(f)})


def _fx(c: Combatant) -> list[dict]:
    out = []
    for key, school, v in c.outgoing_effects:
        out.append({"kind": "blade" if v > 0 else "weakness", "school": school, "pct": round(v * 100),
                    "src": key.split(":")[-1]})
    for key, school, v in c.incoming_effects:
        out.append({"kind": "trap" if v > 0 else "shield", "school": school, "pct": round(v * 100),
                    "src": key.split(":")[-1]})
    for school, v in c.aura.items():
        out.append({"kind": "aura", "school": school, "pct": round(v * 100), "src": "aura"})
    return out


def _snapshot(f: Fight) -> dict:
    m = f.minion
    return {
        "me": {"name": "Wizard", "hp": max(0, f.me.health), "max": f.me.max_health, "pips": f.pips,
               "power": f.power, "fx": _fx(f.me)},
        "minion": ({"name": m.name, "hp": max(0, m.health), "max": m.max_health}
                   if m and m.health > 0 else None),
        "enemies": [{"name": e.name, "hp": max(0, e.health), "max": e.max_health, "boss": e.is_boss,
                     "school": e.school, "fx": _fx(e), "prism": e.name in f.prism_on,
                     "stunned": e.name in f.stunned, "pips": f.foe_pips.get(e.name, 0)} for e in f.enemies],
        "hand": [c.name for c in f.hand],
        "deck_left": len(f.deck),
    }


def _use_up(effects: list, school: str) -> list:
    """A hit of `school` uses each matching blade/trap once."""
    return [e for e in effects if e[1] not in ("", school)]


def _apply_hit(f: Fight, c: Card, targets: list[Combatant], rng: random.Random):
    for t in targets:
        school = c.school
        spell = c
        seen, used = t, spell.school
        if t.name in f.prism_on and school == "myth":  # a hit-all too (the frog)
            # Lands as storm: storm traps/shields and resist; our blades still count.
            seen, used = prism_view(t, "myth"), "storm"
            f.prism_on.discard(t.name)
        dmg = hit_damage(spell, f.me, seen)
        t.health = max(0, t.health - int(dmg))
        t.is_dead = t.health <= 0
        t.incoming_effects = _use_up(t.incoming_effects, used)
        if EffectKind.STEAL in c.kinds:
            f.me.health = min(f.me.max_health, f.me.health + int(dmg / 2))
    f.me.outgoing_effects = _use_up(f.me.outgoing_effects, c.school)


def _cast(f: Fight, action, rng: random.Random):
    c = action.card
    f.hand = [h for h in f.hand if h is not c]
    kinds = set(c.kinds)
    if rng.random() > f.hit_rate.get(c.name, ACCURACY) and not (kinds & {EffectKind.HEAL}):
        return  # fizzled
    if c.is_damage:
        targets = f.enemies if c.is_aoe else [action.target]
        _apply_hit(f, c, [t for t in targets if t and not t.is_dead], rng)
        for e in c.effects:
            # A lasting boost for the rest of the fight (Vermin Virtuoso: +25%
            # myth after its hit); a second cast doesn't add to it.
            if e.kind is EffectKind.OTHER and e.target is Target.NONE and e.school and e.value > 0:
                school = e.school.lower()
                f.me.aura[school] = max(f.me.aura.get(school, 0.0), e.value / 100)
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
        if not (f.minion and f.minion.health > 0):  # one out at a time
            # The simulator's own minion cards carry its hit; a card read from the
            # game carries the minion's template id there (36060): a Troll's 180.
            power = int(max((e.value for e in c.effects if e.kind is EffectKind.SUMMON), default=0))
            power = power if 0 < power <= MAX_MINION_HIT else 180
            f.minion = Combatant("Troll Guardian", 900, 900, is_minion=True, school="myth",
                                 damage_bonus={"power": power})
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
            dmg = f.minion.damage_bonus.get("power", 180) * (1 - (t.resist or {}).get("myth", 0))
            # Its hit breaks a trap on the target, our Feint too.
            traps = [x for x in t.incoming_effects if x[2] > 0 and x[1] in ("", "myth")]
            if traps:
                dmg *= 1 + traps[0][2]
                t.incoming_effects = [x for x in t.incoming_effects if x is not traps[0]]
            t.health = max(0, t.health - int(dmg))
            t.is_dead = t.health <= 0
            _note(f, who="minion", act="hit", target=t.name, dmg={t.name: int(dmg)})
    # Damage over time on our side.
    for dot in f.dots:
        dot[0].health -= dot[1]
        dot[2] -= 1
    f.dots = [d for d in f.dots if d[2] > 0]
    for e in f.enemies:
        if e.is_dead:
            continue
        foe = f.foes[e.name]
        gain = 2 if foe.boss and rng.random() < 0.3 else 1
        pips = f.foe_pips[e.name] = min(MAX_PIPS, f.foe_pips.get(e.name, rng.randint(0, 1)) + gain)
        if e.name in f.stunned:
            f.stunned.discard(e.name)
            _note(f, who=e.name, act="stunned")
            continue
        side = [f.me]  # enemies don't bother with the minion (the player's experience)
        # Heal a hurt ally (the Clockwork Wizard's Sprite).
        heals = [sp for sp in foe.spells if sp.kind == "heal" and sp.pips <= pips]
        hurt = [a for a in f.enemies if not a.is_dead and a.health < a.max_health * 0.5]
        if heals and hurt and rng.random() < 0.7:
            sp = heals[0]
            f.foe_pips[e.name] = pips - sp.pips
            if rng.random() <= foe.accuracy:
                low = min(hurt, key=lambda a: a.health / a.max_health)
                low.health = min(low.max_health, low.health + sp.value)
                _note(f, who=e.name, act="heal", card=sp.name, target=low.name)
            continue
        if foe.samples:
            dmg = rng.choice(foe.samples)
            if dmg <= 0:
                # A round without damage: it may put up a shield (Storm Shield
                # against our prismed hits). Its blades/traps are in the samples.
                shields = [sp for sp in foe.spells if sp.kind == "shield"]
                if shields and rng.random() < SHIELD_ON_QUIET_ROUND:
                    sp = rng.choice(shields)
                    e.incoming_effects.append((f"foe:{e.name}:{sp.name}", sp.school, sp.value / 100))
                    _note(f, who=e.name, act="shield", card=sp.name, target=e.name)
                else:
                    _note(f, who=e.name, act="setup")  # blades/traps (in the samples) or saving
                continue
            if f.minion and f.minion.health > 0 and rng.random() < f.minion_share:
                # It went for our minion (the MooShu boss hit the Cyclops minion).
                f.minion.health -= dmg
                if f.minion.health <= 0:
                    f.minion.is_dead = True
                _note(f, who=e.name, act="hit", target=f.minion.name, dmg={f.minion.name: int(dmg)})
                continue
            victim = f.me
            ours = [x for x in victim.incoming_effects if x[2] < 0 and x[1] in ("", foe.school)]
            victim.incoming_effects = [x for x in victim.incoming_effects if x not in ours]
            done = int(dmg * max(0.0, 1 + sum(v for _k, _s, v in ours)))
            victim.health -= done
            _note(f, who=e.name, act="hit", target="Wizard", dmg={"Wizard": done})
            continue
        buffs = [sp for sp in foe.spells if sp.pips == 0]
        if buffs and rng.random() < foe.buff_chance:
            sp = rng.choice(buffs)
            if rng.random() > foe.accuracy:
                continue
            key = f"foe:{e.name}:{sp.name}"
            if sp.kind == "blade":
                e.outgoing_effects.append((key, sp.school, sp.value / 100))
            elif sp.kind == "shield":
                e.incoming_effects.append((key, sp.school, sp.value / 100))
            elif sp.kind == "trap":
                f.me.incoming_effects.append((key, sp.school, sp.value / 100))
            elif sp.kind == "weak":
                f.me.outgoing_effects.append((key, "", sp.value / 100))
            _note(f, who=e.name, act=sp.kind, card=sp.name,
                  target="Wizard" if sp.kind in ("trap", "weak") else e.name)
            continue
        attacks = [sp for sp in foe.spells if sp.kind in ("hit", "aoe", "drain", "dot") and sp.pips <= pips]
        if not attacks:
            continue
        top = max(sp.pips for sp in foe.spells if sp.kind in ("hit", "aoe", "drain", "dot"))
        sp = rng.choice([a for a in attacks if a.pips == max(x.pips for x in attacks)])
        if sp.pips < top and rng.random() < foe.save_chance:
            _note(f, who=e.name, act="saving")
            continue  # saving up for its big one
        f.foe_pips[e.name] = pips - sp.pips
        if rng.random() > foe.accuracy:
            _note(f, who=e.name, act="fizzle", card=sp.name)
            continue  # fizzled
        blade = [x for x in e.outgoing_effects if x[1] in ("", sp.school)]
        e.outgoing_effects = [x for x in e.outgoing_effects if x not in blade]
        for victim in (side if sp.kind == "aoe" else [rng.choice(side)]):
            used = [x for x in victim.incoming_effects if x[1] in ("", sp.school)]
            victim.incoming_effects = [x for x in victim.incoming_effects if x not in used]
            mult = max(0.0, 1 + sum(v for _k, _s, v in blade)) * max(0.0, 1 + sum(v for _k, _s, v in used))
            dmg = int(sp.value * foe.power * DAMAGE_SCALE * mult * rng.uniform(0.85, 1.15))
            victim.health -= dmg
            if sp.kind == "drain":
                e.health = min(e.max_health, e.health + dmg // 2)
            if sp.kind == "dot":
                f.dots.append([victim, int(sp.extra * DAMAGE_SCALE / 3), 3])
            _note(f, who=e.name, act="hit", card=sp.name, target="Wizard", dmg={"Wizard": dmg})


def _expected_hits(f: Fight, action: Action) -> list[float] | None:
    """The damage `action` will do to each enemy, before casting (a kill hides
    it afterwards): blades, traps, resists, a waiting prism counted."""
    c = action.card
    if action.kind is not ActionKind.CAST or c is None or not c.is_damage:
        return None
    out = []
    for e in f.enemies:
        hit = not e.is_dead and (c.is_aoe or (action.target is not None and action.target.name == e.name))
        seen = prism_view(e, "myth") if e.name in f.prism_on and c.school == "myth" else e
        out.append(hit_damage(c, f.me, seen) if hit else 0.0)
    return out


def _round(f: Fight, rng: random.Random, strat: Strategy | None, rnd: int, first: Action | None = None,
           discards: int = 2, record: list | None = None) -> bool:
    """Our turn of round `rnd` (the hand already drawn): the brain decides
    (after `first`, a move forced on it: the rollout's candidate), then the
    enemies act and we gain a pip. True once the fight is over."""
    for i, c in enumerate(f.hand):
        c.index = i
        c.castable = _pay(c, "myth", f.pips, f.power) is not None
    while True:
        if first is not None:
            action, first = first, None
        else:
            allies = [f.minion] if f.minion and f.minion.health > 0 else []
            b = Battle(me=f.me, allies=allies, enemies=f.enemies, cards=f.hand, pips=f.pips,
                       power_pips=f.power, round=rnd, prismed=set(f.prism_on), summoned=f.summoned,
                       upcoming=list(f.deck), deck_known=True)
            if not b.live_enemies:
                return True
            action = decide(b, strat, discards_left=discards)
        if action.kind is ActionKind.DISCARD and discards > 0:
            if record is not None:
                record.append((rnd, action, [e.health for e in f.enemies], None))
            f.hand = [h for h in f.hand if h is not action.card]
            discards -= 1
            _note(f, round=rnd, who="Wizard", act="discard", card=action.card.name, reason=action.reason)
            continue
        before = [e.health for e in f.enemies]
        expect = _expected_hits(f, action) if record is not None else None
        cast = False
        if action.kind is ActionKind.CAST and action.card is not None:
            paid = _pay(action.card, "myth", f.pips, f.power)
            if paid is not None:
                f.pips, f.power = paid
                _cast(f, action, rng)
                cast = True
        if f.log is not None:
            dealt = {e.name: b0 - e.health for e, b0 in zip(f.enemies, before, strict=True) if b0 != e.health}
            if cast:
                aoe = action.card.is_aoe
                tgt = action.target.name if action.target else ("all enemies" if aoe else "Wizard")
                if action.target is not None and action.target.is_client:
                    tgt = "Wizard"
                _note(f, round=rnd, who="Wizard", act="cast", card=action.card.name, target=tgt,
                      kind=category(action), reason=action.reason, dmg=dealt,
                      fizzled=not dealt and action.card.is_damage)
            else:
                _note(f, round=rnd, who="Wizard", act="pass", reason=action.reason)
        if record is not None:
            record.append((rnd, action, before, expect))
        break
    if not [e for e in f.enemies if not e.is_dead]:
        return True
    _enemy_turn(f, rng)
    if f.me.health <= 0:
        return True
    if rng.random() < POWER_PIP_CHANCE:
        f.power += 1
    else:
        f.pips += 1
    while f.pips + f.power > MAX_PIPS:
        if f.pips:
            f.pips -= 1
        else:
            f.power -= 1
    return False


def _run(f: Fight, rng: random.Random, strat: Strategy | None, start: int, first: Action | None = None,
         discards: int = 2, drawn: bool = False, horizon: int = MAX_ROUNDS) -> tuple[bool, int]:
    """Play on from round `start` (`drawn`: its hand is already in f.hand) to
    the end, or for `horizon` rounds. (won, rounds played); not won and
    alive = still going at the horizon."""
    for rnd in range(start, start + horizon):
        if not drawn:
            while len(f.hand) < HAND and f.deck:
                f.hand.append(f.deck.pop())
        drawn = False
        if _round(f, rng, strat, rnd, first, discards):
            return f.me.health > 0, rnd - start + 1
        first, discards = None, 2
    return False, horizon


def simulate(deck: dict[str, int], foes: list[Foe], strat: Strategy | None = None, seed: int = 0,
             items: list[str] = ITEMS, hp: int = 1843, stats: dict | None = None) -> tuple[bool, int]:
    """One fight from the start. (won, rounds). `stats` (state/enemy_stats.json)
    gives the enemies their logged damage and our spells their hit rates."""
    rng = random.Random(seed)
    cards = [CARDS[n]() for n, k in deck.items() for _ in range(k)] + [CARDS[n]() for n in items]
    rng.shuffle(cards)
    mine = load_my_stats()  # the wizard as last read in a fight: gear's damage bonus and all
    hp = mine.get("max_health", hp)
    me = Combatant("Me", hp, hp, is_client=True, school="myth", resist=mine.get("resist", {}),
                   damage_bonus=mine.get("damage_bonus", {}))
    if stats:
        foes = [with_samples(x, stats) for x in foes]
    enemies = [Combatant(x.name, x.health, x.health, is_enemy=True, is_boss=x.boss, school=x.school,
                         resist=dict(x.resist)) for x in foes]
    f = Fight(me, enemies, {x.name: x for x in foes}, cards, hit_rate=(stats or {}).get("hit_rate", {}),
              minion_share=minion_share(stats))
    f.pips, f.power = (1, 1) if rng.random() < 0.5 else (0, 2)
    return _run(f, rng, strat, 1)


def replay(deck: dict[str, int], foes: list[Foe], stats: dict | None = None, seed: int = 0,
           strat: Strategy | None = None) -> dict:
    """One fight with every step recorded (the visualizer's battle board):
    {"won", "rounds", "seed", "steps": [...]}."""
    rng = random.Random(seed)
    cards = [CARDS[n]() for n, k in deck.items() for _ in range(k)] + [CARDS[n]() for n in ITEMS]
    rng.shuffle(cards)
    mine = load_my_stats()
    hp = mine.get("max_health", 1843)
    me = Combatant("Me", hp, hp, is_client=True, school="myth", resist=mine.get("resist", {}),
                   damage_bonus=mine.get("damage_bonus", {}))
    if stats:
        foes = [with_samples(x, stats) for x in foes]
    enemies = [Combatant(x.name, x.health, x.health, is_enemy=True, is_boss=x.boss, school=x.school,
                         resist=dict(x.resist)) for x in foes]
    f = Fight(me, enemies, {x.name: x for x in foes}, cards, hit_rate=(stats or {}).get("hit_rate", {}),
              minion_share=minion_share(stats), log=[])
    f.pips, f.power = (1, 1) if rng.random() < 0.5 else (0, 2)
    while len(f.hand) < HAND and f.deck:
        f.hand.append(f.deck.pop())
    _note(f, round=1, who="", act="start")
    won, rounds = _run(f, rng, strat, 1, drawn=True)
    return {"won": won, "rounds": rounds, "seed": seed, "steps": f.log}


def _one(args):
    deck, foes, strat, seed, stats = args
    return simulate(deck, foes, strat, seed=seed, stats=stats)


def win_rate(deck, foes, strat=None, n=400, pool=None, seed0=0, stats=None) -> tuple[float, float]:
    """(win rate, mean rounds of the wins) over `n` seeded fights; `pool`: a
    multiprocessing pool to spread them over."""
    jobs = [(deck, foes, strat, seed0 + s, stats) for s in range(n)]
    results = pool.map(_one, jobs, chunksize=16) if pool else [_one(j) for j in jobs]
    wins = [r for ok, r in results if ok]
    return len(wins) / n, (sum(wins) / len(wins) if wins else 0.0)


# --- enemies from the logs -------------------------------------------------

STATS_FILE = Path("state") / "enemy_stats.json"
KNOWN_SPELLS = {x.name: x.spells for x in MEOWIARTY}  # the player's lists: shields and all


MY_STATS_FILE = Path("state") / "my_stats.json"


def load_my_stats(path: Path = MY_STATS_FILE) -> dict:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def load_stats(path: Path = STATS_FILE) -> dict:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def samples_for(name: str, max_health: int, boss: bool, stats: dict) -> list[int]:
    """Its own logged rounds (alone, else alone and shared) when there are
    enough; else those of enemies like it (boss or not, similar health)."""
    enemies = stats.get("enemies", {})
    own = enemies.get(name)
    if own:
        if len(own["alone"]) >= MIN_SAMPLES:
            return list(own["alone"])
        mixed = own["alone"] + own["shared"]
        if len(mixed) >= MIN_SAMPLES:
            return mixed
    alike = [
        x for e in enemies.values()
        if e["boss"] == boss and max_health * 0.6 <= e["max_health"] <= max_health * 1.6
        for x in e["alone"]
    ]
    if len(alike) >= MIN_SAMPLES:
        return alike
    return [x for e in enemies.values() if e["boss"] == boss for x in e["alone"]] or [0, 100]


def with_samples(foe: Foe, stats: dict) -> Foe:
    return dataclasses.replace(foe, samples=samples_for(foe.name, foe.health, foe.boss, stats))


def fight_from_battle(battle: Battle, stats: dict, rng: random.Random) -> Fight:
    """The live fight as a simulation: our health, pips, hand and the rest of
    the deck (shuffled), the enemies as they stand (health, school, resist,
    effects) with their logged damage; prisms waiting on them."""
    me = copy.deepcopy(battle.me)
    enemies = [copy.deepcopy(e) for e in battle.enemies]
    foes = {}
    for e in enemies:
        foes[e.name] = Foe(e.name, e.max_health, e.school or "", dict(e.resist or {}),
                           KNOWN_SPELLS.get(e.name, []), boss=e.is_boss,
                           samples=samples_for(e.name, e.max_health, e.is_boss, stats))
    hand = [copy.copy(c) for c in battle.cards]
    deck = [copy.copy(c) for c in battle.upcoming]
    rng.shuffle(deck)
    minion = next((copy.deepcopy(a) for a in battle.allies if a.is_minion and a.health > 0), None)
    return Fight(me, enemies, foes, deck, hand=hand, pips=battle.pips, power=battle.power_pips,
                 minion=minion, prism_on=set(battle.prismed), summoned=battle.summoned,
                 foe_pips={e.name: rng.randint(0, 2) for e in enemies}, hit_rate=stats.get("hit_rate", {}),
                 minion_share=minion_share(stats))


def _wastes_setup(c: Card, t: Combatant, battle: Battle) -> bool:
    """A chip hit (under half our best hit) that would use up a trap on `t`
    or our blades without killing it: the Feint was for the big hit (a 0-pip
    wand hit spent two Feints on Meowiarty)."""
    school = c.school.lower()
    traps = any(v > 0 and sch in ("", school) for _k, sch, v in t.incoming_effects)
    blades = any(v > 0 and sch in ("", school) for _k, sch, v in battle.me.outgoing_effects)
    prismed = t.name in battle.prismed  # (the prism turns only the next hit: a chip hit spends it)
    if not (traps or blades or prismed):
        return False
    best = max((h.base_damage() for h in [*battle.cards, *battle.upcoming] if h.is_damage), default=0)
    return c.base_damage() < best / 2 and hit_damage(c, battle.me, t) < t.health


MIN_DECK_TO_DISCARD = 4  # rollouts don't bin cards with fewer left in the deck


def candidates(battle: Battle, discards: int = 0) -> list[Action]:
    """Every move this step: each castable card on each target it can take
    (not chip hits that would waste a trap or blade), passing, and (with
    `discards` left) binning each kind of card for a new draw."""
    out = [Action(ActionKind.PASS, reason="rollout: pass")]
    # (Not with the deck nearly out: binning 8 cards in a fight emptied it,
    # and a hand of what's left can't be redrawn.)
    if discards > 0 and len(battle.upcoming) >= MIN_DECK_TO_DISCARD:
        binned = set()
        # A boss can take most of our health in one hit (War Oni: 1100): its
        # heals are never binned, however full our health is now.
        keep_heals = any(e.is_boss for e in battle.live_enemies)
        for c in battle.cards:
            if _is_prism(c) and not _prism_useless(c, battle):
                continue  # a prism with work to do (Cyrus Drake: binned, then Colossus hit his resist)
            if is_reshuffle(c):
                continue  # (never discarded: the player's rule)
            if c.name not in binned and not c.treasure and not (keep_heals and c.is_heal):
                binned.add(c.name)
                out.append(Action(ActionKind.DISCARD, c, reason="rollout: discard for a draw"))
    live = battle.live_enemies
    seen = set()
    for c in battle.cards:
        if not c.castable or c.is_enchant:
            continue
        if EffectKind.SUMMON in c.kinds:
            continue  # no minions at all (the player's rule)
        if is_reshuffle(c):
            continue  # (the brain casts it, on ourselves, when the deck runs dry)
        if not c.is_damage and not setup_fits(c, battle):
            continue  # a trap/blade boosting none of our hits (an ice trap, no ice hits)
        if c.target is Target.ENEMY_SINGLE:
            targets = [t for t in live if not (c.is_damage and (_wastes_setup(c, t, battle)
                                                                 or _overkill(c, t, battle)))]
        elif c.target in (Target.ALLY_SINGLE, Target.SELF):
            targets = [battle.me]
        else:
            targets = [None]
        if _is_prism(c):
            # Only where it converts to something the target is weak to (not a
            # prism on the Death wraith).
            # Not on one prismed already and not hit since: a second prism
            # changes nothing (Haru took three in a row, no hit between).
            targets = [
                t for t in targets
                if t is not None and t.name not in battle.prismed
                and _prism_gain(c, battle.me, t, battle.cards) >= PRISM_WORTH
            ]
        for t in targets:
            key = (c.name, t.name if t else None)
            if key in seen:
                continue
            seen.add(key)
            out.append(Action(ActionKind.CAST, c, t, reason="rollout"))
    return out


HORIZON = 10  # rounds a rollout looks ahead


def rollout_value(won: bool, dead: bool, rounds: int, horizon: int, damage: float, kills: float,
                  health: float) -> float:
    """How good the end of a rollout is (0..~1.3): a win above anything else,
    sooner and healthier better; else progress (share of the enemies' health
    taken, share of them killed) and our health; dying counts least."""
    if won:
        return 1.0 + 0.2 * (horizon - rounds) / horizon + 0.1 * health
    if dead:
        return 0.3 * damage + 0.1 * kills - 0.2
    return 0.5 * damage + 0.15 * kills + 0.3 * health


@dataclass
class Outcome:
    action: Action
    value: float  # mean rollout_value
    wins: float  # share of the rollouts won within the horizon
    deaths: float  # share where we died
    damage: float  # mean share of the enemies' health taken

    @property
    def score(self) -> float:
        return self.value


def _rollout(battle: Battle, action: Action, strat, stats: dict, seed: int, discards: int,
             horizon: int = HORIZON) -> tuple[bool, bool, float]:
    """(won, died, value) of one playout of `action`."""
    rng = random.Random(seed)
    f = fight_from_battle(battle, stats, rng)
    start_hp = sum(max(0, e.health) for e in f.enemies)
    first = action
    if action.card is not None:
        card = f.hand[battle.cards.index(action.card)]
        target = None
        if action.target is not None:
            target = f.me if action.target is battle.me else next(
                (e for e in f.enemies if e.name == action.target.name), None)
        first = Action(action.kind, card, target, reason=action.reason)
    won, rounds = _run(f, rng, strat, battle.round, first, discards, drawn=True, horizon=horizon)
    dead = f.me.health <= 0
    left = sum(max(0, e.health) for e in f.enemies)
    alive_before = sum(1 for e in battle.enemies if not e.is_dead and e.health > 0)
    kills = sum(1 for e in f.enemies if e.health <= 0) - (len(battle.enemies) - alive_before)
    health = max(0, f.me.health) / max(1, f.me.max_health)
    value = rollout_value(won, dead, rounds, horizon, 1 - left / max(1, start_hp),
                          kills / max(1, alive_before), health)
    return won, dead, value, 1 - left / max(1, start_hp)


def evaluate(battle: Battle, actions: list[Action], strat=None, stats: dict | None = None, n: int = 40,
             seed0: int = 0, discards: int = 2, horizon: int = HORIZON) -> list[Outcome]:
    """Each move played out `n` times for `horizon` rounds (the same random
    draws for every move, so they compare fairly), the brain playing on."""
    stats = stats if stats is not None else load_stats()
    out = []
    for a in actions:
        results = [_rollout(battle, a, strat, stats, seed0 + s, discards, horizon) for s in range(n)]
        out.append(Outcome(a, sum(r[2] for r in results) / n, sum(r[0] for r in results) / n,
                           sum(r[1] for r in results) / n, sum(r[3] for r in results) / n))
    return sorted(out, key=lambda o: -o.score)


if __name__ == "__main__":
    stats = load_stats()
    for name, deck in DECKS.items():
        rate, rounds = win_rate(deck, MEOWIARTY, stats=stats)
        print(f"{name:22s} win {rate:5.1%}  (wins take {rounds:.1f} rounds)")


# --- the plan shown on the stream page ---------------------------------------

SELF_KINDS = ("heal", "buff", "shield", "minion")  # untargeted: on us
SPREAD = 0.10  # a spell's damage range around its listed value (Wizard101's are about +-10%)
PLAN_STEPS = 12


class _Always(dict):
    def get(self, key, default=None):
        return 1.0  # the plan assumes every spell lands


def category(action: Action) -> str:
    """attack / buff / debuff / heal / shield / minion / stun / prism / discard / wait."""
    if action.kind is ActionKind.PASS or action.card is None:
        return "wait"
    if action.kind is ActionKind.DISCARD:
        return "discard"
    c = action.card
    kinds = c.kinds
    if _is_prism(c):
        return "prism"
    if c.is_damage:
        return "attack"
    if EffectKind.HEAL in kinds:
        return "heal"
    if EffectKind.SUMMON in kinds:
        return "minion"
    if EffectKind.STUN in kinds:
        return "stun"
    if EffectKind.BLADE in kinds:
        return "buff"
    if EffectKind.TRAP in kinds:
        return "debuff"
    if EffectKind.SHIELD in kinds:
        return "shield"
    return "other"


def plan_preview(battle: Battle, first: Action, strat=None, stats: dict | None = None,
                 steps: int = PLAN_STEPS, discards: int = 2) -> dict:
    """The fight played on from the move just chosen, the way the bot means to
    play it: every spell lands, the enemies deal their typical damage. Each
    step: round, category, spell, target, damage range per enemy and every
    enemy's health range after it; and the rounds to the win."""
    stats = stats if stats is not None else load_stats()
    rng = random.Random(0)
    f = fight_from_battle(battle, stats, rng)
    f.hit_rate = _Always()
    for foe in f.foes.values():
        if foe.samples:
            foe.samples = [round(sum(foe.samples) / len(foe.samples))]
    record: list = []
    forced = first
    if first.card is not None and first.card in battle.cards:
        card = f.hand[battle.cards.index(first.card)]
        target = None
        if first.target is not None:
            target = f.me if first.target is battle.me else next(
                (e for e in f.enemies if e.name == first.target.name), None)
        forced = Action(first.kind, card, target, reason=first.reason)
    names = [e.name for e in f.enemies]
    lo = [max(0, e.health) for e in f.enemies]
    hi = list(lo)
    out_steps = []
    rnd, drawn, won = battle.round, True, False
    for _ in range(steps):
        if not drawn:
            while len(f.hand) < HAND and f.deck:
                f.hand.append(f.deck.pop())
        drawn = False
        start = len(record)
        over = _round(f, rng, strat, rnd, forced, discards, record=record)
        forced, discards = None, 2
        for r, action, before, expect in record[start:]:
            after = [e.health for e in f.enemies]
            dmg = []
            for i, (b, a) in enumerate(zip(before, after, strict=False)):
                d = expect[i] if expect else (max(0, b - a) if action.kind is ActionKind.CAST else 0)
                if d:
                    dl, dh = int(d * (1 - SPREAD)), int(d * (1 + SPREAD))
                    lo[i], hi[i] = max(0, lo[i] - dh), max(0, hi[i] - dl)
                    dmg.append([dl, dh])
                else:
                    dmg.append(None)
            target = action.target.name if action.target is not None else (
                "all enemies" if action.card is not None and action.card.is_aoe else "")
            on_us = target == f.me.name or (not target and category(action) in SELF_KINDS)
            out_steps.append({
                "round": r, "kind": category(action),
                "spell": action.card.name if action.card is not None else "",
                "target": "self" if on_us else target,
                "dmg": dmg, "after": [[lo[i], hi[i]] for i in range(len(names))],
            })
        # Enemy heals (the Clockwork Wizard's Sprite) raise the range too.
        for i, e in enumerate(f.enemies):
            mid = max(0, e.health)
            if mid > hi[i]:
                lo[i] += mid - hi[i]
                hi[i] = mid
        rnd += 1
        if over:
            won = f.me.health > 0 and not [e for e in f.enemies if e.health > 0]
            break
    rounds = (out_steps[-1]["round"] - battle.round + 1) if won and out_steps else None
    return {"steps": out_steps, "rounds": rounds, "won": won, "lost": f.me.health <= 0, "horizon": steps,
            "enemies": [{"name": e.name, "hp": max(0, e.health), "max": e.max_health, "boss": e.is_boss}
                        for e in battle.enemies]}
