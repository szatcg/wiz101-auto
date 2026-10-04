from wiz101_auto.quest import QuestEntry, choose_quest
from wiz101_auto.questlist import CompletionTracker, load_quest_list, norm, parse_quest_list

SAMPLE = """Unicorn Way (2 quests)
1.
Unicorn Way
INSTANCE
SIDE ×1
TALK
2.
Ghost Hunters
MOB
Cyclops Lane (1 quests)
3.
A Good Day to Cyclops
EXPLORE
"""


def test_parse_keeps_order_areas_and_tags():
    qs = parse_quest_list(SAMPLE)
    assert [(q.index, q.name, q.area) for q in qs] == [
        (1, "Unicorn Way", "Unicorn Way"),
        (2, "Ghost Hunters", "Unicorn Way"),
        (3, "A Good Day to Cyclops", "Cyclops Lane"),
    ]
    assert qs[0].tags == ["INSTANCE", "SIDE ×1", "TALK"]


def test_the_real_list_parses():
    order = load_quest_list()
    assert len(order) >= 38 and order[norm("Throwing Nightshade")].area == "Olde Town"


def test_listed_quests_follow_list_order_and_area():
    order = {norm(q.name): q for q in parse_quest_list(SAMPLE)}
    cyclops = QuestEntry(0, "A Good Day to Cyclops", world="Cyclops Lane", goal="Go To Cyclops Lane", hops=0)
    ghosts = QuestEntry(1, "Ghost Hunters", world="The Commons", goal="Defeat Lost Soul", hops=3)
    # Ghost Hunters is a Unicorn Way quest even though its step is in the Commons.
    assert choose_quest([cyclops, ghosts], order=order) is ghosts


def test_completion_needs_two_readings(tmp_path):
    t = CompletionTracker(tmp_path / "done.txt")
    assert t.update({"A", "B"}) == []
    assert t.update({"A"}) == []  # maybe a partial read
    assert t.update({"A", "C"}) == ["B"]
    t.log(["B"])
    assert (tmp_path / "done.txt").read_text(encoding="utf-8") == "B\n"
    assert t.update({"A", "B2"}) == []  # C missing once only
    assert t.update({"A", "C"}) == []  # C came back: not completed


def test_quest_status_story_order():
    from wiz101_auto.questlist import ListedQuest, quest_status

    listed = [ListedQuest(i + 1, n, "Area") for i, n in enumerate(["A", "B", "C", "D"])]
    st = quest_status(listed, completed=["A"], book=["C"], later_world_reached=False)
    assert st == {"A": "done", "B": "done", "C": "active", "D": "todo"}
    assert set(quest_status(listed, [], [], True).values()) == {"done"}


def test_tidy_world_list_merges_repeated_quest_numbers():
    from wiz101_auto.questlist import parse_quest_list, tidy_world_list

    text = "Lab (2 quests)\n38.\nWeird Science\nTALK\n39.\nNext\nTALK\n38.\nWeird Science\nBOSS\n"
    listed = tidy_world_list(parse_quest_list(text))
    assert [q.index for q in listed] == [38, 39]
    assert listed[0].tags == ["TALK", "BOSS"]


def test_tidy_world_list_merges_a_repeat_with_a_typo():
    from wiz101_auto.questlist import parse_quest_list, tidy_world_list

    text = ("A (2 quests)\n3.\nPrawn To King's Fourth\nEXPLORE\n4.\nHow Shellfish\nMOB\n"
            "3.\nPrawn To King's Forth\nTALK\n")
    assert [q.index for q in tidy_world_list(parse_quest_list(text))] == [3, 4]


def test_a_completed_quest_back_in_the_book_is_taken_off(tmp_path):
    from wiz101_auto.questlist import CompletionTracker

    log = tmp_path / "done.txt"
    log.write_text("Old Quest\nScouring for Scouts\n", encoding="utf-8")
    t = CompletionTracker(log)
    t.update({"Scouring for Scouts", "Other"})  # back after a battlefield hid it
    assert log.read_text(encoding="utf-8") == "Old Quest\n"
    t.log(t.update({"Other"}) + t.update({"Other"}))  # gone twice: done
    assert log.read_text(encoding="utf-8") == "Old Quest\nScouring for Scouts\n"
    t.update({"Other", "Scouring for Scouts"})  # and back again
    assert log.read_text(encoding="utf-8") == "Old Quest\n"


def test_wintertusk_zones_are_wintertusk():
    from wiz101_auto.questlist import world_of_zone

    assert world_of_zone("Grizzleheim/GH_HFjord/GH_Vestrilund") == "Wintertusk"
    assert world_of_zone("Grizzleheim/GH_HFjord/Interiors/GH_Aust_Cave05") == "Wintertusk"
    assert world_of_zone("Grizzleheim/GH_MainHub") == "Grizzleheim"
    assert world_of_zone("Krokotopia/KT_Hub") == "Krokotopia"


def test_side_quests_in_wintertusk_after_celestias():
    from wiz101_auto.quest import QuestEntry, choose_quest

    cel = QuestEntry(0, "Land Sharks", world="District of the Stars", reward=100)
    wt = QuestEntry(1, "Breakfast Club", world="Hrundle Fjord", reward=900)
    wiz = QuestEntry(2, "Hard To Resist", world="Unicorn Way", reward=999)
    places = ("Grizzleheim/GH_HFjord",)
    assert choose_quest([cel, wt, wiz], world="Celestia", fallback=places) is cel
    assert choose_quest([wt, wiz], world="Celestia", fallback=places) is wt
    assert choose_quest([wiz], world="Celestia", fallback=places) is None
