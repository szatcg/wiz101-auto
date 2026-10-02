def test_control_actions_write_their_requests(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    from wiz101_auto import control, service

    monkeypatch.setattr(service, "running_pid", lambda: 1234)
    assert control.act("pause")["ok"] and control.PAUSE_REQUEST.exists()
    assert control.act("pet", {"games": 3})["ok"]
    assert (tmp_path / "state" / "pet.request").read_text() == "3"
    assert control.act("start")["ok"] is False  # (already running)
    assert control.act("bogus")["ok"] is False
