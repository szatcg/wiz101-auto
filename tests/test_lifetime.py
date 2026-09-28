from wiz101_auto import lifetime


def test_deaths_seeded_from_the_log_per_world_then_counted(tmp_path):
    log = tmp_path / "bot.log"
    log.write_text(
        "x | INFO | [WizardCity/WC_Streets/WC_Triton] Defeat X\n"
        "x | WARNING | wizard defeated (1/12)\n"
        "x | INFO | working on: Y [Krokotopia/KT_Pyramid/KT_Hall] for 3s\n"
        "x | INFO | [round 2] pips=1 -> cast Troll\n"
        "x | WARNING | wizard defeated (2/12)\n",
        encoding="utf-8",
    )
    state = tmp_path / "lifetime.json"
    data = lifetime.load(state, log)
    assert data["deaths"] == 2
    assert data["deaths_by_world"] == {"Wizard City": 1, "Krokotopia": 1}
    assert lifetime.add_death("Krokotopia", state, log) == 3
    assert lifetime.load(state, log)["deaths_by_world"]["Krokotopia"] == 2  # the file wins from now on
