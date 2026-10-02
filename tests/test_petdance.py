from wiz101_auto import petdance


def test_moves_decode_to_wasd():
    assert petdance.decode_moves(b"acbd\0\0\0\0") == "WSDA"
    assert petdance.decode_moves(b"\0" * 8) == ""


def test_energy_numbers():
    assert petdance.first_number("Energy: 12/45") == 12
    assert petdance.first_number("Cost: 5") == 5
    assert petdance.first_number("") is None


def test_request(tmp_path, monkeypatch):
    monkeypatch.setattr(petdance, "PET_REQUEST", tmp_path / "pet.request")
    assert petdance.games_requested() is None
    (tmp_path / "pet.request").write_text("0")
    assert petdance.games_requested() == 0
    (tmp_path / "pet.request").write_text("3")
    assert petdance.games_requested() == 3


def test_stage_and_goal():
    assert petdance.stage_in(["Your pet is now an Adult!", "Teen"]) == "adult"
    assert petdance.stage_in(["nothing here"]) is None
    assert petdance.kind_in(["Rudy the Bloodbat"], ["bloodbat"]) == "bloodbat"
    goals = {"bloodbat": "adult"}
    assert not petdance.goal_reached("bloodbat", "teen", goals, "mega")
    assert petdance.goal_reached("bloodbat", "adult", goals, "mega")
    assert not petdance.goal_reached("wolf", "adult", goals, "mega")
    assert petdance.goal_reached("wolf", "mega", goals, "mega")
