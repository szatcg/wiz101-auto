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
