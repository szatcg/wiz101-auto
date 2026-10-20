from wiz101_auto.combat.brain import decide
from wiz101_auto.combat.model import ActionKind, Battle, Card, Combatant, Effect, EffectKind, Target


def hand():
    hit = Card(0, "Orthrus", pip_cost=3, school="myth",
               effects=[Effect(EffectKind.DAMAGE, Target.ENEMY_SINGLE, 800)])
    blade = Card(1, "Mythblade", pip_cost=0, school="myth",
                 effects=[Effect(EffectKind.BLADE, Target.ALLY_SINGLE, 35)])
    return [hit, blade]


def fight(name, rnd, immune=(), hp=9200):
    me = Combatant("Me", 6000, 6000, is_client=True, school="myth")
    foe = Combatant(name, hp, 9200, is_enemy=True, is_boss=True)
    return Battle(me=me, allies=[], enemies=[foe], cards=hand(), pips=5, round=rnd, immune=set(immune))


def no_hit(action):
    return not (action.kind is ActionKind.CAST and action.card is not None and action.card.is_damage)


def test_the_bat_wants_shadow_creatures():
    b = fight("The Bat", 2, hp=9200)
    assert decide(b).kind is ActionKind.PASS
    sc = Card(2, "Shadow Creature Big Bomb", pip_cost=2, school="shadow",
              effects=[Effect(EffectKind.DAMAGE, Target.ENEMY_SINGLE, 900)])
    b.cards.append(sc)
    a = decide(b)
    assert a.kind is ActionKind.CAST and a.card.name == "Shadow Creature Big Bomb"


def test_immune_enemy_not_hit():
    assert no_hit(decide(fight("Some Boss", 6, immune={"Some Boss"}, hp=500)))


def test_others_hit_as_usual():
    a = decide(fight("Some Boss", 6, hp=500))
    assert a.kind is ActionKind.CAST and a.card.name == "Orthrus"


def test_paradox_casts_scions():
    b = fight("Aethyr Titan", 30, hp=500000)
    b.cards = [Card(0, "Scion of Elements", pip_cost=0, effects=[]),
               Card(1, "Scion of Spirits", pip_cost=0, effects=[])]
    a = decide(b)
    assert a.kind is ActionKind.CAST and a.card.name.startswith("Scion")
