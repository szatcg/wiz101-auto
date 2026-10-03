from wiz101_auto.combat.calibrate import Round, measure


def test_a_shared_round_is_split_by_max_health():
    foes = [("Plague Oni", 1760, 1760), ("Imitsu Defouler", 675, 675), ("Imitsu Defouler 2", 675, 675)]
    fight = [Round(1, 1982, 1982, foes, "pass"), Round(2, 1671, 1982, foes, "pass")]
    _ours, _fizzles, _theirs, shared = measure([fight])
    assert round(shared["Plague Oni"][0]) == 176  # 311 x 1760/3110
    assert round(shared["Imitsu Defouler"][0]) == 68


def test_the_round_that_killed_us_counts_at_least_our_health():
    foes = [("Malistaire Drake", 944, 8000)]
    fight = [Round(21, 938, 2380, foes, "pass"), Round(22, 938, 2380, foes, "pass")]
    _o, _f, theirs, _s = measure([fight], [True])
    assert theirs["Malistaire Drake"] == [0, 938]
    _o, _f, theirs, _s = measure([fight], [False])
    assert theirs["Malistaire Drake"] == [0]


def test_activity_logs_read_rotated_copies_oldest_first(tmp_path):
    from wiz101_auto.combat.calibrate import activity_logs

    for name in ("activity.log", "activity.2026-09-30_18-18-26_1.log", "activity.2026-09-27_17-34-05_2.log"):
        (tmp_path / name).write_text("", encoding="utf-8")
    assert [p.name for p in activity_logs(tmp_path)] == [
        "activity.2026-09-27_17-34-05_2.log", "activity.2026-09-30_18-18-26_1.log", "activity.log"]
