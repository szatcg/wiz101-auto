import time

from test_brain import battle, blade_card, dmg_card, enemy

from wiz101_auto.combat import brain
from wiz101_auto.combat.model import ActionKind, Target


def test_a_free_hit_is_cast_not_discarded_for_a_draw():
    scorch = dmg_card(0, "Minor Fire Scorch", 94, pips=0)
    big = dmg_card(1, "Orthrus", 700, pips=7, target=Target.ENEMY_ALL, castable=False)
    foes = [enemy("Malistaire Drake", 8000, boss=True)] + [enemy(f"Servant{i}", 890) for i in range(3)]
    b = battle([scorch, big], foes)
    b.pips = 2
    b.upcoming = [blade_card(10 + i) for i in range(10)]
    b.deck_known = True
    a = brain.decide(b)
    assert a.kind is ActionKind.CAST and a.card is scorch


def test_the_long_discard_plan_gives_up_past_its_budget(monkeypatch):
    monkeypatch.setattr(brain, "DISCARD_PLAN_SECONDS", -1.0)  # already past the deadline
    hit = dmg_card(0, "Troll", 190, pips=2)
    b = battle([hit, dmg_card(1, "Thunder Snake", 90, pips=1)], [enemy("Malistaire Drake", 8000, boss=True)])
    b.upcoming = [dmg_card(10 + i, "Troll", 190, pips=2) for i in range(10)]
    started = time.monotonic()
    assert brain._plan_discard(b) is None
    assert time.monotonic() - started < 1.0
