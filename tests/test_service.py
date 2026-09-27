import json
import os

from wiz101_auto import service


def test_status_roundtrip(tmp_path, monkeypatch, capsys):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(service, "STATE", tmp_path / "state")
    monkeypatch.setattr(service, "STATUS_FILE", tmp_path / "state" / "status.json")
    monkeypatch.setattr(service, "PID_FILE", tmp_path / "state" / "bot.pid")
    monkeypatch.setattr(service, "LOG_FILE", tmp_path / "wiz101-auto.log")
    service.write_status(state="running", objective="Collect Cog")
    data = json.loads((tmp_path / "state" / "status.json").read_text())
    assert data["objective"] == "Collect Cog" and "time" in data
    (tmp_path / "wiz101-auto.log").write_text("line1\nline2\n")
    assert service.status() == 1  # not running
    out = capsys.readouterr().out
    assert "running: no" in out and "Collect Cog" in out and "line2" in out


def test_pid_alive_and_running_pid(tmp_path, monkeypatch):
    assert service.pid_alive(os.getpid())
    assert not service.pid_alive(0)
    monkeypatch.setattr(service, "PID_FILE", tmp_path / "bot.pid")
    (tmp_path / "bot.pid").write_text(str(os.getpid()))
    assert service.running_pid() == os.getpid()
    (tmp_path / "bot.pid").write_text("999999999")
    assert service.running_pid() == 0


def test_stop_when_not_running(tmp_path, monkeypatch):
    monkeypatch.setattr(service, "PID_FILE", tmp_path / "bot.pid")
    assert service.stop() == 0


def test_tail_missing_file(tmp_path):
    assert "not found" in service.tail(tmp_path / "nope.log", 5)
