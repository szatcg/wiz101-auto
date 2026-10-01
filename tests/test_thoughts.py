from wiz101_auto.thoughts import parse_line


def test_round_line_becomes_a_battle_event():
    e = parse_line(
        "18:01:13 | INFO    | [round 4] pips=2+1P hp=812/1180 vs Keeper of the Fang* 750/750, "
        "Vault Haunter 435/435 -> cast Cyclops on Keeper of the Fang (finish Keeper of the Fang: ~906 dmg)"
    )
    assert e["kind"] == "cast" and e["round"] == 4 and e["pips"] == 2 and e["power"] == 1
    assert e["card"] == "Cyclops" and e["target"] == "Keeper of the Fang"
    assert e["why"].startswith("finish")
    keeper = {"name": "Keeper of the Fang", "boss": True, "hp": 750, "max": 750, "dead": False}
    assert e["enemies"][0] == keeper


def test_pass_discard_and_other_kinds():
    p = parse_line("18:00:05 | INFO    | [round 2] pips=1+0P hp=1180/1180 vs A 10/20 -> pass (saving pips)")
    assert p["kind"] == "pass" and p["why"] == "saving pips"
    d = parse_line("18:00:05 | INFO    | [round 2] pips=1+0P hp=1/2 vs A 10/20 -> discard Blood Bat (chip)")
    assert d["kind"] == "discard" and d["card"] == "Blood Bat"
    assert parse_line("18:02:00 | INFO    | marked this spot in X before the fight")["kind"] == "mark"
    assert parse_line("18:02:00 | SUCCESS | objective done -> now: 'Talk'")["kind"] == "quest"
    assert parse_line("18:02:00 | INFO    | Hall Servant (ice): resists {}") is None


def test_humanize_battle_moves():
    from wiz101_auto.thoughts import humanize

    def say(line):
        return humanize(parse_line("18:00:00 | INFO    | [round 4] pips=2+1P hp=1/2 vs " + line))

    tag, text = say("K* 750/750 -> cast Cyclops on K (finish K: ~906 dmg)")
    assert tag == "FINISH" and text == "Finishing off K with Cyclops (~906 dmg)"
    tag, text = say("V 245/435 -> pass (waiting: kills V in 2 round(s): pass > Troll (~266))")
    assert tag == "WAIT" and "pass → Troll" in text
    tag, text = say("K* 750/750 -> cast Myth Trap on K (trap K)")
    assert tag == "SETUP" and "Myth Trap on K" in text
    tag, _ = say("A 10/20 -> discard Blood Bat (only chips A; drawing for a bigger hit)")
    assert tag == "TOSS"


def test_humanize_quest_steps_and_places():
    from wiz101_auto.thoughts import humanize, place

    assert place("Krokotopia/KT_Pyramid/KT_AltarOfKings") == "Altar Of Kings"
    line = "marked this spot in Krokotopia/KT_Krokosphinx/KT_Arena before the fight"
    e = parse_line("18:00:00 | INFO    | " + line)
    assert humanize(e) == ("MARK", "Leaving a mark here to Recall back after a defeat")


def test_prediction_attaches_to_the_battle(tmp_path):
    from wiz101_auto.thoughts import read_thoughts

    log = tmp_path / "activity.log"
    log.write_text(
        "skipped first line\n"
        "18:01:13 | INFO    | [round 4] pips=2+1P hp=1/2 vs K* 750/750, V 435/435 -> cast Cyclops on K (x)\n"
        "18:01:13 | INFO    | predict: 0=906\n",
        encoding="utf-8",
    )
    t = read_thoughts(log)
    assert t["battle"]["predict"] == {0: 906}
    assert all("predict" not in e["text"] for e in t["events"])


def test_predicted_damage_for_single_and_aoe():
    from wiz101_auto.combat.brain import predicted_damage
    from wiz101_auto.combat.model import (
        Action,
        ActionKind,
        Battle,
        Card,
        Combatant,
        Effect,
        EffectKind,
        Target,
    )

    a = Combatant("A", 300, 300, is_enemy=True)
    b = Combatant("B", 300, 300, is_enemy=True)
    me = Combatant("Me", 500, 500, is_client=True)
    hit = Card(0, "Troll", effects=[Effect(EffectKind.DAMAGE, Target.ENEMY_SINGLE, 190)])
    aoe = Card(1, "Meteor", effects=[Effect(EffectKind.DAMAGE, Target.ENEMY_ALL, 100)])
    battle = Battle(me=me, allies=[], enemies=[a, b], cards=[hit, aoe])
    assert predicted_damage(battle, Action(ActionKind.CAST, hit, b)) == {1: 190}
    assert predicted_damage(battle, Action(ActionKind.CAST, aoe, None)) == {0: 100, 1: 100}


def test_a_discard_says_what_it_is_drawing_for():
    from wiz101_auto.thoughts import humanize, parse_line

    line = ("00:30:25 | INFO    | [round 1] pips=1+1P hp=1982/1982 vs Infected Villager 675/675 -> "
            "discard Vampire (digging for a blade / hit-all spell)")
    tag, say = humanize(parse_line(line))
    assert say == "Tossing Vampire: drawing for a blade or hit-all spell"
    assert "Troll" not in say
