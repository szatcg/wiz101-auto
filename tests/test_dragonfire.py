"""The Dragonfire deck: Shadow Self Nova (shadow pips), Frenzy (a timed
aura), Pierce (removes a ward), damage enchants by value, Potent Trap,
stacked Feints, and the last attack held for a stacked one-shot."""

import asyncio

from test_brain import enemy, me

from wiz101_auto.combat import brain
from wiz101_auto.combat.model import (
    ActionKind,
    Battle,
    Card,
    Effect,
    EffectKind,
    Target,
    normalize_card,
)
from wiz101_auto.combat.reader import map_effect, shadow_pips


def wizard(hp=4035, **kw):
    m = me(hp, 4035, **kw)
    m.school = "myth"
    return m


def orthrus(i=0, dmg=710, enchanted=False, castable=True):
    return Card(i, "Orthrus", school="myth", pip_cost=7, template_id=900, enchanted=enchanted,
                castable=castable, template_name="Orthrus - T02 - A",
                effects=[Effect(EffectKind.DAMAGE, Target.ENEMY_ALL, dmg, school="myth")])


def nova(i, castable=True):
    # As the game reads it (state/spell_cards.pkl): OTHER on us for 3 rounds and a myth pip.
    raw = Card(i, "Dark Nova", school="Shadow", pip_cost=0, template_name="Shadow Self Nova",
               castable=castable,
               effects=[Effect(EffectKind.OTHER, Target.SELF, 50, rounds=3),
                        Effect(EffectKind.PIPS, Target.SELF, 1, school="myth")])
    return normalize_card(raw)


def frenzy(i, castable=True):
    return Card(i, "Frenzy", school="Star", pip_cost=0, template_id=700, castable=castable,
                effects=[Effect(EffectKind.BLADE, Target.SELF, 40, rounds=4),
                         Effect(EffectKind.OTHER, Target.SELF, 30, rounds=4)])


def pierce(i):
    return normalize_card(Card(i, "Pierce", school="Myth", pip_cost=0,
                               effects=[Effect(EffectKind.OTHER, Target.ENEMY_SINGLE, 1)]))


def shatter(i):
    return normalize_card(Card(i, "Shatter", school="Myth", pip_cost=1,
                               effects=[Effect(EffectKind.OTHER, Target.ENEMY_SINGLE, 1)]))


def feint(i, tid, item=False, pips=1):
    return Card(i, "Feint", school="death", pip_cost=pips, template_id=tid, item=item,
                effects=[Effect(EffectKind.TRAP, Target.ENEMY_SINGLE, 70),
                         Effect(EffectKind.OTHER, Target.SELF, 30)])


def mythblade(i, tid=30):
    return Card(i, "Mythblade", school="myth", pip_cost=0, template_id=tid,
                effects=[Effect(EffectKind.BLADE, Target.ALLY_SINGLE, 35, school="myth")])


def enchant(i, name, value):
    return Card(i, name, school="sun", pip_cost=0,
                effects=[Effect(EffectKind.ENCHANT_DAMAGE, Target.SPELL, value)])


def fight(cards, enemies, my=None, pips=0, power=0, shadow=0, upcoming=None):
    b = Battle(me=my or wizard(), allies=[], enemies=enemies, cards=cards, pips=pips, power_pips=power,
               shadow_pips=shadow)
    b.upcoming = list(upcoming or [])
    b.deck_known = upcoming is not None
    return b


# --- reading -----------------------------------------------------------------

def test_shadow_pips_are_read_from_the_member():
    class Member:
        async def shadow_pips(self):
            return 2

    class Broken:
        async def shadow_pips(self):
            raise ValueError("no participant")

    assert asyncio.run(shadow_pips(Member())) == 2
    assert asyncio.run(shadow_pips(Broken())) == 0


def test_the_nova_pierce_and_potent_trap_read_as_what_they_do():
    n = nova(0)
    assert n.is_shadow and n.target is Target.ENEMY_ALL
    assert [(e.kind, e.value) for e in n.effects] == [(EffectKind.TRAP, 50), (EffectKind.PIPS, 1)]
    assert n.pip_gain() == ("myth", 1)
    p = pierce(1)
    assert p.kinds == {EffectKind.REMOVE_WARD} and p.target is Target.ENEMY_SINGLE
    assert map_effect("remove_ward", "enemy_single", 1).kind is EffectKind.REMOVE_WARD
    potent = normalize_card(Card(2, "Potent Trap", school="sun", effects=[
        Effect(EffectKind.ENCHANT_DAMAGE, Target.SPELL, 10)]))
    assert potent.kinds == {EffectKind.ENCHANT_TRAP} and potent.is_enchant
    plain = feint(3, 10)
    assert normalize_card(plain) is plain


