"""Game-agnostic combat data model.

Nothing in this module touches game memory, so the decision logic built on it
can be unit tested anywhere. `reader.py` converts WizWalker objects into these.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum, auto


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

    @property
    def target(self) -> Target:
        # The primary target is the first effect that has one.
        for e in self.effects:
            if e.target is not Target.NONE:
                return e.target
        return Target.NONE

    @property
    def kinds(self) -> set[EffectKind]:
        return {e.kind for e in self.effects}

    @property
    def is_enchant(self) -> bool:
        return self.target is Target.SPELL

    @property
    def is_damage(self) -> bool:
        return bool(self.kinds & DAMAGE_KINDS) and not self.is_enchant

    @property
    def is_aoe(self) -> bool:
        return any(e.target is Target.ENEMY_ALL and e.kind in DAMAGE_KINDS for e in self.effects)

    @property
    def is_heal(self) -> bool:
        return bool(self.kinds & HEAL_KINDS)

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
    damage_bonus: dict[str, float] = field(default_factory=dict)  # outgoing, per school
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
    round: int = 0
    prismed: set[str] = field(default_factory=set)  # enemies we already cast a prism on this fight
    summoned: int = 0  # minions we summoned this fight
    upcoming: list[Card] = field(default_factory=list)  # deck cards not drawn/played yet this fight
    deck_known: bool = False  # `upcoming` comes from a read of the deck (else it's just empty)

    @property
    def live_enemies(self) -> list[Combatant]:
        return [e for e in self.enemies if not e.is_dead and e.health > 0]

    @property
    def live_allies(self) -> list[Combatant]:
        return [a for a in [self.me, *self.allies] if not a.is_dead]


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
