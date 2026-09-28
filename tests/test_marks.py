from wiz101_auto.marks import Mark, load_mark, recall_is_faster, save_mark, should_travel_mark

# A line of zones: Hub - A - B - C
LINE = ["Hub", "A", "B", "C"]


def hops(a, b):
    if a in LINE and b in LINE:
        return abs(LINE.index(a) - LINE.index(b))
    return None


def test_marks_before_a_long_trip_only():
    assert should_travel_mark("C", "Hub", hops, None, False, set())
    assert not should_travel_mark("B", "C", hops, None, False, set())  # next door: just walk
    assert not should_travel_mark("C", None, hops, None, False, set())  # destination unknown


def test_never_marks_inside_a_dungeon_or_over_a_needed_dungeon_mark():
    assert not should_travel_mark("C", "Hub", hops, None, False, {"C"})
    dungeon = Mark("B", "Defeat Boss", "dungeon")
    assert not should_travel_mark("C", "Hub", hops, dungeon, True, set())
    assert should_travel_mark("C", "Hub", hops, dungeon, False, set())


def test_recalls_when_the_mark_is_nearer():
    mark = Mark("C", "Defeat Gobblers", "travel")
    assert recall_is_faster("Hub", "C", mark, hops)  # 0 + 1 < 3
    assert not recall_is_faster("Hub", "B", mark, hops)  # 1 + 1 == 2: a tie walks
    assert not recall_is_faster("A", "B", mark, hops)  # walking one zone is quicker
    assert not recall_is_faster("C", "Hub", mark, hops)  # already at the mark


def test_unknown_route_recalls_only_into_the_destination():
    mark = Mark("C", "x", "travel")
    assert recall_is_faster("Tower Interior", "C", mark, hops)
    assert not recall_is_faster("Tower Interior", "B", mark, hops)


def test_mark_file_round_trip_and_old_format(tmp_path):
    f = tmp_path / "mark.json"
    save_mark(Mark("C", "Defeat X", "travel"), f)
    assert load_mark(f) == Mark("C", "Defeat X", "travel")
    f.write_text('["Krokotopia/KT_Pyramid/KT_PalaceOfFire", "Defeat Shai"]', encoding="utf-8")
    assert load_mark(f) == Mark("Krokotopia/KT_Pyramid/KT_PalaceOfFire", "Defeat Shai", "dungeon")
