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


def test_place_from_learned_zone():
    from wiz101_auto.travel_data import objective_zone

    learned = ["Empyrea/EM_Z02_Zanadu", "Empyrea/Interiors/EM_Z02_BatCave"]
    assert objective_zone("Defeat Baane in Bat Cave", learned) == "Empyrea/Interiors/EM_Z02_BatCave"
    cave = "Empyrea/Interiors/EM_Z02_BatCave"
    assert objective_zone("Defeat Bat Bot in Bat Cave (1 of 2)", learned) == cave
    assert objective_zone("Defeat Nobody in Nowhere Land", learned) is None


def test_player_pin_kept(tmp_path, monkeypatch):
    import wiz101_auto.quest as q

    monkeypatch.setattr(q, "PIN_FILE", tmp_path / "pin.json")
    q.save_pin("To the Bat's Cave!")
    assert q.player_pinned() == ""
    q.save_pin("Le Chateau de Darkmoor", player=True)
    q.save_pin("Le Chateau de Darkmoor")
    assert q.player_pinned() == "Le Chateau de Darkmoor"


def test_solo_try_covers_quest_rooms():
    import wiz101_auto.teamup as t

    teams = {"Empyrea/Interiors/EM_Z02_SecretEntrance": "To the Bat's Cave!",
             "Empyrea/Interiors/EM_Z02_BatCave": "To the Bat's Cave!"}
    t._SOLO_UNTIL.clear()
    assert not t.solo_quest_room("Empyrea/Interiors/EM_Z02_BatCave", teams)
    t.solo_for("Empyrea/Interiors/EM_Z02_SecretEntrance", 60)
    try:
        assert t.solo_quest_room("Empyrea/Interiors/EM_Z02_BatCave", teams)
    finally:
        t._SOLO_UNTIL.clear()


def test_new_train_pet_not_done(tmp_path, monkeypatch):
    import wiz101_auto.petdance as pd
    from wiz101_auto.config import PetConfig

    monkeypatch.setattr(pd, "PET_STATE", tmp_path / "pet.json")
    pd.save_pet({"kind": None, "stage": "mega"})
    dancer = pd.PetDancer.__new__(pd.PetDancer)
    dancer.cfg = PetConfig(default_goal="mega", train_pet="Twitch Champ", battle_pet="Sir Roscoe")
    assert not dancer.done()  # a new pet: trained until its own goal
    pd.save_pet({"name": "twitch champ", "stage": "mega"})
    assert dancer.done()


def test_stream_words():
    from wiz101_auto.quest import stream_words

    assert stream_words("Explore Sepidious in The Stomach") == ["Sepidious", "Stomach"]


def test_pet_name_like():
    from wiz101_auto.petdance import pet_name_like

    assert pet_name_like("twitch champ", "Twitchy Champ")
    assert not pet_name_like("twitch champ", "Sir Roscoe")
