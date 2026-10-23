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


def feint():
    return Card(5, "Feint", pip_cost=1, school="myth",
                effects=[Effect(EffectKind.TRAP, Target.ENEMY_SINGLE, 70)])


def test_snow_queen_no_feint():
    b = fight("Snow Queen", rnd=2)
    b.cards.append(feint())
    a = decide(b)
    assert not (a.card is not None and a.card.name == "Feint")


def test_snow_queen_round_one_hit():
    b = fight("Snow Queen", rnd=1, pips=2)
    a = decide(b)
    assert a.kind is ActionKind.CAST and a.card is not None and a.card.is_damage


def test_malughast_no_feint():
    b = fight("The Malughast", rnd=2)
    b.cards.append(feint())
    a = decide(b)
    assert not (a.card is not None and a.card.name == "Feint")


def test_malughast_no_malughasts_feint_either():
    b = fight("The Malughast", rnd=2)
    b.cards.append(Card(6, "Malughast's Feint", pip_cost=0, school="shadow",
                        effects=[Effect(EffectKind.TRAP, Target.ENEMY_SINGLE, 70)]))
    a = decide(b)
    assert not (a.card is not None and a.card.name == "Malughast's Feint")
