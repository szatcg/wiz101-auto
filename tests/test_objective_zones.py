from wiz101_auto.quest import given_zone, note_objective_zone


def test_first_zone_kept(tmp_path):
    p = tmp_path / "oz.json"
    assert given_zone("Help Khan Impress the Muse in Zanadu", p) is None
    note_objective_zone("Help Khan Impress the Muse in Zanadu", "Empyrea/Interiors/EM_Z02_DanceHall", p)
    note_objective_zone("Help Khan Impress the Muse in Zanadu", "Empyrea/EM_Z02_Zanadu", p)
    assert given_zone("Help Khan Impress the Muse in Zanadu", p) == "Empyrea/Interiors/EM_Z02_DanceHall"


def test_bad_gates_kept(tmp_path):
    from wiz101_auto.quest import load_bad_gates, save_bad_gate

    p = tmp_path / "bg.json"
    assert load_bad_gates(p) == set()
    save_bad_gate("Empyrea/EM_Z00_Aeriel_HUB", "Empyrea/Interiors/EM_Z02_Sewers", p)
    save_bad_gate("Empyrea/EM_Z00_Aeriel_HUB", "Empyrea/Interiors/EM_Z02_Sewers", p)
    assert load_bad_gates(p) == {("Empyrea/EM_Z00_Aeriel_HUB", "Empyrea/Interiors/EM_Z02_Sewers")}
