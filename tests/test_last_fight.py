from wiz101_auto.dungeons import last_fight, note_last_fight


def test_last_fight_round_trip(tmp_path):
    f = tmp_path / "last_fights.json"
    assert last_fight("Z", f) is None
    note_last_fight("Z", (1.0, 2.0, 3.0), f)
    note_last_fight("Z", (4.0, 5.0, 6.0), f)
    note_last_fight("", (7.0, 8.0, 9.0), f)
    assert last_fight("Z", f) == (4.0, 5.0, 6.0)
    assert last_fight("other", f) is None
