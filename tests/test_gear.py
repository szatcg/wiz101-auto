from wiz101_auto.gear import StatSnapshot, _pick, gear_score


def test_any_real_item_beats_an_empty_slot():
    empty = StatSnapshot(health=585, mana=30)
    hat = StatSnapshot(health=610, mana=30, resist=0.02)
    assert gear_score(hat) > gear_score(empty)


def test_damage_for_own_school_outweighs_a_little_health():
    tanky = StatSnapshot(health=600)
    hitter = StatSnapshot(health=590, damage=0.05)
    assert gear_score(hitter) > gear_score(tanky)


def test_pick_reads_the_school_slot_plus_all_schools():
    assert _pick([0.0, 0.0, 0.0, 0.04], "myth", 0.01) == 0.05
    assert _pick([], "myth", 0.02) == 0.02


def test_only_wearable_gear_counts_as_a_new_item():
    from wiz101_auto.gear import is_wearable

    assert is_wearable("Hat-T1-018") and is_wearable("Shoe-T1-011") and is_wearable("Athame-T1-008")
    assert not is_wearable("Emote2_Oppa") and not is_wearable("Mount-Broom-Basic-001")
    assert not is_wearable("Deck-T1-001") and not is_wearable("Wand-T1-L0-All")
