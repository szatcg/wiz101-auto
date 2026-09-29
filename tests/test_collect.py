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


def test_is_chest_matches_any_chest_name():
    from wiz101_auto.collect import is_chest

    assert is_chest("KT-Chest-Boss-001")
    assert is_chest("BossChest")
    assert not is_chest("Krokopatra")
    assert not is_chest("")
