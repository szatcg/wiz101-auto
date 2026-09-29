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
