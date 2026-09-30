import asyncio

from wiz101_auto.combat import sim
from wiz101_auto.combat.model import Action, ActionKind, Battle, Card, Combatant, Effect, EffectKind, Target
from wiz101_auto.combat.rollout import RolloutPlanner

STATS = {
    "enemies": {
        "Weakling": {"max_health": 300, "boss": False, "shared": [],
                     "alone": [0, 50, 0, 60, 40, 0, 30, 20, 0]},
        "Brute": {"max_health": 1200, "boss": True, "shared": [],
                  "alone": [200, 0, 250, 300, 0, 220, 0, 260, 240]},
    },
    "hit_rate": {"Big Hit": 1.0},
}


def _hit(i, dmg, pips, name="Big Hit"):
    dmg_fx = Effect(EffectKind.DAMAGE, Target.ENEMY_SINGLE, dmg)
    return Card(i, name, school="myth", pip_cost=pips, effects=[dmg_fx])


def _battle(enemies, cards, pips=4):
    me = Combatant("Me", 1500, 1500, is_client=True, school="myth", resist={})
    return Battle(me=me, allies=[], enemies=enemies, cards=cards, pips=pips, power_pips=0, round=1,
                  upcoming=[_hit(9, 300, 2) for _ in range(6)], deck_known=True)


def test_samples_for_uses_its_own_rounds_else_similar_enemies():
    assert sim.samples_for("Weakling", 300, False, STATS) == STATS["enemies"]["Weakling"]["alone"]
    alike = sim.samples_for("Unknown Brute", 1100, True, STATS)  # a boss of about that health
    assert alike == STATS["enemies"]["Brute"]["alone"]


def test_a_killing_hit_plays_out_better_than_passing():
    foe = Combatant("Weakling", 250, 300, is_enemy=True, school="fire", resist={})
    b = _battle([foe], [_hit(0, 400, 3)])
    hit, pas = Action(ActionKind.CAST, b.cards[0], foe), Action(ActionKind.PASS)
    outs = sim.evaluate(b, [pas, hit], stats=STATS, n=10)
    assert outs[0].action is hit and outs[0].wins == 1.0


def test_fight_from_battle_copies_the_live_state():
    foe = Combatant("Brute", 900, 1200, is_enemy=True, is_boss=True, school="myth", resist={"myth": 0.5})
    b = _battle([foe], [_hit(0, 400, 3)])
    b.prismed = {"Brute"}
    import random

    f = sim.fight_from_battle(b, STATS, random.Random(1))
    assert f.enemies[0] is not foe and f.enemies[0].health == 900 and f.prism_on == {"Brute"}
    assert f.foes["Brute"].samples == STATS["enemies"]["Brute"]["alone"]
    assert len(f.hand) == 1 and len(f.deck) == 6


def test_planner_replaces_a_clearly_worse_move(monkeypatch):
    import wiz101_auto.combat.rollout as rollout

    monkeypatch.setattr(rollout, "MIN_ENEMY_HEALTH", 0)
    # A hard hitter with 250 health left and us at 300: a pass risks dying.
    foe = Combatant("Brute", 250, 1200, is_enemy=True, is_boss=True, school="fire", resist={})
    b = _battle([foe], [_hit(0, 400, 3)])
    b.me.health = 300
    planner = RolloutPlanner(workers=2, time_limit=120, stats=STATS)
    try:
        chosen = asyncio.run(planner.choose(b, Action(ActionKind.PASS, reason="brain"), None, 0))
    finally:
        planner.close()
    assert chosen.kind is ActionKind.CAST and chosen.card.index == 0 and chosen.target.name == "Brute"


def test_planner_never_considers_a_chip_hit_on_our_feint():
    boss = Combatant("Brute", 1200, 1200, is_enemy=True, is_boss=True, school="myth", resist={},
                     incoming_effects=[("spell:feint", "", 0.7)])
    chip = Card(1, "Wand Hit", school="fire", pip_cost=0, item=True,
                effects=[Effect(EffectKind.DAMAGE, Target.ENEMY_SINGLE, 85)])
    b = _battle([boss], [_hit(0, 495, 5), chip], pips=2)
    b.cards[0].castable = False
    moves = sim.candidates(b)
    assert not any(m.card is chip for m in moves)
    boss.incoming_effects = []  # nothing to waste: the chip hit is a move again
    assert any(m.card is chip for m in sim.candidates(b))
