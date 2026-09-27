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
