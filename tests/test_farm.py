from wiz101_auto.farm import Farm


def test_farm_runs_end_on_the_final_boss_and_count(tmp_path):
    path = tmp_path / "farm.json"
    farm = Farm(active=True)
    assert farm.ends_run(["Zeus Sky Father"])
    assert not farm.ends_run(["Ares Savage Spear", "Apollo Bright One"])
    farm.record_run(path)
    farm.record_run(path)
    again = Farm.load(path)
    assert again.runs == 2 and again.active and again.final_boss == "Zeus Sky Father"


def test_targets_fill_in_as_looted():
    from wiz101_auto.farm import Farm, target_status

    looted = {"Helmet of Zeus' Will": {"slot": "Hat"}, "Zeus' Armor of Supremacy": {"slot": "Robe"}}
    status = {t["name"]: t["have"] for t in target_status("Mount Olympus", looted)}
    assert status["Helmet of Zeus' Will"] and status["Zeus' Armor of Supremacy"]
    assert not status["Boots of Zeus' Lore"]
    assert not Farm().targets_done(looted)
    assert Farm().targets_done({**looted, "Boots of Zeus' Lore": {"slot": "Shoes"}})


def test_raiment_is_a_robe_and_hasta_a_wand():
    from wiz101_auto.gear import is_wand, item_slot

    assert item_slot(["Zeus' Conjurer Raiment"]) == "Tab_Robe"
    assert is_wand(["Sky Iron Hasta"]) and item_slot(["Sky Iron Hasta"]) is None
