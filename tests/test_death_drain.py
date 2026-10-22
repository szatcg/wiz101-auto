from wiz101_auto.combat.brain import decide
from wiz101_auto.combat.model import ActionKind, Battle, Card, Combatant, Effect, EffectKind, Target


def cards():
    single = Card(0, "Orthrus", pip_cost=3, school="myth",
                  effects=[Effect(EffectKind.DAMAGE, Target.ENEMY_SINGLE, 800)])
    aoe = Card(1, "Humongofrog", pip_cost=4, school="myth",
               effects=[Effect(EffectKind.DAMAGE, Target.ENEMY_ALL, 700)])
    return [single, aoe]


def fight(names, hp=6000):
    me = Combatant("Me", hp, 6000, is_client=True, school="myth")
    foes = [Combatant(n, 500, 3900, is_enemy=True) for n in names]
    return Battle(me=me, allies=[], enemies=foes, cards=cards(), pips=5, round=3)


def cast(a):
    return a.card.name if a.kind is ActionKind.CAST and a.card is not None else None


def test_no_aoe_on_two_oogie_boogies():
    assert cast(decide(fight(["Oogie Boogie", "Oogie Boogie"]))) != "Humongofrog"


def test_no_kill_when_a_drain_would_finish_us():
    a = decide(fight(["Oogie Boogie"], hp=1500))
    assert not (a.kind is ActionKind.CAST and a.card is not None and a.card.is_damage)


def test_respawn_hit_when_low():
    a = decide(fight(["Oogie Boogie", "Elite Ice Minion"], hp=1500))
    assert cast(a) in (None, "Orthrus")


def test_no_aoe_on_the_last_oogie_boogie_either():
    assert cast(decide(fight(["Oogie Boogie", "Elite Ice Minion"]))) != "Humongofrog"
