from wiz101_auto.farm import PRESETS, Farm, target_status, unlock_quest


def test_darkmoor_preset():
    dungeon, name, boss, unlock = PRESETS["darkmoor"]
    assert (name, boss, unlock) == ("Darkmoor Graveyard", "Yevgeny NightCreeper", "Castle Darkmoor")
    assert [t["name"] for t in target_status(name, {})] == ["Lord of Death's Razor"]
    assert target_status(name, {"Lord of Death's Razor": {}})[0]["have"]


def test_run_ends_on_the_boss():
    farm = Farm(final_boss="Yevgeny NightCreeper")
    assert farm.ends_run(["Yevgeny Nightcreeper"])
    assert not farm.ends_run(["Howling Chaney"])


def test_unlock_quest_prefers_the_tracked_one():
    book = [
        {"name": "Stream On", "world": "Empyrea"},
        {"name": "Le Chateau de Darkmoor", "world": "Castle Darkmoor"},
        {"name": "Upper Halls", "world": "Castle Darkmoor", "active": True},
    ]
    assert unlock_quest("Castle Darkmoor", book) == "Upper Halls"
    assert unlock_quest("Castle Darkmoor", book[:1]) == ""


def test_darkmoor_never_alone():
    from wiz101_auto.teamup import no_solo

    assert no_solo("G14_DM/DM_Z02_MainHall")
    assert not no_solo("Empyrea/Interiors/EM_Z08_MeowlsDen")


def test_darkmoor_sigils_always_team():
    from wiz101_auto.teamup import is_team_dungeon

    assert is_team_dungeon("G14_DM/DM_Z01_CastleDarkmoor@20,279")
    assert is_team_dungeon("G14_DM/DM_Z04_LandingZone2")
    assert not is_team_dungeon("Empyrea/Interiors/EM_Z99_Nowhere")
