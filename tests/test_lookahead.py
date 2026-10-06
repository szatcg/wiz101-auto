"""The whole-deck planner (combat/lookahead.py)."""

import pytest

from wiz101_auto.combat import brain, lookahead
from wiz101_auto.combat.model import ActionKind, Battle, Card, Combatant, Effect, EffectKind, Target


@pytest.fixture(autouse=True)
def _quick(monkeypatch):
    monkeypatch.setattr(lookahead, "SAMPLES", 12)
    monkeypatch.setattr(brain, "incoming_per_round", lambda battle: 300.0)
    lookahead._CACHE.clear()


def orthrus(i):
    return Card(i, "Orthrus", school="myth", pip_cost=7,
                effects=[Effect(EffectKind.DAMAGE, Target.ENEMY_ALL, 1200)])


def frog(i):
    return Card(i, "Humongofrog", school="myth", pip_cost=5,
                effects=[Effect(EffectKind.DAMAGE, Target.ENEMY_ALL, 700)])


def feint(i):
    return Card(i, "Feint", school="death", pip_cost=1,
                effects=[Effect(EffectKind.TRAP, Target.ENEMY_SINGLE, 70)])


def blade(i):
    return Card(i, "Mythblade", school="myth", pip_cost=0,
                effects=[Effect(EffectKind.BLADE, Target.ALLY_SINGLE, 35, "myth")])


def mtrap(i):
    return Card(i, "Myth Trap", school="myth", pip_cost=0,
                effects=[Effect(EffectKind.TRAP, Target.ENEMY_SINGLE, 40, "myth")])


def bolt(i):
    return Card(i, "Myth Bolt", school="myth", pip_cost=3,
                effects=[Effect(EffectKind.DAMAGE, Target.ENEMY_SINGLE, 300)])


def _battle(hand, deck, hp=6000, pips=2, power=1):
    me = Combatant("Marcello", 3197, 3197, is_client=True, school="myth")
    boss = Combatant("Big Boss", hp, hp, is_enemy=True, is_boss=True, school="fire", resist={})
    return Battle(me=me, allies=[], enemies=[boss], cards=hand, pips=pips, power_pips=power,
                  upcoming=deck, deck_known=True)


def test_the_kill_search_refills_the_hand_and_takes_power_pips():
    # A full hand of cards it can't use: a pass draws nothing. One card fewer
    # (a discard): the Orthrus in the deck comes next round.
    junk = [Card(i, "Treasure Map", school="myth", pip_cost=0,
                 effects=[Effect(EffectKind.OTHER, Target.NONE, 0)]) for i in range(7)]
    deck = [orthrus(100)]
    b = _battle(junk, deck, hp=1100, pips=0, power=4)
    assert brain._kill_search(b, b.enemies[0], 3, deck, gains=[True] * 5, hand_size=7) is None
    trimmed = Battle(**{**b.__dict__, "cards": junk[:6]})
    found = brain._kill_search(trimmed, b.enemies[0], 3, deck, gains=[True] * 5, hand_size=7)
    assert found is not None and found[0] == 2 and "Orthrus" in found[3][-1]


def test_a_forced_first_move_is_the_line_scored():
    b = _battle([orthrus(0), blade(1)], [], hp=1500, pips=0, power=4)
    found = brain._kill_search(b, b.enemies[0], 3, [], force="pass", hand_size=7)
    assert found is not None and found[3][0] == "pass"


def test_a_clogged_hand_digs_for_the_big_hit():
    hand = [frog(0), bolt(1), bolt(2), bolt(3), feint(4), blade(5), mtrap(6)]
    deck = [orthrus(100), orthrus(101), blade(102), feint(103), *[bolt(110 + i) for i in range(4)], frog(120)]
    b = _battle(hand, deck)
    choice = lookahead.choose(b, brain.decide(b), power_chance=0.8, budget=60)
    assert choice is not None and choice.action.kind is ActionKind.DISCARD
    assert choice.action.card.name == "Myth Bolt"


def test_the_overlay_lists_the_draw_that_cuts_the_rounds():
    hand = [bolt(i) for i in range(5)]
    deck = [orthrus(100)] + [bolt(110 + i) for i in range(8)]
    b = _battle(hand, deck, hp=3000)
    base, better = lookahead.draw_values(b, 0.8, budget=60)
    assert "Orthrus" in better and better["Orthrus"] < base
