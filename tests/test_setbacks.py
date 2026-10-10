from wiz101_auto.quest import QuestEntry, choose_quest
from wiz101_auto.setbacks import ALWAYS_SKIP, DEFER_SECONDS, Setbacks

BOSS = "Defeat Lord Nightshade in Haunted Cave"


def test_second_defeat_sets_the_quest_aside_until_a_level_up(tmp_path):
    s = Setbacks(tmp_path / "s.json")
    assert not s.record_defeat(BOSS, "Throwing Nightshade", 10, now=0)
    assert s.record_defeat(BOSS, "Throwing Nightshade", 10, now=10)
    s.save()
    s = Setbacks.load(tmp_path / "s.json")
    assert s.set_aside(10, now=20) - ALWAYS_SKIP == {"Throwing Nightshade"}
    assert s.set_aside(11, now=30) - ALWAYS_SKIP == set()  # levelled up: try again


def test_set_aside_expires_after_a_while(tmp_path):
    s = Setbacks(tmp_path / "s.json")
    s.record_defeat(BOSS, "Throwing Nightshade", 10, now=0)
    s.record_defeat(BOSS, "Throwing Nightshade", 10, now=0)
    assert s.set_aside(10, now=DEFER_SECONDS + 1) - ALWAYS_SKIP == set()


def test_choose_quest_skips_a_set_aside_questline():
    stuck = QuestEntry(0, "Throwing Nightshade", mainline=True, active=True, hops=1)
    other = QuestEntry(1, "Cog Collection", hops=1)
    assert choose_quest([stuck, other]) is stuck
    assert choose_quest([stuck, other], {"Throwing Nightshade"}) is other
    assert choose_quest([stuck], {"Throwing Nightshade"}) is stuck  # nothing else to do


def test_skipped_quests_stay_skipped(tmp_path):
    s = Setbacks(tmp_path / "s.json")
    s.skipped.add("Advanced Combat")
    s.save()
    assert Setbacks.load(tmp_path / "s.json").set_aside(99, now=1e12) - ALWAYS_SKIP == {"Advanced Combat"}


def test_a_stalled_quest_is_set_aside_until_a_level_up(tmp_path):
    s = Setbacks(tmp_path / "s.json")
    s.set_quest_aside("Collecting Gems", "Collect Flame Gems in Palace of Fire (0 of 4)", 13, now=0)
    assert s.set_aside(13, now=60) - ALWAYS_SKIP == {"Collecting Gems"}
    assert s.set_aside(14, now=60) - ALWAYS_SKIP == set()


def test_a_set_aside_main_quest_comes_back_soon(tmp_path):
    from wiz101_auto.setbacks import MAIN_RETRY_SECONDS

    s = Setbacks(tmp_path / "s.json")
    s.set_quest_aside("Payback", "Defeat Akori Nirini in Akori's Chamber", 14, now=0, main=True)
    s.set_quest_aside("Collecting Gems", "Collect Flame Gems", 14, now=0)
    assert s.set_aside(14, now=10) - ALWAYS_SKIP == {"Payback", "Collecting Gems"}
    # (not until a level-up: 'Quest for Perfection' left the bot grinding)
    assert s.set_aside(14, now=MAIN_RETRY_SECONDS + 10) - ALWAYS_SKIP == {"Collecting Gems"}
    assert s.set_aside(14, now=DEFER_SECONDS + 10) - ALWAYS_SKIP == set()


def test_stuck_main_quest_comes_back_after_its_retry_time(tmp_path):
    from wiz101_auto.setbacks import Setbacks

    s = Setbacks(tmp_path / "s.json")
    s.set_quest_aside("De-Cipher the Djeserits", "Defeat King Shemet", 20, now=0, main=True, retry_after=1800)
    assert "De-Cipher the Djeserits" in s.set_aside(20, now=1000)
    assert "De-Cipher the Djeserits" not in s.set_aside(20, now=2000)


def test_ironworks_dungeon_quests_are_always_skipped(tmp_path):
    from wiz101_auto.setbacks import Setbacks

    s = Setbacks.load(tmp_path / "setbacks.json")  # no file: nothing saved yet
    aside = s.set_aside(level=33)
    assert {"No Entry", "Gate Crashers"} <= aside


def test_a_stuck_quest_stays_aside_until_nothing_else_is_left(tmp_path):
    from wiz101_auto.setbacks import Setbacks

    s = Setbacks(tmp_path / "s.json") if "path" in Setbacks.__init__.__code__.co_varnames else Setbacks()
    s.set_quest_aside("Into the Zebra Tomb", "Explore King's Tomb", 66, now=0.0, main=True, stuck=True)
    assert "Into the Zebra Tomb" in s.set_aside(67, now=10_000.0)  # a level-up and hours later: still aside
    assert s.release_stuck(now=100.0) == []                         # not yet an hour
    assert s.release_stuck(now=4000.0) == ["Into the Zebra Tomb"]
    assert "Into the Zebra Tomb" not in s.set_aside(67, now=4000.0)


def test_a_stuck_side_quest_isnt_released_by_the_hour(tmp_path):
    from wiz101_auto.setbacks import Setbacks

    s = Setbacks(tmp_path / "s.json") if "path" in Setbacks.__init__.__code__.co_varnames else Setbacks()
    s.set_quest_aside("All Your Basilisk...", "Go To The Hoarder's Tower", 67, now=0.0, main=False,
                      stuck=True)
    assert "All Your Basilisk..." in s.set_aside(67, now=10_000.0)


def test_a_stuck_main_quest_comes_back_after_15_minutes(tmp_path):
    from wiz101_auto.setbacks import Setbacks

    s = Setbacks(tmp_path / "s.json") if "path" in Setbacks.__init__.__code__.co_varnames else Setbacks()
    s.set_quest_aside("Schooling Fish", "Defeat Tzapotec", 89, now=0.0, main=True, stuck=True)
    s.set_quest_aside("Art History", "Find Zafaria History", 89, now=0.0, main=False, stuck=True)
    assert s.release_stuck_main(now=100.0) == []
    assert s.release_stuck_main(now=1000.0) == ["Schooling Fish"]
    assert "Art History" in s.deferred


def test_a_side_boss_lost_again_after_the_hour_waits_for_a_level_up(tmp_path):
    s = Setbacks(tmp_path / "s.json")
    boss = "Defeat Clemente Moraga in Mangrove Marsh"
    s.record_defeat(boss, "Turn Up the Sun", 90, now=0)
    assert s.record_defeat(boss, "Turn Up the Sun", 90, now=0)
    assert "Turn Up the Sun" not in s.set_aside(90, now=DEFER_SECONDS + 1)  # the hour: back
    assert s.record_defeat(boss, "Turn Up the Sun", 90, now=DEFER_SECONDS + 2)  # one loss is enough
    assert "Turn Up the Sun" in s.set_aside(90, now=10 * DEFER_SECONDS)  # same level: still aside
    assert "Turn Up the Sun" not in s.set_aside(91, now=10 * DEFER_SECONDS)
