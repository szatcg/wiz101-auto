"""Heals that leave us under the next hit (Catalan the Lightning Lizard, 2026-10-06)."""
from wiz101_auto.combat import brain
from wiz101_auto.combat.brain import decide, heal_falls_short
from wiz101_auto.combat.model import ActionKind, Battle, Card, Combatant, Effect, EffectKind, Target


def _orthrus(i, castable=False):
    return Card(i, "Orthrus", school="myth", pip_cost=7, castable=castable,
                effects=[Effect(EffectKind.DAMAGE, Target.ENEMY_ALL, 710, school="myth")])


def _pixie(i):
    return Card(i, "Pixie", school="life", pip_cost=2, effects=[Effect(EffectKind.HEAL, Target.SELF, 400,
                                                                       school="life")])


def _mythblade(i):
    return Card(i, "Mythblade", school="myth", pip_cost=0,
                effects=[Effect(EffectKind.BLADE, Target.ALLY_SINGLE, 35, school="myth")])


def _feint(i):
    return Card(i, "Feint", school="death", pip_cost=1,
                effects=[Effect(EffectKind.TRAP, Target.ENEMY_SINGLE, 70),
                         Effect(EffectKind.OTHER, Target.SELF, 30)])


def _spirit_blade(i):
    return Card(i, "Spirit Blade", school="balance", pip_cost=1, effects=[
        Effect(EffectKind.BLADE, Target.ALLY_SINGLE, 35, school=s) for s in ("death", "myth", "life")])


def _myth_trap(i):
    return Card(i, "Myth Trap", school="myth", pip_cost=0,
                effects=[Effect(EffectKind.TRAP, Target.ENEMY_SINGLE, 40, school="myth")])


def _catalan_round(health, hand, power_pips=2):
    me = Combatant("Marcello", health, 3285, is_client=True, school="myth", damage_bonus={"myth": 0.72})
    catalan = Combatant("Catalan the Lightning Lizard", 2589, 11520, is_enemy=True, is_boss=True,
                        school="storm", resist={"myth": -0.35, "storm": 0.4},
                        incoming_effects=[("spell:400226031", "myth", 0.4)], trap_count=1)
    upcoming = [Card(20, "Gargantuan", school="sun", effects=[Effect(EffectKind.ENCHANT_DAMAGE, Target.SPELL,
                                                                      225)]),
                _mythblade(21)]
    return Battle(me=me, allies=[], enemies=[catalan], cards=hand, pips=0, power_pips=power_pips,
                  round=8, upcoming=upcoming, deck_known=True)


def test_no_pixie_that_leaves_us_under_the_next_hit(monkeypatch):
    # Round 8: 336/3285 health, 2 power pips, Orthrus (with the Myth Trap up)
    # kills in a few rounds; Catalan hits for ~1200 when he hits. The Pixie
    # (2 power pips, ~400) leaves us under his next hit anyway and puts
    # Orthrus off: the bot cast it, and again at 879, and lost.
    monkeypatch.setattr(brain, "hit_size", lambda battle: 1200.0)
    hand = [_mythblade(0), _pixie(1), _feint(2), _spirit_blade(3), _pixie(4), _orthrus(5), _orthrus(6)]
    b = _catalan_round(336, hand)
    assert heal_falls_short(b, hand[1])
    action = decide(b)
    assert not (action.kind is ActionKind.CAST and action.card.name == "Pixie"), action.describe()
    # (Feint and Spirit Blade wait too: each takes a power pip Orthrus needs.)
    assert action.card is None or action.card.pip_cost == 0, action.describe()


def test_a_heal_that_lifts_us_over_the_next_hit_still_goes(monkeypatch):
    # Round 10 at 879: healed to ~1280, his next ~1200 hit no longer kills.
    monkeypatch.setattr(brain, "hit_size", lambda battle: 1200.0)
    hand = [_myth_trap(0), _feint(2), _spirit_blade(3), _pixie(4), _orthrus(5), _orthrus(6)]
    b = _catalan_round(879, hand)
    assert not heal_falls_short(b, hand[3])
    assert decide(b).card.name == "Pixie"


def test_hit_size_counts_only_the_rounds_it_hit(monkeypatch):
    from wiz101_auto.combat import lookahead

    monkeypatch.setattr(lookahead, "OBSERVED_HIT", 0.0)
    monkeypatch.setattr(brain, "incoming_per_round", lambda battle: 0.0)
    monkeypatch.setattr(brain, "_STATS", [0.0, {"Catalan the Lightning Lizard": {
        "alone": [0, 0, 1235, 0, 0, 1229, 975, 0], "shared": []}}])
    b = _catalan_round(879, [])
    assert abs(brain.hit_size(b) - (1235 + 1229 + 975) / 3) < 1
