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
