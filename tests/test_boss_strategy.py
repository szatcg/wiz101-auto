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

