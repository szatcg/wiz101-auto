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


def test_gear_memory_skips_beaten_items_and_retries_locked_on_level_up(tmp_path):
    from wiz101_auto.gear import GearMemory

    m = GearMemory(tmp_path / "gear.json")
    m.mark("Tab_Hat", "Soft Hood", "worse")
    m.mark("Tab_Hat", "Alicane's Cowl", "locked")
    m.save()
    m = GearMemory(tmp_path / "gear.json")
    names = ["Soft Hood", "Alicane's Cowl", "Brand New Helm"]
    assert m.candidates("Tab_Hat", names, level_up=False) == ["Brand New Helm"]
    assert m.candidates("Tab_Hat", names, level_up=True) == ["Alicane's Cowl", "Brand New Helm"]


def test_gear_memory_keeps_the_item_to_put_back(tmp_path):
    from wiz101_auto.gear import GearMemory

    m = GearMemory(tmp_path / "gear.json")
    m.restore["Tab_Robe"] = "Senior Novice's Robe"
    m.save()
    assert GearMemory(tmp_path / "gear.json").restore == {"Tab_Robe": "Senior Novice's Robe"}


def test_own_school_flat_damage_counts():
    from wiz101_auto.gear import StatSnapshot, gear_score

    plain = StatSnapshot(health=845)
    assert gear_score(StatSnapshot(health=845, flat_damage=2)) > gear_score(plain)
    # +60 health (Cloak of Tales) beats +2 damage (Trollskin Cloak)
    assert gear_score(StatSnapshot(health=905)) > gear_score(StatSnapshot(health=845, flat_damage=2))


def test_main_quests_get_five_tries(tmp_path):
    from wiz101_auto.setbacks import Setbacks

    s = Setbacks(tmp_path / "s.json")
    boss = "Defeat Akori Nirini in Akori's Chamber"
    assert not any(s.record_defeat(boss, "Payback", 14, now=0, main=True) for _ in range(4))
    assert s.record_defeat(boss, "Payback", 14, now=0, main=True)  # the fifth loss
