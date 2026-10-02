

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


def test_no_plan_discard_when_the_kill_is_in_hand():
    from wiz101_auto.combat.brain import _plan_discard
    from wiz101_auto.combat.model import Battle, Card, Combatant, Effect, EffectKind, Target

    def hit(i, name, cost, dmg):
        fx = [Effect(EffectKind.DAMAGE, Target.ENEMY_SINGLE, dmg)]
        return Card(i, name, school="myth", pip_cost=cost, effects=fx)

    trap = Card(1, "Myth Trap", school="myth", effects=[Effect(EffectKind.TRAP, Target.ENEMY_SINGLE, 30)])
    cards = [hit(0, "Colossus", 4, 900), trap, hit(2, "Minor Scorch", 0, 60)]
    me = Combatant("me", 2000, 2000, is_client=True, school="myth")
    troll = Combatant("Troll", 700, 765, is_enemy=True, resist={})
    b = Battle(me=me, allies=[], enemies=[troll], cards=cards, pips=4, deck_known=True,
               upcoming=[hit(10, "Colossus", 4, 900)])
    assert _plan_discard(b) is None


def test_blades_traps_and_big_hits_are_never_discarded_for_a_draw():
    from wiz101_auto.combat.brain import keep_from_discard
    from wiz101_auto.combat.model import Card, Effect, EffectKind, Target

    def card(i, name, pips, kind, target, value):
        return Card(i, name, school="myth", pip_cost=pips, effects=[Effect(kind, target, value)])

    blade = card(0, "Spirit Blade", 1, EffectKind.BLADE, Target.SELF, 35)
    feint = card(1, "Feint", 1, EffectKind.TRAP, Target.ENEMY_SINGLE, 70)
    colossus = card(2, "Stone Colossus", 3, EffectKind.DAMAGE, Target.ENEMY_SINGLE, 600)
    pixie = card(3, "Pixie", 2, EffectKind.HEAL, Target.ALLY_SINGLE, 400)
    assert keep_from_discard(blade) and keep_from_discard(feint) and keep_from_discard(colossus)
    assert not keep_from_discard(pixie)


def test_a_draw_only_counts_if_the_shorter_plan_plays_it():
    from wiz101_auto.combat.brain import improving_draws
    from wiz101_auto.combat.model import Battle, Card, Combatant, Effect, EffectKind, Target

    def card(i, name, pips, kind, target, value):
        return Card(i, name, school="myth", pip_cost=pips, effects=[Effect(kind, target, value)])

    me = Combatant("me", 2000, 2000, is_client=True, school="myth")
    boss = Combatant("Boss", 1500, 5000, is_enemy=True, is_boss=True, resist={})
    hit = card(0, "Colossus", 3, EffectKind.DAMAGE, Target.ENEMY_SINGLE, 600)
    deck = [card(10, "Pixie", 2, EffectKind.HEAL, Target.ALLY_SINGLE, 400),
            card(11, "Colossus", 3, EffectKind.DAMAGE, Target.ENEMY_SINGLE, 600)]
    b = Battle(me=me, allies=[], enemies=[boss], cards=[hit], pips=3, deck_known=True, upcoming=deck)
    _base, better = improving_draws(b)
    assert "Pixie" not in better  # a heal shortens no fight


def test_heal_waits_when_power_pips_would_pay_for_an_off_school_heal():
    from wiz101_auto.combat.brain import Strategy, _best_heal
    from wiz101_auto.combat.model import Battle, Card, Combatant, Effect, EffectKind, Target

    pixie = Card(0, "Pixie", school="life", pip_cost=2, effects=[Effect(EffectKind.HEAL, Target.SELF, 400)])
    boss = Combatant("Gurtok", 5000, 5600, is_enemy=True, is_boss=True, resist={})
    me = Combatant("me", 926, 2380, is_client=True, school="myth")  # 39%: under the boss heal line
    with_power = Battle(me=me, allies=[], enemies=[boss], cards=[pixie], pips=0, power_pips=2)
    assert _best_heal(with_power, Strategy()) is None  # 2 power pips = 4 of Orthrus's 7
    plain = Battle(me=me, allies=[], enemies=[boss], cards=[pixie], pips=2, power_pips=0)
    assert _best_heal(plain, Strategy()) is not None  # plain pips: heal as before
    low = Combatant("me", 600, 2380, is_client=True, school="myth")  # 25%: under the floor
    assert _best_heal(Battle(me=low, allies=[], enemies=[boss], cards=[pixie], pips=0, power_pips=2),
                      Strategy()) is not None


def test_a_boss_too_big_to_kill_gets_feint_before_orthrus():
    from wiz101_auto.combat.brain import _boss_setup_first
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

    orthrus = Card(0, "Orthrus", school="myth", pip_cost=7,
                   effects=[Effect(EffectKind.DAMAGE, Target.ENEMY_ALL, 700)])
    feint = Card(1, "Feint", school="death", pip_cost=1,
                 effects=[Effect(EffectKind.TRAP, Target.ENEMY_SINGLE, 70)])
    me = Combatant("me", 2000, 2380, is_client=True, school="myth")
    boss = Combatant("Gurtok", 5600, 5600, is_enemy=True, is_boss=True, resist={})
    b = Battle(me=me, allies=[], enemies=[boss], cards=[orthrus, feint], pips=1, power_pips=3)
    got = _boss_setup_first(b, Action(ActionKind.CAST, orthrus, None))
    assert got is not None and got.card is feint and got.target is boss
    weak = Combatant("Gurtok", 500, 5600, is_enemy=True, is_boss=True, resist={})
    b2 = Battle(me=me, allies=[], enemies=[weak], cards=[orthrus, feint], pips=1, power_pips=3)
    assert _boss_setup_first(b2, Action(ActionKind.CAST, orthrus, None)) is None  # it kills: go
