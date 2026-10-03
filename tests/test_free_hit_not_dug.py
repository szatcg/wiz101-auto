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


def test_a_free_hit_that_would_break_a_trap_may_be_dug_away():
    scorch = dmg_card(0, "Minor Fire Scorch", 94, pips=0)
    big = dmg_card(1, "Orthrus", 700, pips=7, target=Target.ENEMY_ALL, castable=False)
    foes = [enemy("Malistaire Drake", 8000, boss=True), enemy("Servant", 890), enemy("Servant 2", 890)]
    for e in foes:
        e.incoming_effects = [("feint", "", 0.7)]
        e.trap_count = 1
    b = battle([scorch, big], foes)
    b.upcoming = [blade_card(10 + i) for i in range(10)]
    b.deck_known = True
    assert brain._free_hit(b) is None
    a = brain._dig_for_setup(b, brain.Strategy())
    assert a is not None and a.kind is ActionKind.DISCARD and a.card is scorch
    for e in foes:  # no traps to break: it's cast, not dug away
        e.incoming_effects, e.trap_count = [], 0
    assert brain._dig_for_setup(b, brain.Strategy()) is None


def test_using_up_a_weakness_is_named():
    from test_brain import me
    scorch = dmg_card(0, "Minor Fire Scorch", 94, pips=0)
    my = me(2000, 2380)
    my.outgoing_effects = [("weakness", "", -0.25)]
    b = battle([scorch], [enemy("Malistaire Drake", 8000, boss=True), enemy("Servant", 890)], my=my)
    a = brain._free_hit(b)
    assert a is not None and a.card is scorch and "Weakness" in a.reason