# --- Shadow Self Nova ----------------------------------------------------------

def test_the_nova_is_castable_only_with_a_shadow_pip():
    boss = enemy("Boss", 15000, boss=True)
    b = fight([orthrus(0, castable=False), nova(1)], [boss], pips=2)
    brain.card_rules(b)
    assert not b.cards[1].castable
    a = brain.decide(b)
    assert a.card is None or not a.card.is_shadow
    b = fight([orthrus(0, castable=False), nova(1)], [boss], pips=2, shadow=1)
    a = brain.decide(b)
    assert a.kind is ActionKind.CAST and a.card.is_shadow and a.target is None


def test_the_nova_goes_before_other_setup():
    boss = enemy("Boss", 15000, boss=True)
    b = fight([orthrus(0, castable=False), mythblade(1), feint(2, 10), nova(3)], [boss], pips=3, shadow=1)
    a = brain.decide(b)
    assert a.card is not None and a.card.is_shadow


def test_the_search_counts_the_novas_traps_and_pip():
    # 6 pips: Orthrus (7) is a pip short; the Nova's myth pip and +50% trap
    # make it castable next round and the kill: 2 rounds.
    boss = enemy("Boss", 1500, boss=True)
    o = orthrus(0, dmg=1100)
    b = fight([o, nova(1)], [boss], pips=4, power=1, shadow=1)
    found = brain._kill_search(b, boss, 3)
    assert found is not None and found[0] == 2 and "Dark Nova" in found[3][0]
    b0 = fight([o, nova(1)], [boss], pips=4, power=1, shadow=0)  # no shadow pip: no Nova in the plan
    found0 = brain._kill_search(b0, boss, 3)
    assert found0 is None or "Dark Nova" not in " ".join(found0[3])


# --- Frenzy ----------------------------------------------------------------------

def test_frenzy_only_when_the_big_hit_comes_within_its_rounds():
    boss = enemy("Boss", 15000, boss=True)
    far = fight([orthrus(0, castable=False), frenzy(1)], [boss], pips=0)  # Orthrus 7 rounds away
    brain.card_rules(far)
    assert not far.cards[1].castable
    near = fight([orthrus(0, castable=False), frenzy(1)], [boss], pips=5)  # 2 rounds away
    brain.card_rules(near)
    assert near.cards[1].castable


def test_frenzy_lasts_its_rounds_and_is_not_used_up_by_a_hit():
    f = frenzy(0)
    fx = brain.timed_fx(f, 2)  # cast in round 2 (depth): good through depth 6
    assert brain._use_up(fx, "myth") == fx
    assert brain.expire(fx, 6) == fx and brain.expire(fx, 7) == []


def test_the_search_casts_frenzy_right_before_the_hit():
    boss = enemy("Boss", 1300, boss=True)
    # 1000 x1.4 kills; Orthrus castable next round
    b = fight([orthrus(0, dmg=1000), frenzy(1)], [boss], pips=6)
    found = brain._kill_search(b, boss, 3)
    assert found is not None and found[3][:2] == ["Frenzy", "Orthrus (~1400)"]


# --- Pierce -------------------------------------------------------------------------

def test_pierce_only_when_a_shield_keeps_the_kill_out_of_reach():
    o = orthrus(0, dmg=1500)
    shielded = enemy("Boss", 1400, boss=True)
    shielded.incoming_effects = [("spell:5", "myth", -0.5)]  # a Myth Shield: 750 < 1400
    b = fight([o, pierce(1)], [shielded], pips=7)
    assert brain.pierce_target(b) is shielded
    a = brain.decide(b)
    assert a.card is not None and EffectKind.REMOVE_WARD in a.card.kinds and a.target is shielded
    # A fire shield on top: Pierce would take that one, not the one in the way.
    fire = enemy("Boss", 1400, boss=True)
    fire.incoming_effects = [("spell:6", "fire", -0.5), ("spell:5", "myth", -0.5)]
    assert brain.pierce_target(fight([o, pierce(1)], [fire], pips=7)) is None
    # No shield, or one the hit kills through anyway: never.
    bare = enemy("Boss", 1400, boss=True)
    b2 = fight([o, pierce(1)], [bare], pips=7)
    brain.card_rules(b2)
    assert not b2.cards[1].castable
    weak = enemy("Boss", 600, boss=True)
    weak.incoming_effects = [("spell:5", "", -0.5)]
    assert brain.pierce_target(fight([o, pierce(1)], [weak], pips=7)) is None


