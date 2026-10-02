"""Convert live WizWalker combat objects into the pure model in `model.py`.

The mapping helpers at the top work on enum *names* (strings) so they can be
unit tested without the game or WizWalker installed.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING

from loguru import logger

from ..names import lang_name
from .model import Battle, Card, Combatant, Effect, EffectKind, Target

if TYPE_CHECKING:  # pragma: no cover
    from wizwalker.combat import CombatCard, CombatHandler, CombatMember


# --- pure mapping -----------------------------------------------------------

TARGETS: dict[str, Target] = {
    "enemy_single": Target.ENEMY_SINGLE,
    "preselected_enemy_single": Target.ENEMY_SINGLE,
    "at_least_one_enemy": Target.ENEMY_SINGLE,
    "multi_target_enemy": Target.ENEMY_SINGLE,
    "enemy_team": Target.ENEMY_ALL,
    "enemy_team_all_at_once": Target.ENEMY_ALL,
    "self": Target.SELF,
    "friendly_single": Target.ALLY_SINGLE,
    "friendly_single_not_me": Target.ALLY_SINGLE,
    "friendly_minion": Target.ALLY_SINGLE,
    "multi_target_friendly": Target.ALLY_SINGLE,
    "friendly_team": Target.ALLY_ALL,
    "friendly_team_all_at_once": Target.ALLY_ALL,
    "spell": Target.SPELL,
    "specific_spells": Target.SPELL,
}

_FRIENDLY = {Target.SELF, Target.ALLY_SINGLE, Target.ALLY_ALL}
_ENEMY = {Target.ENEMY_SINGLE, Target.ENEMY_ALL}


def map_target(target_name: str) -> Target:
    return TARGETS.get(target_name, Target.NONE)


def map_effect(effect_name: str, target_name: str, param: float, rounds: int = 0) -> Effect:
    """Classify a single (non-compound) spell effect."""
    target = map_target(target_name)
    kind = EffectKind.OTHER
    value = float(param)

    if target is Target.SPELL:
        if effect_name in ("modify_card_damage", "modify_card_outgoing_damage", "modify_card_damage_by_rank"):
            kind = EffectKind.ENCHANT_DAMAGE
        elif effect_name in ("modify_card_accuracy", "modify_card_outgoing_accuracy"):
            kind = EffectKind.ENCHANT_ACCURACY
    elif effect_name in ("damage", "damage_no_crit", "damage_per_total_pip_power", "max_health_damage"):
        kind = EffectKind.DAMAGE
    elif effect_name == "damage_over_time":
        kind = EffectKind.DOT
    elif effect_name == "steal_health":
        kind = EffectKind.STEAL
    elif effect_name in ("heal", "heal_percent", "set_heal_percent", "heal_by_ward"):
        kind = EffectKind.HEAL
    elif effect_name == "heal_over_time":
        kind = EffectKind.HOT
    elif effect_name in ("modify_outgoing_damage", "modify_outgoing_damage_flat"):
        if target in _FRIENDLY and value > 0:
            kind = EffectKind.BLADE
        elif target in _ENEMY and value < 0:
            kind = EffectKind.WEAKNESS
    elif effect_name in ("modify_incoming_damage", "modify_incoming_damage_flat"):
        if target in _ENEMY and value > 0:
            kind = EffectKind.TRAP
        elif target in _FRIENDLY and value < 0:
            kind = EffectKind.SHIELD
    elif effect_name == "absorb_damage" and target in _FRIENDLY:
        kind = EffectKind.SHIELD
    elif effect_name in ("modify_pips", "modify_power_pips", "modify_school_pips"):
        kind = EffectKind.PIPS
    elif effect_name == "stun":
        kind = EffectKind.STUN
    elif effect_name == "summon_creature":
        kind = EffectKind.SUMMON

    return Effect(kind=kind, target=target, value=value, rounds=rounds)


def average_effects(groups: list[list[Effect]]) -> list[Effect]:
    """A RandomSpellEffect picks one child; plan with the average damage/heal."""
    if not groups:
        return []
    out: list[Effect] = []
    for i, e in enumerate(groups[0]):
        vals = [g[i].value for g in groups if i < len(g)]
        out.append(Effect(e.kind, e.target, sum(vals) / len(vals), e.rounds))
    return out


# --- live readers (Windows / game only) --------------------------------------

_RANDOM = {"RandomSpellEffect", "RandomPerTargetSpellEffect"}
_LIST = {"EffectListSpellEffect", "ShadowSpellEffect", "ShadowPactSpellEffect"}


async def read_effects(effect, depth: int = 0) -> list[Effect]:
    if depth > 4:
        return []
    try:
        type_name = await effect.maybe_read_type_name()
    except Exception:
        type_name = ""

    try:
        if type_name in _RANDOM:
            groups = [await read_effects(c, depth + 1) for c in await effect.maybe_effect_list()]
            return average_effects([g for g in groups if g])
        if type_name == "VariableSpellEffect":
            # Variable effects scale with pips; plan with the strongest branch.
            groups = [await read_effects(c, depth + 1) for c in await effect.maybe_effect_list()]
            groups = [g for g in groups if g]
            return max(groups, key=lambda g: sum(e.value for e in g)) if groups else []
        if type_name in _LIST:
            out: list[Effect] = []
            for c in await effect.maybe_effect_list():
                out.extend(await read_effects(c, depth + 1))
            return out
        if type_name == "ConditionalSpellEffect":
            elements = await effect.elements()
            if elements:
                return await read_effects(await elements[-1].effect(), depth + 1)
            return []
    except Exception as exc:  # compound layout changed after a patch; degrade
        logger.debug(f"compound effect {type_name} unreadable: {exc}")

    effect_type = await effect.effect_type()
    target = await effect.effect_target()
    param = await effect.effect_param()
    rounds = 0
    try:
        rounds = await effect.num_rounds()
    except Exception:
        pass
    mapped = map_effect(effect_type.name, target.name, param, rounds)
    try:
        school = (await effect.string_damage_type() or "").strip().lower()
        mapped.school = "" if school in _ALL_SCHOOLS else school
    except Exception:
        pass
    return [mapped]



SCHOOL_PIP_KINDS = ("balance", "death", "fire", "ice", "life", "myth", "storm")
_school_pips_logged = False


async def school_pips(member) -> int:
    """Class (school) pips: they count like power pips, and the bot didn't
    see them (spells planned a round late). Read beside the generic and
    power pips in the participant's pip count."""
    global _school_pips_logged
    try:
        part = await member.get_participant()
        count = await part.pip_count()
        found = {k: await getattr(count, f"{k}_pips")() for k in SCHOOL_PIP_KINDS}
    except Exception:
        return 0
    total = sum(found.values())
    if total and not _school_pips_logged:
        _school_pips_logged = True
        seen = ", ".join(f"{k} {n}" for k, n in found.items() if n)
        logger.info(f"class pips: {seen} (counted as power pips)")
    return total

