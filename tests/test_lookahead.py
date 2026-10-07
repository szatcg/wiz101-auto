"""The whole-deck planner (combat/lookahead.py)."""

import pytest

from wiz101_auto.combat import brain, lookahead
from wiz101_auto.combat.model import ActionKind, Battle, Card, Combatant, Effect, EffectKind, Target


@pytest.fixture(autouse=True)
def _quick(monkeypatch):
    monkeypatch.setattr(lookahead, "SAMPLES", 12)
    monkeypatch.setattr(brain, "incoming_per_round", lambda battle: 300.0)
    monkeypatch.setattr(lookahead, "hit_rate", lambda card: card.accuracy / 100.0)  # (not the logs' rates)
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


def test_a_clogged_hand_digs_for_the_big_hit(monkeypatch):
    # (Spare copies of big hits may go only with SPARE_COPIES: off by default,
    # a binned copy left the simulator's decks short when a hit missed.)
    monkeypatch.setattr(lookahead, "SPARE_COPIES", True)
    hand = [frog(0), bolt(1), bolt(2), bolt(3), feint(4), blade(5), mtrap(6)]
    deck = [orthrus(100), orthrus(101), blade(102), feint(103), *[bolt(110 + i) for i in range(4)], frog(120)]
    b = _battle(hand, deck, hp=5000)  # (a kill the planner can see within its horizon)
    # (against the brain passing: a brain discard of its own is left alone)
    from wiz101_auto.combat.model import Action

    choice = lookahead.choose(b, Action(ActionKind.PASS), power_chance=0.8, budget=60)
    assert choice is not None and choice.action.kind is ActionKind.DISCARD
    assert choice.action.card.name == "Myth Bolt"


def test_the_overlay_lists_the_draw_that_cuts_the_rounds():
    hand = [bolt(i) for i in range(5)]
    deck = [orthrus(100)] + [bolt(110 + i) for i in range(8)]
    b = _battle(hand, deck, hp=3000)
    base, better = lookahead.draw_values(b, 0.8, budget=60)
    assert "Orthrus" in better and better["Orthrus"] < base


def test_a_prism_the_enemy_needs_is_never_a_dig_discard():
    # (The search counts one prism per hit, so the 2nd and 3rd looked spare:
    # it binned the Myth Prisms Porrich needs for every big hit.)
    prisms = [Card(i, "Myth Prism", school="myth", pip_cost=0,
                   effects=[Effect(EffectKind.OTHER, Target.ENEMY_SINGLE, 0)]) for i in range(3)]
    me = Combatant("Marcello", 3197, 3197, is_client=True, school="myth")
    porrich = Combatant("Porrich", 6000, 6000, is_enemy=True, is_boss=True, school="myth",
                        resist={"myth": 0.8, "storm": -0.35})
    b = Battle(me=me, allies=[], enemies=[porrich], cards=[*prisms, bolt(5)], pips=2, power_pips=1,
               upcoming=[orthrus(100)], deck_known=True)
    assert not any(brain._is_prism(c) for c in lookahead._droppable(b))


def gargantuan(i):
    return Card(i, "Gargantuan", school="sun", pip_cost=0,
                effects=[Effect(EffectKind.ENCHANT_DAMAGE, Target.SPELL, 300)])


def _rounds(b, monkeypatch, **flags):
    for k, v in flags.items():
        monkeypatch.setattr(lookahead, k, v)
    fut = lookahead._futures(b, 1, 0.0, 0)[0]
    return lookahead._group_search(b, fut.draws, fut.gains, 1e18, misses=fut.misses, rolls=fut.rolls)


def test_a_hit_takes_the_damage_enchant_in_hand(monkeypatch):
    # 300 + Gargantuan's 300 kills a 550 boss now; the bolt alone takes two casts.
    b = _battle([bolt(0), bolt(1), gargantuan(2)], [], hp=550, pips=3, power=0)
    assert _rounds(b, monkeypatch, ENCHANTS=False)[0] > 1  # (the second bolt waits for 3 pips)
    assert _rounds(b, monkeypatch, ENCHANTS=True)[0] == 1


