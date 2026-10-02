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


def test_restarts_are_counted(tmp_path, monkeypatch):
    monkeypatch.setattr(gamerestart, "HISTORY", tmp_path / "hist")
    now = time.time()
    (tmp_path / "hist").write_text(f"{now - 4000:.0f}\n{now - 100:.0f}\n{now - 50:.0f}\n", encoding="utf-8")
    assert gamerestart.recent_restarts(now) == 2


def test_one_restart_per_15_minutes(tmp_path, monkeypatch):
    monkeypatch.setattr(gamerestart, "HISTORY", tmp_path / "hist")
    now = time.time()
    assert gamerestart.wait_before_restart(now) == 0
    (tmp_path / "hist").write_text(f"{now - 300:.0f}\n", encoding="utf-8")
    assert 590 < gamerestart.wait_before_restart(now) <= 600
    (tmp_path / "hist").write_text(f"{now - 1000:.0f}\n", encoding="utf-8")
    assert gamerestart.wait_before_restart(now) == 0


def test_the_death_realm_is_no_return():
    from wiz101_auto.dungeons import no_return

    assert no_return("MooShu/MS_Death/Interiors/MS_Death3_SpiritWorld")
    assert not no_return("MooShu/MS_Death/Interiors/MS_Death3_T4")


def test_bot_game_pid_round_trip(tmp_path, monkeypatch):
    from wiz101_auto import gamerestart

    monkeypatch.setattr(gamerestart, "GAME_FILE", tmp_path / "game.json")
    assert gamerestart.bot_game_pid() == 0
    gamerestart.remember_game(1234, 99)
    assert gamerestart.bot_game_pid() == 1234
