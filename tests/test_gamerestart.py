import time

from wiz101_auto import gamerestart


def test_restart_reasons(tmp_path, monkeypatch):
    monkeypatch.setattr(gamerestart, "REQUEST", tmp_path / "req")
    monkeypatch.setattr(gamerestart, "game_windows", lambda: [123])
    assert gamerestart.needs_restart("") == ""
    assert "hook" in gamerestart.needs_restart("Could not hook into the game: ...")
    gamerestart.request("game frozen: a loading screen for 301s")
    assert gamerestart.needs_restart("") == "game frozen: a loading screen for 301s"
    (tmp_path / "req").unlink()
    monkeypatch.setattr(gamerestart, "game_windows", lambda: [])
    assert "no game window" in gamerestart.needs_restart("")


def test_restarts_are_capped_per_hour(tmp_path, monkeypatch):
    monkeypatch.setattr(gamerestart, "HISTORY", tmp_path / "hist")
    now = time.time()
    (tmp_path / "hist").write_text(f"{now - 4000:.0f}\n{now - 100:.0f}\n{now - 50:.0f}\n", encoding="utf-8")
    assert gamerestart.recent_restarts(now) == 2


def test_the_death_realm_is_no_return():
    from wiz101_auto.dungeons import no_return

    assert no_return("MooShu/MS_Death/Interiors/MS_Death3_SpiritWorld")
    assert not no_return("MooShu/MS_Death/Interiors/MS_Death3_T4")