def test_a_card_that_fizzles_does_nothing(monkeypatch):
    b = _battle([bolt(0)], [bolt(10 + i) for i in range(5)], hp=250, pips=3, power=0)
    monkeypatch.setattr(lookahead, "hit_rate", lambda card: 0.0 if card.index == 0 else 1.0)
    assert _rounds(b, monkeypatch, MISSES=False)[0] == 1
    assert _rounds(b, monkeypatch, MISSES=True)[0] > 1


def test_a_crit_can_make_the_kill():
    hand = [bolt(0), bolt(1)]
    b = _battle(hand, [], hp=500, pips=3, power=0)
    b.me.level, b.me.crit = 40, {"myth": 100.0}  # (a 40% crit chance: not counted on; no block: x2)
    gains = [False] * 30
    assert lookahead._group_search(b, [], gains, 1e18)[0] > 1
    crits = {id(hand[0]): (0.1, 0.5)}  # (this future: the first bolt crits)
    assert lookahead._group_search(b, [], gains, 1e18, rolls=crits)[0] == 1


def test_a_line_we_dont_live_through_is_no_kill(monkeypatch):
    # 300 a round against our 1000: down after the 4th round; the boss needs 5 bolts.
    b = _battle([bolt(i) for i in range(7)], [], hp=1450, pips=3, power=0)
    b.me.health = 1000
    monkeypatch.setattr(lookahead, "_hits_per_round", lambda battle, enemies: [300.0])
    assert _rounds(b, monkeypatch, SURVIVAL="line")[2] is False
    assert _rounds(b, monkeypatch, SURVIVAL="prune")[2] is False
    assert _rounds(b, monkeypatch, SURVIVAL="")[2] is None


def test_killing_the_add_first_is_what_keeps_us_up(monkeypatch):
    # (Live: a pass scored over the frog finishing Whiptail Pantera, the
    # add, as if the add hit for nothing once dead.) Each hits 300 a round;
    # four 300 zaps kill both in 4 rounds either way, but only the add
    # first leaves us standing (900 taken of 1000, against 1500).
    zaps = [Card(i, "Zap", school="myth", pip_cost=0,
                 effects=[Effect(EffectKind.DAMAGE, Target.ENEMY_SINGLE, 300)]) for i in range(4)]
    me = Combatant("Marcello", 1000, 3000, is_client=True, school="myth")
    boss = Combatant("Boss", 900, 900, is_enemy=True, is_boss=True, school="fire", resist={})
    add = Combatant("Pantera", 300, 300, is_enemy=True, school="fire", resist={})
    b = Battle(me=me, allies=[], enemies=[boss, add], cards=zaps, pips=0, power_pips=0,
               upcoming=[], deck_known=True)
    monkeypatch.setattr(lookahead, "SURVIVAL", "line")
    monkeypatch.setattr(lookahead, "_hits_per_round", lambda battle, enemies: [300.0] * len(enemies))
    gains = [False] * 30
    on_boss = lookahead._group_search(b, [], gains, 1e18, force=0, force_target="Boss")
    on_add = lookahead._group_search(b, [], gains, 1e18, force=0, force_target="Pantera")
    assert on_boss[0] == on_add[0] == 4
    assert on_boss[2] is False and on_add[2] is True


def test_overlay_saves_whole_rounds(monkeypatch):
    # 4.4 rounds now, 4.1 with a Feint: not "-0.0999999 rounds" but nothing;
    # 3.2 with an Orthrus: one whole round saved.
    from wiz101_auto.combat import fighter, lookahead
    from wiz101_auto.combat.model import Action, ActionKind, Battle, Card, Combatant

    monkeypatch.setattr(lookahead, "draw_values",
                        lambda *a, **k: (4.4, {"Feint": 4.1, "Orthrus": 3.2}))
    me = Combatant("me", 3000, 3300, is_client=True, school="myth")
    boss = Combatant("Boss", 9000, 9000, is_enemy=True, is_boss=True, resist={})
    deck = [Card(i, n) for i, n in enumerate(["Feint", "Orthrus", "Mythblade"])]
    b = Battle(me=me, allies=[], enemies=[boss], cards=[], upcoming=deck)
    out = fighter.improve_odds(b, Action(ActionKind.PASS))
    assert out["base"] == 4
    assert set(out["cards"]) == {"Orthrus"}
    assert out["base"] - out["cards"]["Orthrus"]["rounds"] == 1
