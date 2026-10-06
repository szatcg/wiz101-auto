from wiz101_auto import loopwatch


def test_the_same_line_eight_times_in_ten_minutes_is_a_loop():
    loopwatch.reset()
    lines = [f"relogging: the wizard is stuck in place at ({i}, {i * 2})" for i in range(8)]
    found = [loopwatch.note(m, now=10.0 * i) for i, m in enumerate(lines)]
    assert found[:7] == [None] * 7 and found[7] is not None


def test_spread_out_or_fight_lines_are_not():
    loopwatch.reset()
    assert all(loopwatch.note("heading to WizardCity/WC_Hub", now=200.0 * i) is None for i in range(8))
    assert all(loopwatch.note(f"[round {i}] pips=1 -> pass", now=float(i)) is None for i in range(20))
