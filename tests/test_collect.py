import pytest

from wiz101_auto.collect import collect_item_name, matches_item


@pytest.mark.parametrize(
    "objective,item",
    [
        ("Collect Cog in Triton Avenue (0 of 3)", "Cog"),
        ("Collect Cog in Triton Avenue", "Cog"),
        ("Find the Missing Book in the Library (1 of 2)", "the Missing Book"),
        ("Collect Wild Bolete", "Wild Bolete"),
        ("Talk To Sergeant Muldoon in Olde Town", ""),
        ("Defeat Lost Soul in Unicorn Way (1 of 2)", ""),
    ],
)
def test_collect_item_name(objective, item):
    assert collect_item_name(objective) == item


def test_matches_item():
    assert matches_item("Cog", "WC_Cog_01")
    assert matches_item("Cogs", "Quest Cog")
    assert matches_item("Cog", "irrelevant", "Cog")
    assert not matches_item("Cog", "WispHealth", "Lost Soul")
    assert not matches_item("Cog", "")


def test_spread_points_keeps_spacing_nearest_first():
    from wiz101_auto.collect import spread_points

    pts = [(0, 0, 0), (100, 0, 0), (5000, 0, 0), (5100, 0, 0), (-9000, 0, 0)]
    assert spread_points(pts, (4000, 0, 0), 3000) == [(5000, 0, 0), (100, 0, 0), (-9000, 0, 0)]


def test_away_from_drops_points_near_mobs():
    from wiz101_auto.collect import away_from

    assert away_from([(0, 0, 0), (2000, 0, 0)], [(100, 0, 0)], 900) == [(2000, 0, 0)]


def test_matches_item_by_the_objects_kind_word():
    assert matches_item("Gemstones", "KT_Gem_Fire")
    assert not matches_item("Firecat Whiskers", "KT_Gem_Fire")
    assert not matches_item("Gemstones", "KT_WispHealth")


def test_duel_circles_are_not_landmarks():
    from wiz101_auto.collect import LANDMARK_NAMES

    assert "duel circle" not in LANDMARK_NAMES


def test_an_objective_for_two_kinds_matches_either():
    from wiz101_auto.collect import item_alternatives, matches_item

    both = ["Green Crystal Sample", "Purple Crystal Sample"]
    assert item_alternatives("Green and Purple Crystal Sample") == both
    assert matches_item("Green and Purple Crystal Sample", "Purple Crystal Sample")
    assert matches_item("Red and Orange Crystal Sample", "Red Crystal Sample")
    assert not matches_item("Green and Purple Crystal Sample", "Orange Crystal Sample")
    assert item_alternatives("Cog") == ["Cog"]


def test_loose_names_for_an_item_not_found_by_its_quest_name():
    from wiz101_auto.collect import loose_names, matches_item

    assert loose_names("Red Crystal Sample") == ["Red Crystal Sample", "Crystal Sample", "Crystal"]
    assert loose_names("Green and Purple Crystal Sample")[1:] == ["Crystal Sample", "Crystal"]
    assert loose_names("Cog") == ["Cog"]
    assert matches_item("Crystal Sample", "Crystal Sample")


def test_plural_items_match_their_own_plural_name():
    from wiz101_auto.collect import matches_item

    assert matches_item("Berries", "GH_Berries")
    assert matches_item("Berries", "Berries")
    assert matches_item("Supplies", "Supply Crate")


def test_steal_is_a_collect():
    from wiz101_auto.collect import collect_item_name

    objective = "Steal Barrel of Kermes Fire in Tyrian Gorge (0 of 6)"
    assert collect_item_name(objective) == "Barrel of Kermes Fire"
