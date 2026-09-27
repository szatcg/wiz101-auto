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



def test_item_slot_from_template_strings():
    from wiz101_auto.gear import item_slot

    assert item_slot(["WC_Boots_Graven", "", "", "Graven Boots"]) == "Tab_Shoes"
    assert item_slot(["", "Hat", "IconHatMyth"]) == "Tab_Hat"
    assert item_slot(["Trollskin Cloak"]) == "Tab_Robe"
    assert item_slot(["", "", "", "Ring of Resolve"]) == "Tab_Ring"
    assert item_slot(["", "", "", "Bandit's Boots"]) == "Tab_Shoes"  # "bandit" isn't a band
    assert item_slot(["HS_Chair_Wooden", "Housing", ""]) is None
