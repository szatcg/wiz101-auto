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
