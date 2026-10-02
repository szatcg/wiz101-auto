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


def test_pick_goal_waits_for_the_new_goals_text():
    from wiz101_auto.ui import pick_goal

    known: dict = {}
    assert pick_goal(["Talk To Edrik"], 1, known) == "Talk To Edrik"
    # The goal moved on but the helper still shows the old text: not remembered...
    assert pick_goal(["Talk To Edrik"], 2, known) == "Talk To Edrik"
    assert 2 not in known
    # ...so the new text is taken as soon as it shows.
    assert pick_goal(["Talk To Edrik", "Use Crystal Charger"], 2, known) == "Use Crystal Charger"


def test_recall_refusal_marks_no_return_unless_the_timer_ran_out(tmp_path, monkeypatch):
    from wiz101_auto import dungeons

    monkeypatch.setattr(dungeons, "NO_RETURN_FILE", tmp_path / "nr.json")
    assert dungeons.refusal_means_no_return("You cannot teleport to that location.")
    assert not dungeons.refusal_means_no_return(
        "You cannot teleport to that location.  The dungeon timer has ended and the dungeon has been reset.")
    assert dungeons.learn_no_return("DragonSpire/X/Interiors/DS_Tower")
    assert dungeons.no_return("DragonSpire/X/Interiors/DS_Tower")
    assert not dungeons.learn_no_return("DragonSpire/X/Interiors/DS_Tower")  # known already
