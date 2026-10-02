

def test_no_rollout_heal_while_well():
    from wiz101_auto.combat.model import (
        Action,
        ActionKind,
        Battle,
        Card,
        Combatant,
        Effect,
        EffectKind,
        Target,
    )
    from wiz101_auto.combat.rollout import _early_heal

    heal_fx = [Effect(EffectKind.HEAL, Target.ALLY_SINGLE, 400)]
    pixie = Card(0, "Pixie", school="life", pip_cost=2, effects=heal_fx)
    stats = {"enemies": {"Boris": {"max_health": 7000, "boss": True, "alone": [900, 700, 0, 800, 650, 0],
                                   "shared": [], "school": "ice"}}}
    boss = Combatant("Boris", 1800, 7000, is_enemy=True, is_boss=True)
    heal = Action(ActionKind.CAST, pixie)
    well = Combatant("me", 1364, 2189, is_client=True)
    assert _early_heal(heal, Battle(me=well, allies=[], enemies=[boss], cards=[pixie]), stats)
    low = Combatant("me", 1000, 2189, is_client=True)  # under half
    assert not _early_heal(heal, Battle(me=low, allies=[], enemies=[boss], cards=[pixie]), stats)
    near = Combatant("me", 1150, 2189, is_client=True)  # his worst hit (900)... not enough to kill
    assert _early_heal(heal, Battle(me=near, allies=[], enemies=[boss], cards=[pixie]), stats)


def test_dig_candidate_keeps_the_plan_cards():
    from wiz101_auto.combat import sim
    from wiz101_auto.combat.model import ActionKind, Battle, Card, Combatant, Effect, EffectKind, Target

    hit = [Effect(EffectKind.DAMAGE, Target.ENEMY_SINGLE, 700)]
    heal = [Effect(EffectKind.HEAL, Target.ALLY_SINGLE, 400)]
    cards = [Card(0, "Colossus", school="myth", pip_cost=5, effects=hit),
             Card(1, "Pixie", school="life", pip_cost=2, effects=heal),
             Card(2, "Pixie", school="life", pip_cost=2, effects=heal),
             Card(3, "Ether Shield", school="myth", pip_cost=0,
                  effects=[Effect(EffectKind.SHIELD, Target.SELF, -40)])]
    me = Combatant("me", 2000, 2000, is_client=True, school="myth")
    boss = Combatant("Boss", 300, 5000, is_enemy=True, is_boss=True, resist={})
    blade = Card(9, "Mythblade", school="myth", effects=[Effect(EffectKind.BLADE, Target.SELF, 35)])
    b = Battle(me=me, allies=[], enemies=[boss], cards=cards, pips=1, deck_known=True, upcoming=[blade])
    dig = sim.dig_action(b, discards=4)
    assert dig is not None and dig.kind is ActionKind.PASS
    assert dig.plan_cards == {2, 3}  # a spare Pixie and the shield; Colossus and one Pixie stay
