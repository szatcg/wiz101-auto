from wiz101_auto.quest_steps import base, record, view


def test_steps_are_recorded_in_order_and_counts_update_in_place(tmp_path):
    f = tmp_path / "steps.json"
    record("Burn It", "Burn Scout Tower in Tatakai Outpost (0 of 3)", f)
    record("Burn It", "Burn Scout Tower in Tatakai Outpost (2 of 3)", f)
    record("Burn It", "Talk To Sanisai Fukido in Tatakai Outpost", f)
    v = view("Burn It", "Talk To Sanisai Fukido in Tatakai Outpost", path=f)
    assert v["done"] == ["Burn Scout Tower in Tatakai Outpost (2 of 3)"]
    assert v["now"] == "Talk To Sanisai Fukido in Tatakai Outpost"


def test_base_drops_the_count():
    assert base("Defeat Cursed Ronins (3 of 10)") == "Defeat Cursed Ronins"


def test_pick_goal_skips_text_of_an_old_goal():
    from wiz101_auto.ui import pick_goal

    known: dict = {}
    assert pick_goal(["Talk To Sandor"], 1, known) == "Talk To Sandor"
    # The goal moved on; the old element is still visible and listed last.
    assert pick_goal(["Defeat Sandor", "Talk To Sandor"], 2, known) == "Defeat Sandor"
    assert pick_goal(["Defeat Sandor", "Talk To Sandor"], 2, known) == "Defeat Sandor"
    assert pick_goal(["A", "B"], None, {}) == "B"
    assert pick_goal([], 3, known) == ""


def test_side_world_story_is_not_main():
    from wiz101_auto.quest import QuestEntry, in_side_world

    assert in_side_world(QuestEntry(0, "Face Your Fate", world="Savarstaad Pass", zone="Grizzleheim"))
    assert not in_side_world(QuestEntry(0, "Going Portal", world="The Atheneum", zone="Dragonspyre"))
