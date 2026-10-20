from wiz101_auto.streamportal import choose_stop, load_stops, save_stop

OPTIONS = ["Aeriel", "Zanadu", "Outer Athanor", "Inner Athanor", "Sepidious", "Mandalla"]


def never(a, b):
    return False


def test_learned_landing_wins():
    learned = {"Mandalla": "Empyrea/EM_Z12_LandingZone", "Aeriel": "Empyrea/EM_Z00_Aeriel_HUB"}
    stop = choose_stop(OPTIONS, "Empyrea/EM_Z12_LandingZone", "Empyrea/EM_Z00_Aeriel_HUB", learned, never, [])
    assert stop == "Mandalla"


def test_route_from_landing():
    learned = {"Mandalla": "Empyrea/EM_Z12_LandingZone"}

    def reaches(a, b):
        return (a, b) == ("Empyrea/EM_Z12_LandingZone", "Empyrea/EM_Z12_SamsaraVillage")

    stop = choose_stop(OPTIONS, "Empyrea/EM_Z12_SamsaraVillage", "Empyrea/EM_Z00_Aeriel_HUB", learned,
                       reaches, ["samsaravillage"])
    assert stop == "Mandalla"


def test_same_area():
    learned = {"Sepidious": "Empyrea/EM_Z05_Sepidious"}
    stop = choose_stop(OPTIONS, "Empyrea/Interiors/EM_Z05_RightEye", "Empyrea/EM_Z00_Aeriel_HUB", learned,
                       never, ["righteye"])
    assert stop == "Sepidious"


def test_never_the_stop_we_are_at():
    learned = {"Mandalla": "Empyrea/EM_Z12_LandingZone"}
    stop = choose_stop(OPTIONS, "Empyrea/EM_Z00_Aeriel_HUB", "Empyrea/EM_Z12_LandingZone", learned, never,
                       ["aeriel", "mandalla"])
    assert stop == "Aeriel"


def test_names_when_nothing_learned():
    stop = choose_stop(OPTIONS, "Empyrea/EM_Z03_OuterAthanor", "Empyrea/EM_Z00_Aeriel_HUB", {}, never,
                       ["outerathanor"])
    assert stop == "Outer Athanor"
    assert choose_stop(OPTIONS, "Empyrea/EM_Z99_Nowhere", "x", {}, never, ["nowhere"]) == "Aeriel"


def test_stops_kept(tmp_path):
    p = tmp_path / "stops.json"
    save_stop("Empyrea", "Mandalla", "Empyrea/EM_Z12_LandingZone", p)
    assert load_stops("Empyrea", p) == {"Mandalla": "Empyrea/EM_Z12_LandingZone"}


def test_hub_never_team_dungeon():
    from wiz101_auto.teamup import is_open_hub

    assert is_open_hub("Empyrea/EM_Z00_Aeriel_HUB")
    assert is_open_hub("WizardCity/WC_Hub")
    assert not is_open_hub("Empyrea/Interiors/EM_Z15_DreamShrine")


def test_objective_person():
    from wiz101_auto.quest import objective_person

    assert objective_person("Follow Josiah into Town in Port Aero") == "Josiah"
    assert objective_person("Talk To Sparck in Zanadu") == "Sparck"
    assert objective_person("Defeat Baane in Bat Cave") == ""


def test_unlearned_stop_tried():
    learned = {o: f"Empyrea/EM_Z0{i}_X" for i, o in enumerate(OPTIONS)}
    stop = choose_stop([*OPTIONS, "Port Aero"], "Empyrea/EM_Z08_SWAeroplains", "Empyrea/EM_Z00_X", learned,
                       never, ["swaeroplains"])
    assert stop == "Port Aero"


def test_play_is_operated():
    from wiz101_auto.quest import operate_target

    assert operate_target("Play Whirlyburly in Velo City") == "Whirlyburly"


def test_walk_zones_learned(tmp_path, monkeypatch):
    from wiz101_auto import safe_teleport

    monkeypatch.setattr(safe_teleport, "LEARNED_WALK_FILE", tmp_path / "walk.json")

    class C:
        pass

    assert safe_teleport.walk_zone(C(), "Empyrea/Interiors/EM_Z07_PrivateWing")
    assert safe_teleport.learn_walk_zone("Empyrea/Interiors/EM_Z99_Room")
    assert not safe_teleport.learn_walk_zone("Empyrea/Interiors/EM_Z99_Room")
    assert safe_teleport.walk_zone(C(), "Empyrea/Interiors/EM_Z99_Room")