async def _is_item(card) -> bool:
    try:
        return bool(await card.is_item_card())
    except Exception:
        return False


async def read_card(index: int, card: CombatCard) -> Card | None:
    try:
        gspell = await card.wait_for_graphical_spell()
        template = await gspell.spell_template()
        name = template_name = await template.name()
        try:
            name = await lang_name(card.combat_handler.client, await card.display_name_code()) or name
        except Exception:
            pass
        effects: list[Effect] = []
        for eff in await gspell.spell_effects():
            effects.extend(await read_effects(eff))
        pip_cost = 0
        try:
            rank = await gspell.pip_cost()
            if rank is not None:
                pip_cost = await rank.spell_rank()
        except Exception:
            pass
        school = ""
        try:
            school = await template.magic_school_name()
        except Exception:
            pass
        return Card(
            index=index,
            name=name,
            school=school,
            pip_cost=pip_cost,
            accuracy=await gspell.accuracy(),
            effects=effects,
            castable=await card.is_castable(),
            enchanted=await card.is_enchanted(),
            treasure=await card.is_treasure_card(),
            item=await _is_item(card),
            template_name=template_name,
        )
    except Exception as exc:
        logger.debug(f"could not read card {index}: {exc}")
        return None


# Order of the per-school stat vectors (dmg_reduce_percent etc.), as used by Deimos.
SCHOOL_ORDER = (
    "fire", "ice", "storm", "myth", "life", "death", "balance", "star", "sun", "moon", "gardening", "shadow",
)
_logged_stats: set[str] = set()


def raw_per_school(values: list[float], all_schools: float = 0.0) -> dict[str, float]:
    """A points stat (flat damage, ratings) per school, as it is."""
    return {s: v + all_schools for s, v in zip(SCHOOL_ORDER, values, strict=False)}


def curve_stat(stat: float, limit: float, k0: float, n0: float) -> float:
    """A player's damage or resist past the game's soft cap, curved the way
    the game does (Deimos's combat_math.curve_stat, by charlied134 and Major;
    GPL-3.0). `stat` and `limit` are fractions, k0 and n0 percent."""
    import math

    if stat <= (k0 + n0) / 100 or limit <= 0:
        return stat
    lim = limit * 100
    k = math.log(lim / (lim - k0)) / k0 if k0 else 1 / lim
    n = math.log(1 - (k0 + n0) / lim) + k * (k0 + n0)
    return limit - limit * math.e ** (-k * stat * 100 + n)


