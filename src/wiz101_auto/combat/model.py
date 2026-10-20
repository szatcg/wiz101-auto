"""Game-agnostic combat data model.

Nothing in this module touches game memory, so the decision logic built on it
can be unit tested anywhere. `reader.py` converts WizWalker objects into these.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum, auto
from functools import cached_property


class EffectKind(Enum):
    DAMAGE = auto()
    DOT = auto()  # damage over time
    STEAL = auto()  # drain: damage + self heal
    HEAL = auto()
    HOT = auto()  # heal over time
    BLADE = auto()  # +outgoing damage on caster/ally
    TRAP = auto()  # +incoming damage on enemy
    SHIELD = auto()  # -incoming damage on caster/ally
    WEAKNESS = auto()  # -outgoing damage on enemy
    ENCHANT_DAMAGE = auto()  # treasure/enchant card that adds damage to a spell
    ENCHANT_ACCURACY = auto()
    PIPS = auto()
    STUN = auto()
    SUMMON = auto()  # minion
    REMOVE_WARD = auto()  # Pierce: takes the newest ward (shield) off the target
    ENCHANT_TRAP = auto()  # Potent Trap: +value% onto a trap card in hand
    ENCHANT_BLADE = auto()  # Sharpened Blade: +value% onto a blade card in hand
    OTHER = auto()


class Target(Enum):
    ENEMY_SINGLE = auto()
    ENEMY_ALL = auto()
    SELF = auto()
    ALLY_SINGLE = auto()
    ALLY_ALL = auto()
    SPELL = auto()  # enchants: targets another card in hand
    NONE = auto()


DAMAGE_KINDS = {EffectKind.DAMAGE, EffectKind.DOT, EffectKind.STEAL}
HEAL_KINDS = {EffectKind.HEAL, EffectKind.HOT}


@dataclass
class Effect:
    kind: EffectKind
    target: Target
    value: float = 0.0  # damage/heal amount, or percent for blades/traps/shields
    rounds: int = 0
    school: str = ""  # lower case school a blade/trap/shield applies to ("" = every school)


@dataclass
class Card:
    index: int  # position in hand, left to right
    name: str
    school: str = ""
    pip_cost: int = 0
    accuracy: int = 100
    effects: list[Effect] = field(default_factory=list)
    castable: bool = True
    enchanted: bool = False
    treasure: bool = False
    item: bool = False  # granted by gear (e.g. a wand's off-school spells)
    template_name: str = ""  # e.g. 'Minion Myth 001' (display names lack the number)
    template_id: int = 0  # the spell's id: a hanging blade/trap's "spell:<id>" is this card's own
    banned: bool = False  # a boss's rules forbid it all fight (never planned, in any round)

    @cached_property  # effects never change after a card is made
    def target(self) -> Target:
        # The primary target is the first effect that has one.
        for e in self.effects:
            if e.target is not Target.NONE:
                return e.target
        return Target.NONE

    @cached_property
    def kinds(self) -> frozenset[EffectKind]:
        return frozenset(e.kind for e in self.effects)

    @cached_property
    def is_enchant(self) -> bool:
        return self.target is Target.SPELL

    @cached_property
    def is_damage(self) -> bool:
        return bool(self.kinds & DAMAGE_KINDS) and not self.is_enchant

    @cached_property
    def is_aoe(self) -> bool:
        return any(e.target is Target.ENEMY_ALL and e.kind in DAMAGE_KINDS for e in self.effects)

    @cached_property
    def is_heal(self) -> bool:
        return bool(self.kinds & HEAL_KINDS)

    @cached_property
    def is_shadow(self) -> bool:
        """A shadow spell (Shadow Self Nova, shown as Dark Nova): paid with a
        shadow pip, not regular pips."""
        return (self.school or "").lower() == "shadow"

    @cached_property
    def timed_blade(self) -> int:
        """Rounds a lasting outgoing boost on us lasts (Frenzy: +40% for 4
        rounds, an aura not used up by hits); 0 for an ordinary blade."""
        return max((e.rounds for e in self.effects
                    if e.kind is EffectKind.BLADE and e.target is Target.SELF and e.rounds > 0), default=0)

    def pip_gain(self) -> tuple[str, int]:
        """(school, pips) the spell gives us (the Nova: a myth pip)."""
        n = sum(int(e.value) for e in self.effects if e.kind is EffectKind.PIPS and e.target is Target.SELF)
        school = next((e.school for e in self.effects if e.kind is EffectKind.PIPS and e.school), "")
        return school.lower(), n

    def base_damage(self) -> float:
        """Total damage per target, before buffs."""
        return sum(e.value for e in self.effects if e.kind in DAMAGE_KINDS)

    def heal_amount(self) -> float:
        return sum(e.value for e in self.effects if e.kind in HEAL_KINDS)


@dataclass
class Combatant:
    name: str
    health: int
    max_health: int
    is_client: bool = False
    is_enemy: bool = False
    is_boss: bool = False
    is_dead: bool = False
    is_minion: bool = False
    is_stunned: bool = False
    mana: int | None = None  # read for the wizard only
    school: str = ""  # lower case, e.g. "myth"
    # Per-school incoming damage reduction as a fraction (0.3 = resists 30%,
    # negative = takes extra). None when the stats couldn't be read.
    resist: dict[str, float] | None = None
    damage_bonus: dict[str, float] = field(default_factory=dict)  # outgoing, per school (players: curved)
    # The rest of the damage formula (Deimos's combat_math): flat damage and
    # flat resist (points), pierce (fraction), critical and block ratings.
    damage_flat: dict[str, float] = field(default_factory=dict)
    resist_flat: dict[str, float] = field(default_factory=dict)
    pierce: dict[str, float] = field(default_factory=dict)
    crit: dict[str, float] = field(default_factory=dict)
    block: dict[str, float] = field(default_factory=dict)
    accuracy: dict[str, float] = field(default_factory=dict)  # gear's accuracy bonus (fraction) per school
    level: int = 0
    # Lasting boosts for the rest of the fight (Vermin Virtuoso: +25% myth;
    # auras; the battle's global effect), never used up by a hit, unlike
    # blades: school ("" = every school) -> fraction.
    aura: dict[str, float] = field(default_factory=dict)
    # Flat hanging effects, in points: (key, school, value); incoming ones are
    # flat wards and absorbs (negative), outgoing ones flat blades.
    incoming_flat: list[tuple[str, str, float]] = field(default_factory=list)
    outgoing_flat: list[tuple[str, str, float]] = field(default_factory=list)
    incoming_pierce: float = 0.0  # pierce hits on this one gain (or lose: negative)
    myth_prism: bool = False  # a prism hangs on it turning myth hits into storm (read from the game)
    # Summed percentages of hanging effects, e.g. two +25% blades -> 0.5
    outgoing_boost: float = 0.0  # blades (positive) / weaknesses (negative)
    incoming_boost: float = 0.0  # traps (positive) / shields (negative)
    blade_count: int = 0
    trap_count: int = 0
    shield_count: int = 0
    # Individual hanging effects: (key, school, fraction). key tells copies of the
    # same spell apart from different spells; school "" means every school.
    incoming_effects: list[tuple[str, str, float]] = field(default_factory=list)  # traps/shields
    outgoing_effects: list[tuple[str, str, float]] = field(default_factory=list)  # blades/weaknesses
    # Set on the brain's prism view of an enemy: the enemy as it really is.
    unprismed: Combatant | None = field(default=None, repr=False, compare=False)

    @property
    def health_ratio(self) -> float:
        return self.health / self.max_health if self.max_health else 1.0


@dataclass
class Battle:
    me: Combatant
    allies: list[Combatant]
    enemies: list[Combatant]
    cards: list[Card]
    pips: int = 0
    power_pips: int = 0
    shadow_pips: int = 0  # paid for shadow spells only (Shadow Self Nova)
    # Class (school) pips by school, for the overlay: our own school's are in
    # power_pips (worth 2 for its spells, like power pips), others' in pips.
    school_pips: dict[str, int] = field(default_factory=dict)
    round: int = 0
    prismed: set[str] = field(default_factory=set)  # enemies we already cast a prism on this fight
    summoned: int = 0  # minions we summoned this fight
    upcoming: list[Card] = field(default_factory=list)  # deck cards not drawn/played yet this fight
    deck_known: bool = False  # `upcoming` comes from a read of the deck (else it's just empty)
    immune: set[str] = field(default_factory=set)  # enemies our last big hit barely scratched (held off)

    @property
    def live_enemies(self) -> list[Combatant]:
        return [e for e in self.enemies if not e.is_dead and e.health > 0]

    @property
    def live_allies(self) -> list[Combatant]:
        return [a for a in [self.me, *self.allies] if not a.is_dead]


# The 0-pip wand hit by name too (the player: never played; discarded, it
# clogged the hand three at a time, read as no item card).
NEVER_PLAY = {"ultra assail"}


def normalize_card(card: Card) -> Card:
    """Spells whose effect list, as the game reads, doesn't say what they do
    (the player's words and the card faces), made into what the brain plans
    with. Other cards come back as they are.

    - Shadow Self Nova (Dark Nova in the hand; shadow school, a shadow pip):
      read as OTHER 50 on us for 3 rounds plus a myth pip. It puts a +50%
      trap on every enemy and gives the myth pip.
    - Pierce (Myth, 0 pips): read as OTHER 1 on an enemy; its card says
      "Remove <ward>": one ward (the newest shield) off the target.
    - Potent Trap (Sun enchant): "+10% to spell", onto a trap card."""
    name = f"{card.name} {card.template_name}".lower()
    kinds = card.kinds
    if card.is_shadow and "nova" in name and EffectKind.TRAP not in kinds:
        value = next((e.value for e in card.effects if e.kind is EffectKind.OTHER and e.value > 1), 50.0)
        pips = [e for e in card.effects if e.kind is EffectKind.PIPS] or [
            Effect(EffectKind.PIPS, Target.SELF, 1, school="myth")]
        return _remade(card, [Effect(EffectKind.TRAP, Target.ENEMY_ALL, value), *pips])
    if "pierce" in name and not card.is_enchant and kinds and kinds <= {EffectKind.OTHER}:
        n = next((e.value for e in card.effects if e.kind is EffectKind.OTHER), 1.0) or 1.0
        return _remade(card, [Effect(EffectKind.REMOVE_WARD, Target.ENEMY_SINGLE, n)])
    if "potent trap" in name and EffectKind.ENCHANT_TRAP not in kinds:
        value = next((e.value for e in card.effects if e.value > 0), 10.0)
        return _remade(card, [Effect(EffectKind.ENCHANT_TRAP, Target.SPELL, value)])
    if "sharpened blade" in name and EffectKind.ENCHANT_BLADE not in kinds:
        # (Read as a +10 damage enchant: it would have gone on Orthrus.)
        value = next((e.value for e in card.effects if e.value > 0), 10.0)
        return _remade(card, [Effect(EffectKind.ENCHANT_BLADE, Target.SPELL, value)])
    never = card.item or any(n in name for n in NEVER_PLAY)
    if never and card.is_damage and card.pip_cost == 0 and not card.banned:
        # The wand's 0-pip hit (the player, 2026-10-16: never; Pierce takes a
        # shield off instead).
        from dataclasses import replace

        return replace(card, banned=True)
    return card


def _remade(card: Card, effects: list[Effect]) -> Card:
    from dataclasses import replace

    return replace(card, effects=effects)  # (a new Card: its cached properties start afresh)


def is_blade_card(card: Card) -> bool:
    return EffectKind.BLADE in card.kinds and not card.is_damage and not card.is_enchant


def is_trap_card(card: Card) -> bool:
    return EffectKind.TRAP in card.kinds and not card.is_damage and not card.is_enchant


def with_trap_enchant(card: Card, enchant: Card) -> Card:
    """A trap card with Potent Trap on it: each trap part that much bigger."""
    extra = sum(e.value for e in enchant.effects if e.kind is EffectKind.ENCHANT_TRAP)
    from dataclasses import replace

    effects = [replace(e, value=e.value + extra) if e.kind is EffectKind.TRAP else e for e in card.effects]
    return replace(card, effects=effects, enchanted=True)


class ActionKind(Enum):
    CAST = auto()
    ENCHANT = auto()  # cast `card` onto `target_card` (does not end the turn)
    DISCARD = auto()  # does not end the turn
    PASS = auto()


@dataclass
class Action:
    kind: ActionKind
    card: Card | None = None
    target: Combatant | None = None  # None with CAST = untargeted (AoE, self)
    target_card: Card | None = None
    reason: str = ""
    plan_cards: set[int] = field(default_factory=set)  # hand indices a planned line uses

    def describe(self) -> str:
        if self.kind is ActionKind.PASS:
            return f"pass ({self.reason})"
        name = self.card.name if self.card else "?"
        if self.kind is ActionKind.ENCHANT:
            return f"enchant {self.target_card.name if self.target_card else '?'} with {name}"
        if self.kind is ActionKind.DISCARD:
            return f"discard {name} ({self.reason})"
        on = f" on {self.target.name}" if self.target else ""
        return f"cast {name}{on} ({self.reason})"
