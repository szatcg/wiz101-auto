

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


def test_plan_discard_bins_what_the_plan_skips():
    from wiz101_auto.combat.brain import _plan_discard, plan_hand_use
    from wiz101_auto.combat.model import ActionKind, Battle, Card, Combatant, Effect, EffectKind, Target

    def hit(i, name, cost, dmg):
        fx = [Effect(EffectKind.DAMAGE, Target.ENEMY_SINGLE, dmg)]
        return Card(i, name, school="myth", pip_cost=cost, effects=fx)

    heal = [Effect(EffectKind.HEAL, Target.ALLY_SINGLE, 400)]
    cards = [hit(0, "Colossus", 4, 600), hit(1, "Minor Scorch", 0, 60),
             Card(2, "Pixie", school="life", pip_cost=2, effects=heal)]
    me = Combatant("me", 2000, 2000, is_client=True, school="myth")
    boss = Combatant("Boss", 1100, 5000, is_enemy=True, is_boss=True, resist={})
    blade = Card(9, "Mythblade", school="myth", effects=[Effect(EffectKind.BLADE, Target.SELF, 100)])
    b = Battle(me=me, allies=[], enemies=[boss], cards=cards, pips=4, deck_known=True,
               upcoming=[blade, hit(10, "Colossus", 4, 600)])
    rounds, used = plan_hand_use(b)
    assert 0 in used  # Colossus is the plan
    got = _plan_discard(b)
    if got is not None:  # (only when a draw shortens the win)
        assert got.kind is ActionKind.DISCARD and got.card.index not in used and got.card.name != "Pixie"