@dataclass
class Curves:
    """The duel's soft caps for players' damage and resist: (limit, k0, n0)."""
    damage: tuple[float, float, float] | None = None
    resist: tuple[float, float, float] | None = None


async def read_curves(handler) -> Curves:
    try:
        duel = handler.client.duel
        return Curves((await duel.damage_limit(), await duel.d_k0(), await duel.d_n0()),
                      (await duel.resist_limit(), await duel.r_k0(), await duel.r_n0()))
    except Exception as exc:
        logger.debug(f"duel curves unreadable: {exc}")
        return Curves()


def per_school(values: list[float], all_schools: float = 0.0) -> dict[str, float]:
    """Map a stat vector onto school names. Stats may be fractions or percents."""
    if any(abs(v) > 1.5 for v in [*values, all_schools]):
        values, all_schools = [v / 100 for v in values], all_schools / 100
    return {s: v + all_schools for s, v in zip(SCHOOL_ORDER, values, strict=False)}


async def _read_school_stats(c: Combatant, participant, curves: Curves | None = None) -> None:
    from wizwalker.memory.memory_objects.enums import MagicSchool

    try:
        c.school = MagicSchool(await participant.primary_magic_school_id()).name
    except Exception:
        pass
    try:
        stats = await participant.game_stats()
        if stats is None:
            return
        reduce = await stats.dmg_reduce_percent()
        c.resist = per_school(reduce, await stats.dmg_reduce_percent_all())
        c.damage_bonus = per_school(await stats.dmg_bonus_percent(), await stats.dmg_bonus_percent_all())
    except Exception as exc:
        logger.debug(f"school stats unreadable for {c.name}: {exc}")
        c.resist = None
        return
    try:  # the rest of the formula (Deimos's combat_math); a fight goes on without it
        c.damage_flat = raw_per_school(await stats.dmg_bonus_flat(), await stats.dmg_bonus_flat_all())
        c.resist_flat = raw_per_school(await stats.dmg_reduce_flat(), await stats.dmg_reduce_flat_all())
        c.pierce = per_school(await stats.ap_bonus_percent(), await stats.ap_bonus_percent_all())
        crit = await stats.critical_hit_rating_by_school()
        c.crit = raw_per_school(crit, await stats.critical_hit_rating_all())
        c.block = raw_per_school(await stats.block_rating_by_school(), await stats.block_rating_all())
        c.level = await stats.reference_level()
        if curves and await participant.is_player():  # players' stats are soft-capped
            if curves.damage:
                c.damage_bonus = {s: curve_stat(v, *curves.damage) for s, v in c.damage_bonus.items()}
            if curves.resist:
                c.resist = {s: curve_stat(v, *curves.resist) for s, v in c.resist.items()}
    except Exception as exc:
        logger.debug(f"extra stats unreadable for {c.name}: {exc}")
    if c.name not in _logged_stats:
        _logged_stats.add(c.name)
        shown = {s: round(v, 2) for s, v in c.resist.items() if v}
        raw = [round(v, 3) for v in reduce]
        logger.info(f"{c.name} ({c.school or '?'}): resists {shown or 'nothing'}; raw {raw}")


_ALL_SCHOOLS = {"", "all", "universal", "none"}


async def _effect_identity(eff, param) -> tuple[str, str]:
    """(key, school) of a hanging effect: copies of one spell share a key; the
    school is lower case, "" when it applies to every school."""
    try:
        school = (await eff.string_damage_type() or "").strip().lower()
    except Exception:
        school = ""
    if school in _ALL_SCHOOLS:
        school = ""
    try:
        tid = await eff.spell_template_id()
    except Exception:
        tid = 0
    return (f"spell:{tid}" if tid else f"{param}:{school}"), school


_last_effects: dict[str, str] = {}


def _log_effects(c: Combatant):
    """Log what hangs on a combatant (traps, shields, blades) when it changes."""
    parts = [f"incoming {k} {s or 'all'} {v:+.0%}" for k, s, v in c.incoming_effects]
    parts += [f"outgoing {k} {s or 'all'} {v:+.0%}" for k, s, v in c.outgoing_effects]
    text = ", ".join(parts) or "none"
    if _last_effects.get(c.name) != text:
        _last_effects[c.name] = text
        logger.info(f"{c.name} effects: {text}")


async def _lasting(c: Combatant, participant):
    """Auras: lasting outgoing boosts (never used up by a hit)."""
    try:
        for eff in await participant.aura_effects():
            if (await eff.effect_type()).name == "modify_outgoing_damage":
                _key, school = await _effect_identity(eff, 0)
                c.aura[school] = c.aura.get(school, 0.0) + await eff.effect_param() / 100
    except Exception as exc:
        logger.debug(f"aura effects unreadable for {c.name}: {exc}")


