from wiz101_auto import lifetime


def test_deaths_seeded_from_the_log_then_counted(tmp_path):
    log = tmp_path / "bot.log"
    log.write_text("x | WARNING | wizard defeated (1/12)\nother\nx | WARNING | wizard defeated (2/12)\n",
                   encoding="utf-8")
    state = tmp_path / "lifetime.json"
    assert lifetime.load(state, log)["deaths"] == 2
    assert lifetime.add_death(state, log) == 3
    assert lifetime.load(state, log)["deaths"] == 3  # the file wins over the log from now on