def test_shatter_takes_every_shield_when_they_keep_orthrus_from_killing():
    # The player: Shatter clears all the enemy's beneficial wards; Pierce one.
    # A Myth Shield and a universal shield: Orthrus's 1500 does 450 < 1400.
    o = orthrus(0, dmg=1500)
    boss = enemy("Boss", 1400, boss=True)
    boss.incoming_effects = [("spell:7", "", -0.4), ("spell:5", "myth", -0.5)]
    assert brain.pierce_target(fight([o, pierce(1)], [boss], pips=7)) is None  # one off: 900, short
    b = fight([o, shatter(1)], [boss], pips=7)
    assert brain.pierce_target(b, b.cards[1]) is boss
    a = brain.decide(b)
    assert a.card is not None and a.card.name == "Shatter" and a.target is boss
    # Shields Orthrus kills through anyway: no Shatter.
    weak = enemy("Boss", 300, boss=True)
    weak.incoming_effects = [("spell:7", "", -0.4), ("spell:5", "myth", -0.5)]
    b2 = fight([o, shatter(1)], [weak], pips=7)
    brain.card_rules(b2)
    assert not b2.cards[1].castable


# --- enchants ---------------------------------------------------------------------------

def test_the_best_damage_enchant_by_value_goes_on_orthrus():
    o = orthrus(0, castable=False)
    cards = [o, enchant(1, "Gargantuan", 225), enchant(2, "Epic", 300), enchant(3, "Colossal", 275)]
    b = fight(cards, [enemy("Boss", 15000, boss=True)])
    a = brain.decide(b)
    assert a.kind is ActionKind.ENCHANT and a.card.name == "Epic" and a.target_card is o
    o2 = orthrus(0)
    o2.name = "Orthrus - T02 - A"  # (the template name shown as the card's)
    b2 = fight([o2, enchant(1, "Colossal", 275)], [enemy("Boss", 15000, boss=True)])
    a2 = brain._enchant_early(b2)
    assert a2 is not None and a2.card.name == "Colossal"


def test_potent_trap_goes_on_a_feint_that_then_stacks_with_the_plain_one():
    potent = normalize_card(Card(5, "Potent Trap", school="sun", effects=[
        Effect(EffectKind.ENCHANT_DAMAGE, Target.SPELL, 10)]))
    f1, f2 = feint(1, 10), feint(2, 10)
    b = fight([orthrus(0, castable=False), f1, f2, potent], [enemy("Boss", 15000, boss=True)], pips=2)
    a = brain.decide(b)
    assert a.kind is ActionKind.ENCHANT and a.card is potent and a.target_card.name == "Feint"
    from wiz101_auto.combat.model import with_trap_enchant

    strong = with_trap_enchant(f2, potent)
    assert strong.effects[0].value == 80 and strong.enchanted
    up = [("spell:10", "", 0.7)]  # the plain trained Feint hangs
    assert brain._is_duplicate(f1, EffectKind.TRAP, up, "myth")
    assert not brain._is_duplicate(strong, EffectKind.TRAP, up, "myth")
    assert brain.fx_id(strong) != brain.fx_id(f1)


# --- Feints and the stacked one-shot ---------------------------------------------------

def test_every_feint_goes_on_the_boss_before_orthrus():
    boss = enemy("Boss", 6000, boss=True)
    o = orthrus(0, dmg=1300)
    cards = [o, feint(1, 10), feint(2, 20, item=True), feint(3, 30, item=True)]
    b = fight(cards, [boss], pips=7, upcoming=[])
    cast = []
    for _ in range(6):  # play the brain's moves out: traps land, a pip a round
        a = brain.decide(b)
        if a.kind is ActionKind.CAST and a.card is o:
            break
        assert a.kind is ActionKind.CAST and a.card.name == "Feint", a.describe()
        cast.append(a.card.template_id)
        boss.incoming_effects = boss.incoming_effects + [(f"spell:{a.card.template_id}", "", 0.7)]
        b.cards = [c for c in b.cards if c is not a.card]
        b.pips += 1
    assert sorted(cast) == [10, 20, 30]
    assert brain.hit_damage(o, b.me, boss) >= boss.health


