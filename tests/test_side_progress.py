from wiz101_auto.givers import GuideQuest
from wiz101_auto.questlist import ListedQuest
from wiz101_auto.side_progress import area_of_zone, area_progress, remember_book, side_tag_count


def test_side_quests_of_an_area_from_the_list_and_the_book(tmp_path):
    guide = [GuideQuest("Ken Shui", "Be Very, Very Quiet", None, True, "VILLAGE OF SORROW"),
             GuideQuest("Old Mo", "Lost Lantern", None, False, "VILLAGE OF SORROW"),
             GuideQuest("Bo", "Far Away", None, False, "TREE OF LIFE")]
    known = remember_book({"quests": [
        {"name": "Ghost Hunt", "area": "Village of Sorrow", "main": False},
        {"name": "Or Call a Locksmith", "area": "Village of Sorrow", "main": False},  # story, called side
    ]}, tmp_path / "areas.json")
    listed = [ListedQuest(1, "Or Call a Locksmith", "Village of Sorrow", ["D&C", "TALK"])]
    p = area_progress("Village of Sorrow", guide, known, listed, ["Lost Lantern"])
    assert (p["done"], p["total"], p["pct"]) == (1, 2, 50)


def test_side_tags_set_a_floor_for_the_total():
    listed = [ListedQuest(1, "MooShu", "Jade Palace", ["SIDE ×9", "EXPLORE"])]
    assert side_tag_count(listed[0].tags) == 9
    assert area_progress("Jade Palace", None, {}, listed, [])["total"] == 9


def test_area_of_zone():
    zones = [("crimson fields", "MooShu/MS_War/MS_War_BattlefieldA"), ("knight's court", "MB/KC")]
    assert area_of_zone("MooShu/MS_War/MS_War_BattlefieldA", zones) == "Crimson Fields"
    assert area_of_zone("MB/KC", zones) == "Knight's Court"
    assert area_of_zone("MooShu/Interiors/X", zones) == ""
