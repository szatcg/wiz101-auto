from wiz101_auto.puzzles import gray_flips, is_switch, use_target


def test_gray_flips_visit_every_combination_once():
    state, seen = 0, {0}
    for i in gray_flips(6):
        state ^= 1 << i
        seen.add(state)
    assert len(gray_flips(6)) == 63 and len(seen) == 64  # all 2^6 on/off states


def test_use_target_and_switch_names():
    assert use_target("Use Oka's Chest in Grand Arena") == "Oka's Chest"
    assert use_target("Talk To Ako in Grand Arena") is None
    assert is_switch("KT_Obelisk_Sun") and is_switch("Brazier") and not is_switch("Oka's Chest")