def test_the_last_attack_waits_for_the_stack_that_kills_everyone():
    # Clemente Moraga and the Ortiz brothers: one Orthrus in the deck (with
    # Colossal on it) killed only the brothers; no attack was left.
    boss = enemy("Clemente Moraga", 15000, boss=True)
    adds = [enemy("Primo Ortiz", 2840), enemy("Segundo Ortiz", 2840)]
    my = wizard(2558, damage_bonus={"myth": 1.5})
    my.outgoing_effects = [("spell:30", "myth", 0.35), ("spell:31", "myth", 0.35)]
    o = orthrus(0, dmg=985, enchanted=True)
    hand = [o, feint(1, 10), feint(2, 20, item=True), feint(3, 40, item=True), frenzy(4),
            Card(5, "Spirit Blade", school="balance", pip_cost=1, template_id=50,
                 effects=[Effect(EffectKind.BLADE, Target.ALLY_SINGLE, 35, school="myth")])]
    b = fight(hand, [boss, *adds], my=my, pips=0, power=4, upcoming=[])
    assert brain.must_hold(b, o)
    a = brain.decide(b)
    assert not (a.kind is ActionKind.CAST and a.card is o), a.describe()
    assert a.kind is ActionKind.CAST and a.card.name in ("Feint", "Spirit Blade"), a.describe()
    shot = brain.one_shot_plan(b, o)
    assert shot.kills and shot.rounds < 99 and shot.steps[-1].startswith("Orthrus")
    assert "one shot" in brain.plan_fight(b).text
    # Stacked enough to kill them all: the hit goes.
    for e in (boss, *adds):
        e.incoming_effects = [("spell:10", "", 0.7), ("spell:20", "", 0.7), ("spell:40", "", 0.7)]
    a = brain.decide(b)
    assert a.kind is ActionKind.CAST and a.card is o


def test_a_hit_that_isnt_the_last_one_is_not_held():
    boss = enemy("Boss", 15000, boss=True)
    adds = [enemy("Add", 2000), enemy("Add 2", 2000)]
    o = orthrus(0, dmg=2500)
    b = fight([o, feint(1, 10)], [boss, *adds], pips=7,
              upcoming=[orthrus(10 + k, dmg=2500) for k in range(6)])
    assert not brain.must_hold(b, o)
    b.deck_known = False
    assert not brain.must_hold(b, o)  # (the deck unknown: never)


def test_sharpened_blade_goes_on_the_trained_mythblade_never_an_item_card():
    sharp = normalize_card(Card(5, "Sharpened Blade", school="sun", effects=[
        Effect(EffectKind.ENCHANT_DAMAGE, Target.SPELL, 10)]))
    assert EffectKind.ENCHANT_BLADE in sharp.kinds and EffectKind.ENCHANT_DAMAGE not in sharp.kinds
    pet = mythblade(1, tid=31)
    pet.item = True
    trained = mythblade(2, tid=30)
    b = fight([orthrus(0, castable=False), pet, trained, sharp], [enemy("Boss", 15000, boss=True)], pips=2)
    a = brain._enchant_early(b)
    assert a.kind is ActionKind.ENCHANT and a.card is sharp and a.target_card is trained
    only_item = fight([orthrus(0, castable=False), pet, sharp], [enemy("Boss", 15000, boss=True)], pips=2)
    assert brain._blade_enchant(only_item) is None


def test_potent_trap_never_on_an_item_feint():
    potent = normalize_card(Card(5, "Potent Trap", school="sun", effects=[
        Effect(EffectKind.ENCHANT_DAMAGE, Target.SPELL, 10)]))
    boss = enemy("Boss", 15000, boss=True)
    b = fight([orthrus(0, castable=False), feint(1, 20, item=True), potent], [boss], pips=2)
    assert brain._trap_enchant(b) is None


def test_the_wands_zero_pip_hit_is_banned():
    wand = normalize_card(Card(3, "Minor Scorch", school="fire", pip_cost=0, item=True,
                               effects=[Effect(EffectKind.DAMAGE, Target.ENEMY_SINGLE, 70)]))
    assert wand.banned


def test_sharpened_blade_never_on_frenzy():
    sharp = normalize_card(Card(5, "Sharpened Blade", school="sun", effects=[
        Effect(EffectKind.ENCHANT_DAMAGE, Target.SPELL, 10)]))
    frenzy = Card(1, "Frenzy", school="myth", pip_cost=0, template_id=40,
                  effects=[Effect(EffectKind.BLADE, Target.SELF, 40, school="myth")])
    b = fight([orthrus(0, castable=False), frenzy, sharp], [enemy("Boss", 15000, boss=True)], pips=2)
    assert brain._blade_enchant(b) is None
