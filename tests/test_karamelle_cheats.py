from wiz101_auto.combat.brain import decide
from wiz101_auto.combat.model import ActionKind, Battle, Card, Combatant, Effect, EffectKind, Target


def cards(pips_hit=5):
    big = Card(0, "Orthrus", pip_cost=pips_hit, school="myth",
               effects=[Effect(EffectKind.DAMAGE, Target.ENEMY_SINGLE, 1500)])
    small = Card(1, "Myth Minotaur", pip_cost=2, school="myth",
                 effects=[Effect(EffectKind.DAMAGE, Target.ENEMY_SINGLE, 400)])
    blade = Card(2, "Mythblade", pip_cost=0, school="myth",
                 effects=[Effect(EffectKind.BLADE, Target.ALLY_SINGLE, 35)])
    return [big, small, blade]


def fight(name, rnd=2, pips=2, hp=9000):
    me = Combatant("Me", 6000, 6000, is_client=True, school="myth")
    foe = Combatant(name, hp, 9000, is_enemy=True, is_boss=True)
    return Battle(me=me, allies=[], enemies=[foe], cards=cards(), pips=pips, round=rnd)


def test_treemugger_no_saving():
    a = decide(fight("Treemugger"))
    assert not (a.kind is ActionKind.PASS and "saving" in (a.reason or ""))


def test_quake_charmer_no_blade_in_round_1():
    a = decide(fight("The Quake Charmer", rnd=1))
    assert not (a.card is not None and a.card.name == "Mythblade")


def test_bundozer_no_blade():
    a = decide(fight("The Bundozer", rnd=3))
    assert not (a.card is not None and a.card.name == "Mythblade")
