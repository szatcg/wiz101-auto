from wiz101_auto.combat.fighter import draw_chance


def test_draw_chance():
    assert draw_chance(10, 0, 3) == 0
    assert draw_chance(10, 10, 1) == 1
    assert round(draw_chance(10, 2, 3), 3) == round(1 - (8 * 7 * 6) / (10 * 9 * 8), 3)
    assert draw_chance(3, 1, 5) == 1  # more draws than cards: certain
