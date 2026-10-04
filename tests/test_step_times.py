import datetime as dt

from wiz101_auto.dashboard import objective_times


def test_each_objective_took_until_the_next_began():
    lines = [
        "21:24:18 | SUCCESS | objective done -> now: \"Use Baker's Oven in Hrundle Fjord\"",
        "21:30:00 | INFO    | something else",
        "21:40:00 | SUCCESS | objective done -> now: 'Use Beehive in Austrilund'",
        "21:40:11 | SUCCESS | objective done -> now: 'Talk To Skeggis Forkbeak in Austrilund'",
    ]
    now = dt.datetime(2026, 10, 3, 21, 45).timestamp()
    t = objective_times(lines, now)
    assert t["took"] == {"Use Baker's Oven in Hrundle Fjord": 942, "Use Beehive in Austrilund": 11}
    assert now - t["started"]["Talk To Skeggis Forkbeak in Austrilund"] == 289


def test_times_after_now_are_yesterdays():
    now = dt.datetime(2026, 10, 4, 0, 5).timestamp()
    t = objective_times(["23:59:00 | SUCCESS | objective done -> now: 'X'"], now)
    assert now - t["started"]["X"] == 360