async def read_combatant(member: CombatMember, my_team: int, curves: Curves | None = None) -> Combatant:
    participant = await member.get_participant()
    team = await participant.team_id()
    c = Combatant(
        name=await member.name(),
        health=await member.health(),
        max_health=await member.max_health(),
        is_client=await member.is_client(),
        is_enemy=team != my_team,
        is_boss=await member.is_boss(),
        is_dead=await member.is_dead(),
    )
    try:
        c.is_minion = await member.is_minion()
    except Exception:
        pass
    try:
        c.is_stunned = await member.is_stunned()
    except Exception:
        pass
    await _read_school_stats(c, participant, curves)
    await _lasting(c, participant)
    try:
        for eff in await participant.hanging_effects():
            et = (await eff.effect_type()).name
            param = await eff.effect_param()
            key, school = await _effect_identity(eff, param)
            if et == "modify_outgoing_damage":
                c.outgoing_boost += param / 100
                c.outgoing_effects.append((key, school, param / 100))
                if param > 0:
                    c.blade_count += 1
            elif et == "modify_incoming_damage":
                c.incoming_boost += param / 100
                c.incoming_effects.append((key, school, param / 100))
                if param > 0:
                    c.trap_count += 1
                else:
                    c.shield_count += 1
            elif et == "intercept":
                c.incoming_effects.append((key, school, param / 100))
            elif et == "absorb_damage":
                c.shield_count += 1
                c.incoming_flat.append((key, school, -abs(param)))
            elif et == "modify_incoming_damage_flat":
                c.incoming_flat.append((key, school, param))
            elif et == "modify_outgoing_damage_flat":
                c.outgoing_flat.append((key, school, param))
            elif et == "modify_incoming_armor_piercing":
                c.incoming_pierce += param / 100
            elif et == "modify_incoming_damage_type" and school == "myth":
                c.myth_prism = True
    except Exception as exc:
        logger.debug(f"hanging effects unreadable for {c.name}: {exc}")
    # One blade spell (Spirit Blade: myth, life and death parts) is one blade,
    # and only the parts that boost our own school count: its life/death
    # leftovers, still hanging after the myth part was used, blocked the
    # second copy for ten rounds against Meowiarty.
    mine = (c.school or "").lower()
    c.blade_count = len({k for k, s, v in c.outgoing_effects if v > 0 and (not s or not mine or s == mine)})
    _log_effects(c)
    return c


async def _global_effect(handler, everyone: list[Combatant]):
    """The battle's bubble (a global effect boosting a school's hits): a
    lasting boost on everyone's hits."""
    try:
        resolver = await handler.client.duel.combat_resolver()
        eff = await resolver.global_effect() if resolver else None
        if eff is None or (await eff.effect_type()).name != "modify_outgoing_damage":
            return
        _key, school = await _effect_identity(eff, 0)
        value = await eff.effect_param() / 100
    except Exception as exc:
        logger.debug(f"global effect unreadable: {exc}")
        return
    logger.debug(f"global effect: {school or 'every school'} {value:+.0%}")
    for c in everyone:
        c.aura[school] = c.aura.get(school, 0.0) + value


@dataclass
class BattleSnapshot:
    battle: Battle
    cards: dict[int, CombatCard] = field(default_factory=dict)  # Card.index -> live card
    members: dict[int, CombatMember] = field(default_factory=dict)  # id(Combatant) -> member


async def read_battle(handler: CombatHandler) -> BattleSnapshot:
    """Snapshot the battle along with handles to the live cards and members."""
    me_member = await handler.get_client_member()
    my_team = await (await me_member.get_participant()).team_id()

    curves = await read_curves(handler)
    me = await read_combatant(me_member, my_team, curves)
    try:
        me.mana = await me_member.mana()
    except Exception:
        pass
    members: dict[int, CombatMember] = {id(me): me_member}
    allies: list[Combatant] = []
    enemies: list[Combatant] = []
    for m in await handler.get_members():
        if await m.is_client():
            continue
        c = await read_combatant(m, my_team, curves)
        members[id(c)] = m
        (enemies if c.is_enemy else allies).append(c)

    cards: list[Card] = []
    card_map: dict[int, CombatCard] = {}
    for i, lc in enumerate(await handler.get_cards()):
        card = await read_card(i, lc)
        if card:
            cards.append(card)
            card_map[i] = lc

    await _global_effect(handler, [me, *allies, *enemies])
    battle = Battle(
        me=me,
        allies=allies,
        enemies=enemies,
        cards=cards,
        pips=await me_member.normal_pips(),
        power_pips=await me_member.power_pips() + await school_pips(me_member),
        round=await handler.round_number(),
    )
    return BattleSnapshot(battle, card_map, members)
