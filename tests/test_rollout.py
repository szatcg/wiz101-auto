

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
