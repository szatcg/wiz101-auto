from test_brain import battle, blade_card, enemy, heal_card, me

from wiz101_auto.combat import brain
from wiz101_auto.combat.model import ActionKind


def test_a_boss_fight_never_digs_away_a_heal():
    pixies = [heal_card(0, "Pixie", 400), heal_card(1, "Pixie", 400)]
    b = battle(pixies, [enemy("Malistaire Drake", 8000, boss=True)], my=me(2300, 2380))
    b.upcoming = [blade_card(10 + i) for i in range(10)]
    b.deck_known = True
    a = brain.decide(b)
    assert not (a.kind is ActionKind.DISCARD and a.card.is_heal)



def test_a_hit_all_waits_for_the_blade_that_lets_it_kill_everyone():
    from test_brain import dmg_card, trap_card

    from wiz101_auto.combat.model import Target

    frog = dmg_card(0, "Humongofrog", 470, pips=4, target=Target.ENEMY_ALL)
    blade = blade_card(1, 35)
    a_ = enemy("Clawcutter", 610)
    a_.incoming_effects, a_.trap_count = [("trap", "", 0.3)], 1
    b = battle([frog, blade], [a_, enemy("Clawcutter 2", 610)], my=me(2400, 2400))
    b.pips = 4
    act = brain.decide(b)
    assert act.card is blade  # not the frog that kills one and leaves the other at ~140
    boss = enemy("Boss", 3000, boss=True)
    b2 = battle([frog, blade], [a_, boss], my=me(2400, 2400))
    b2.pips = 4
    assert brain.decide(b2).card is frog  # a bigger boss: kill what it can now
    _ = trap_card


def test_a_castable_hit_all_beats_waiting_for_a_far_off_bigger_one():
    from test_brain import dmg_card

    from wiz101_auto.combat.model import Target

    orthrus = dmg_card(0, "Orthrus", 1300, pips=7, target=Target.ENEMY_ALL, castable=False)
    frog = dmg_card(1, "Humongofrog", 600, pips=4, target=Target.ENEMY_ALL)
    b = battle([orthrus, frog], [enemy("Waverunner", 1230), enemy("Waverunner 2", 1230)], my=me(1156, 2402))
    b.pips, b.power_pips = 2, 1
    act = brain.decide(b)
    assert act.kind is not ActionKind.PASS and act.card is not orthrus


def test_a_second_copy_of_a_blade_that_is_up_is_wasted():
    from wiz101_auto.combat.model import Action, Card, Effect, EffectKind, Target

    blade = Card(0, "Mythblade", pip_cost=0, school="Myth",
                 effects=[Effect(EffectKind.BLADE, Target.ALLY_SINGLE, 35, school="myth")])
    my = me(2000, 2424)
    my.school = "myth"
    b = battle([blade], [enemy("Water Servant", 840)], my=my)
    assert not brain.wasted_setup(Action(ActionKind.CAST, blade, my), b)
    my.outgoing_effects = [("mythblade", "myth", 0.35)]
    assert brain.wasted_setup(Action(ActionKind.CAST, blade, my), b)


def _myth(i, name, dmg, pips, aoe=False, castable=True):
    from wiz101_auto.combat.model import Card, Effect, EffectKind, Target

    t = Target.ENEMY_ALL if aoe else Target.ENEMY_SINGLE
    return Card(i, name, pip_cost=pips, school="myth", castable=castable,
                effects=[Effect(EffectKind.DAMAGE, t, dmg, school="myth")])


def test_a_small_hit_on_a_boss_waits_for_the_big_one():
    frog = _myth(0, "Humongofrog", 600, 4, aoe=True)
    orthrus = _myth(1, "Orthrus", 1300, 7, aoe=True, castable=False)
    my = me(1300, 2221)
    my.school = "myth"
    b = battle([frog, orthrus], [enemy("Runed Annihilator", 2008, boss=True)], my=my)
    b.pips, b.power_pips = 0, 3
    assert brain.decide(b, discards_left=2).card is not frog  # Orthrus in hand: keep the pips
    b2 = battle([frog], [enemy("Runed Annihilator", 2008, boss=True)], my=my)
    b2.pips, b2.power_pips = 0, 3
    b2.upcoming = [_myth(5, "Orthrus", 1300, 7, aoe=True)]
    b2.deck_known = True
    a = brain.decide(b2, discards_left=2)
    assert a.kind is ActionKind.DISCARD and a.card is frog  # Orthrus in the deck: dig for it


def test_a_one_pip_off_school_setup_waits_for_a_regular_pip_when_the_payoff_would_slip():
    from wiz101_auto.combat.model import Action, Card, Effect, EffectKind, Target

    feint = Card(0, "Feint", pip_cost=1, school="death",
                 effects=[Effect(EffectKind.TRAP, Target.ENEMY_SINGLE, 70)])
    orthrus = _myth(1, "Orthrus", 1300, 7, aoe=True, castable=False)
    my = me(2000, 2221)
    my.school = "myth"
    foe = enemy("Water Servant", 840)
    b = battle([feint, orthrus], [foe, enemy("Water Servant 2", 840)], my=my)
    b.pips, b.power_pips = 0, 3  # 6 now; Feint by a power pip leaves 4, +1 next round = 5 < 7
    act = brain._pip_wise_setup(b, Action(ActionKind.CAST, feint, foe), brain.Strategy())
    assert act is not None and act.card is not feint
    b.pips, b.power_pips = 1, 3  # a regular pip pays for it
    assert brain._pip_wise_setup(b, Action(ActionKind.CAST, feint, foe), brain.Strategy()) is None


def test_discards_make_room_to_draw_the_whole_deck_when_a_card_left_ends_it():
    filler = [_myth(i, f"Hit{i}", 50, 2) for i in range(7)]
    my = me(2000, 2221)
    my.school = "myth"
    b = battle(filler, [enemy("Last One", 900)], my=my)
    b.pips, b.power_pips = 1, 3  # 7 next round
    b.upcoming = [_myth(10, "Pixie-ish", 10, 2), _myth(11, "Orthrus", 1300, 7, aoe=True),
                  _myth(12, "Junk", 10, 1)]
    b.deck_known = True
    a = brain.decide(b, discards_left=0)
    assert a.kind is ActionKind.DISCARD and "draw the whole deck" in a.reason
    b.cards = filler[:4]  # room for 3: every card left comes anyway
    assert brain._dig_for_sure_kill(b) is None
