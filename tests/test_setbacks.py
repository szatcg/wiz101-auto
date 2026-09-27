from wiz101_auto.quest import QuestEntry, choose_quest
from wiz101_auto.setbacks import DEFER_SECONDS, Setbacks

BOSS = "Defeat Lord Nightshade in Haunted Cave"


def test_second_defeat_sets_the_quest_aside_until_a_level_up(tmp_path):
    s = Setbacks(tmp_path / "s.json")
    assert not s.record_defeat(BOSS, "Throwing Nightshade", 10, now=0)
    assert s.record_defeat(BOSS, "Throwing Nightshade", 10, now=10)
    s.save()
    s = Setbacks.load(tmp_path / "s.json")
    assert s.set_aside(10, now=20) == {"Throwing Nightshade"}
    assert s.set_aside(11, now=30) == set()  # levelled up: try again


def test_set_aside_expires_after_a_while(tmp_path):
    s = Setbacks(tmp_path / "s.json")
    s.record_defeat(BOSS, "Throwing Nightshade", 10, now=0)
    s.record_defeat(BOSS, "Throwing Nightshade", 10, now=0)
    assert s.set_aside(10, now=DEFER_SECONDS + 1) == set()


def test_choose_quest_skips_a_set_aside_questline():
    stuck = QuestEntry(0, "Throwing Nightshade", mainline=True, active=True, hops=1)
    other = QuestEntry(1, "Cog Collection", hops=1)
    assert choose_quest([stuck, other]) is stuck
    assert choose_quest([stuck, other], {"Throwing Nightshade"}) is other
    assert choose_quest([stuck], {"Throwing Nightshade"}) is stuck  # nothing else to do
