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
    from wiz101_auto.farm import target_status

    looted = {"Zeus' Conjurer Hood": {"slot": "Hat"}, "Sky Iron Hasta": {"slot": "Wand"}}
    status = {t["name"]: t["have"] for t in target_status("Mount Olympus", looted)}
    assert status["Zeus' Conjurer Hood"] and status["Sky Iron Hasta"]
    assert not status["Zeus' Conjurer Raiment"] and not status["Senator's Conjurer Tunic"]


def test_raiment_is_a_robe_and_hasta_a_wand():
    from wiz101_auto.gear import is_wand, item_slot

    assert item_slot(["Zeus' Conjurer Raiment"]) == "Tab_Robe"
    assert is_wand(["Sky Iron Hasta"]) and item_slot(["Sky Iron Hasta"]) is None
