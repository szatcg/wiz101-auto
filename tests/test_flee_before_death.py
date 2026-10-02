from test_brain import battle, dmg_card, enemy, heal_card, me

from wiz101_auto.combat.brain import flee_before_death
from wiz101_auto.combat.model import Action, ActionKind, Target


def test_flees_when_the_next_round_can_kill_and_nothing_helps():
    hit = dmg_card(0, "Troll", 300)
    b = battle([hit], [enemy("Malistaire Drake", 5800, boss=True)], my=me(400, 2380))
    assert flee_before_death(b, Action(ActionKind.CAST, hit, b.enemies[0]), threat=534)
    assert flee_before_death(b, Action(ActionKind.PASS), threat=534)


def test_stays_when_healthy_healing_or_finishing():
    hit = dmg_card(0, "Orthrus", 900, target=Target.ENEMY_ALL)
    pixie = heal_card(1, "Pixie", 400)
    b = battle([hit, pixie], [enemy("Soul Servant", 500)], my=me(400, 2380))
    assert not flee_before_death(b, Action(ActionKind.PASS), threat=300)  # can't kill us
    assert not flee_before_death(b, Action(ActionKind.CAST, pixie), threat=534)
    assert not flee_before_death(b, Action(ActionKind.CAST, hit), threat=534)  # ends the fight


def test_round_threat_is_the_worst_hitter_plus_the_others_typical_rounds():
    from wiz101_auto.combat.brain import round_threat

    servant = [0, 40, 43, 50, 132]
    assert round_threat([[0, 534, 938], servant, servant, servant]) == 938 + 3 * 43
    assert round_threat([]) == 0.0
