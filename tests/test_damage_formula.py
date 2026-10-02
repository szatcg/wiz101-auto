"""The full damage formula (ported from Deimos's combat_math)."""

import pytest

from wiz101_auto.combat.brain import crit_chance, hit_damage
from wiz101_auto.combat.model import Card, Combatant, Effect, EffectKind, Target


def card(dmg=1000, school="myth"):
    return Card(0, "Hit", school=school, effects=[Effect(EffectKind.DAMAGE, Target.ENEMY_SINGLE, dmg)])


def me(**kw):
    return Combatant("me", 2000, 2000, is_client=True, **kw)


def foe(**kw):
    kw.setdefault("resist", {})
    return Combatant("foe", 5000, 5000, is_enemy=True, **kw)


def test_pierce_breaks_a_shield_and_is_used_up():
    shield = [("spell:1", "myth", -0.30)]
    # 20% pierce: the 30% shield cuts only 10%, and no pierce is left for resist.
    got = hit_damage(card(), me(pierce={"myth": 0.20}), foe(incoming_effects=shield, resist={"myth": 0.2}))
    assert got == pytest.approx(1000 * 0.9 * 0.8)


def test_pierce_left_over_cuts_resist():
    got = hit_damage(card(), me(pierce={"myth": 0.15}), foe(resist={"myth": 0.25}))
    assert got == pytest.approx(1000 * 0.9)
    # more pierce than resist: no resist at all, never a boost
    assert hit_damage(card(), me(pierce={"myth": 0.5}), foe(resist={"myth": 0.25})) == pytest.approx(1000)


def test_flat_damage_and_flat_resist():
    got = hit_damage(card(), me(damage_flat={"myth": 50}), foe(resist_flat={"myth": 30}))
    assert got == pytest.approx(1020)


def test_absorb_and_flat_blade():
    got = hit_damage(card(), me(outgoing_flat=[("b", "", 100)]), foe(incoming_flat=[("a", "", -300)]))
    assert got == pytest.approx(800)


def test_universal_aura_and_negative_resist_boost():
    got = hit_damage(card(), me(aura={"": 0.25}), foe(resist={"myth": -0.5}))
    assert got == pytest.approx(1000 * 1.25 * 1.5)


def test_crit_only_when_near_sure():
    attacker = me(level=45, crit={"myth": 500})
    assert crit_chance(attacker, foe(), "myth") == pytest.approx(0.45)
    assert hit_damage(card(), attacker, foe()) == pytest.approx(1000)  # 45%: not counted on
    sure = me(level=100, crit={"myth": 1000})
    assert crit_chance(sure, foe(), "myth") >= 0.85
    assert hit_damage(card(), sure, foe()) == pytest.approx(2000)  # no block: double


def test_unchanged_without_the_new_stats():
    target = foe(resist={"myth": 0.1}, incoming_effects=[("t", "myth", 0.3)])
    attacker = me(damage_bonus={"myth": 0.4}, outgoing_effects=[("b", "myth", 0.35)])
    assert hit_damage(card(), attacker, target) == pytest.approx(1000 * 1.4 * 1.35 * 1.3 * 0.9)


def test_curve_stat_soft_caps_past_the_knee():
    from wiz101_auto.combat.reader import curve_stat

    assert curve_stat(0.30, 1.5, 50, 10) == 0.30  # below (k0 + n0)%: as it is
    high = curve_stat(1.40, 1.5, 50, 10)
    assert 0.60 < high < 1.40  # past the knee: pulled down, under the limit


def test_raw_per_school_keeps_points():
    from wiz101_auto.combat.reader import raw_per_school

    got = raw_per_school([10, 0, 0, 25], 5)
    assert got["fire"] == 15 and got["myth"] == 30
