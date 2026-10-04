from wiz101_auto.combat.brain import (
    _crit_gamble,
    block_chance,
    clear_chance,
    crit_chance,
    crit_multiplier,
    kill_chance,
)
from wiz101_auto.combat.model import Action, ActionKind, Battle, Card, Combatant, Effect, EffectKind, Target

ORTHRUS = Card(0, "Orthrus", school="myth", pip_cost=7,
               effects=[Effect(EffectKind.DAMAGE, Target.ENEMY_ALL, 700)])
BOLT = Card(1, "Myth Bolt", school="myth", pip_cost=1,
            effects=[Effect(EffectKind.DAMAGE, Target.ENEMY_SINGLE, 1000)])


def me(crit=0.0, level=100):
    return Combatant("me", 3000, 3000, is_client=True, school="myth", crit={"myth": crit}, level=level)


def foe(hp, block=0.0, name="Foe"):
    return Combatant(name, hp, hp, is_enemy=True, resist={}, block={"myth": block})


def test_the_2021_numbers():
    # Equal crit and block: 75% crit, 25% block at level 100; 15% / 5% at 20; a crit x1.25.
    assert round(crit_chance(me(100), foe(1, 100), "myth"), 2) == 0.75
    assert round(block_chance(me(100), foe(1, 100), "myth"), 2) == 0.25
    assert round(crit_chance(me(100, 20), foe(1, 100), "myth"), 2) == 0.15
    assert round(block_chance(me(100, 20), foe(1, 100), "myth"), 2) == 0.05
    assert round(crit_multiplier(me(100), foe(1, 100), "myth"), 2) == 1.25
    assert crit_multiplier(me(100), foe(1, 0), "myth") == 2.0
    assert crit_chance(me(0), foe(1, 100), "myth") == 0.0  # no crit stat: never


def test_kill_and_clear_chances():
    m = me(100)
    assert kill_chance(BOLT, m, foe(900)) == 1.0          # dies anyway
    assert kill_chance(BOLT, m, foe(1500)) > 0.5          # only on a crit (x2, no block): the crit chance
    assert kill_chance(BOLT, m, foe(5000)) == 0.0         # not even on a crit
    two = [foe(1000, name="A"), foe(800, 100, name="B")]  # Orthrus 700: both die only on a crit
    p = clear_chance(ORTHRUS, m, two)
    assert 0 < p < crit_chance(m, two[0], "myth")         # B may block it (x1.25 still kills B)


def test_the_crit_gamble():
    m = me(100, level=60)  # 60% crit against no block
    target = foe(1500)
    b = Battle(me=m, allies=[], enemies=[target], cards=[BOLT], pips=3, power_pips=0)
    got = _crit_gamble(b, Action(ActionKind.PASS, reason="saving pips"))
    assert got is not None and got.card is BOLT and "on a crit" in got.reason
    # No crit stat: no gamble.
    b0 = Battle(me=me(0), allies=[], enemies=[foe(1500)], cards=[BOLT], pips=3, power_pips=0)
    assert _crit_gamble(b0, Action(ActionKind.PASS, reason="saving pips")) is None


def test_the_overlay_reads_the_crit_line(tmp_path):
    from wiz101_auto.thoughts import read_thoughts

    log = tmp_path / "activity.log"
    log.write_text("x\n"
                   "12:00:00 | INFO    | [round 2] pips=1+2P hp=2000/2400 vs Boss* 3000/3000 -> Orthrus\n"
                   "12:00:00 | INFO    | predict: 0=1200\n"
                   "12:00:00 | INFO    | crit: 0=35/10/1200/1650\n", encoding="utf-8")
    b = read_thoughts(log)["battle"]
    assert b["crit"] == {0: {"c": 35, "b": 10, "n": 1200, "x": 1650}}


def test_one_turn_is_one_line(tmp_path):
    from wiz101_auto.thoughts import read_thoughts

    log = tmp_path / "activity.log"
    head = "| INFO    | [round {}] pips=0+4P hp=2000/2400 vs Boss* 3000/3000 -> {}\n"
    moves = [(3, "enchant Orthrus with Giant"), (3, "cast Orthrus (~1600 dmg)"),
             (4, "discard Feint (spare)"), (4, "cast Mythblade on Me (blade up)")]
    log.write_text("x\n" + "".join(f"12:00:0{i} " + head.format(r, m) for i, (r, m) in enumerate(moves)),
                   encoding="utf-8")
    events = [e for e in read_thoughts(log)["events"] if "round" in e]
    assert [e["round"] for e in events] == [3, 4]
    assert "→" in events[0]["say"] and "→" in events[1]["say"]


def test_the_stream_counts_our_prism():
    from wiz101_auto.combat.brain import predicted_damage

    boss = Combatant("Ildrede", 5000, 5000, is_enemy=True, is_boss=True, school="myth",
                     resist={"myth": 0.8, "storm": -0.35})
    b = Battle(me=me(), allies=[], enemies=[boss], cards=[ORTHRUS], pips=7, power_pips=0)
    plain = predicted_damage(b, Action(ActionKind.CAST, ORTHRUS))[0]
    b.prismed = {"Ildrede"}
    assert predicted_damage(b, Action(ActionKind.CAST, ORTHRUS))[0] > 3 * plain
