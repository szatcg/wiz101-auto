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
