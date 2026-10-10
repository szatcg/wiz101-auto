

def test_team_list_adds_and_drops(tmp_path, monkeypatch):
    from wiz101_auto import teamup

    monkeypatch.setattr(teamup, "TEAM_LIST_FILE", tmp_path / "team_dungeons.json")
    tent = "Zafaria/Interiors/ZF_Z08_I02_Belloqs_Tent"
    assert not teamup.is_team_up_zone(tent)
    teamup.add_team_dungeon(tent, "Under the Big Tent")
    assert teamup.is_team_up_zone(tent) and teamup.is_team_dungeon(tent)
    assert teamup.drop_team_dungeons({"Under the Big Tent"}) == []
    assert teamup.drop_team_dungeons({"Something Else"}) == [tent]
    assert not teamup.is_team_up_zone(tent)


def test_later_rooms_of_a_listed_dungeon_are_team_rooms(tmp_path, monkeypatch):
    from wiz101_auto import teamup

    monkeypatch.setattr(teamup, "TEAM_LIST_FILE", tmp_path / "team_list.json")
    teamup.add_team_dungeon("Azteca/Interiors/AZ_Z08_PyramidMotherMoon_Room01", "Hum a Few Bars")
    assert teamup.in_team_list("Azteca/Interiors/AZ_Z08_PyramidMotherMoon_Room03")
    assert not teamup.in_team_list("Azteca/Interiors/AZ_Z08_UnderwaterCat")


def test_a_team_dungeon_stays_until_its_quest_leaves_the_book(tmp_path, monkeypatch):
    from wiz101_auto import teamup

    monkeypatch.setattr(teamup, "TEAM_LIST_FILE", tmp_path / "team_list.json")
    teamup.add_team_dungeon("Azteca/AZ_Z12_Xibalba", "This Is the Way the World Ends")
    # Not in the book yet (or a read that missed it): kept.
    assert teamup.drop_team_dungeons({"Tall Enough to Meet the Sun"}, {"Tall Enough to Meet the Sun"}) == []
    # In the book last time, gone now: done.
    assert teamup.drop_team_dungeons(set(), {"This Is the Way the World Ends"}) == ["Azteca/AZ_Z12_Xibalba"]


def test_team_size_goes_to_four_after_a_loss(tmp_path, monkeypatch):
    from wiz101_auto import teamup

    monkeypatch.setattr(teamup, "TEAM_LIST_FILE", tmp_path / "team_list.json")
    monkeypatch.setattr(teamup, "TEAM_SIZE_FILE", tmp_path / "team_sizes.json")
    teamup.add_team_dungeon("Azteca/Interiors/AZ_Z11_PyramidFallingStar_Room01", "Not a Cold Dead Place")
    assert teamup.team_size("Azteca/Interiors/AZ_Z11_PyramidFallingStar_Room01") == 2
    teamup.set_team_size("Azteca/Interiors/AZ_Z11_PyramidFallingStar_Room03", 4)  # lost in a later room
    assert teamup.team_size("Azteca/Interiors/AZ_Z11_PyramidFallingStar_Room01") == 4


def test_a_room_before_the_listed_one_is_no_team_room(tmp_path, monkeypatch):
    from wiz101_auto import teamup

    monkeypatch.setattr(teamup, "TEAM_LIST_FILE", tmp_path / "team_list.json")
    teamup.add_team_dungeon("Khrysalis/Interiors/KR_Z02_MoonCliffs_Int01_02", "Moonlight Shadows")
    assert teamup.in_team_list("Khrysalis/Interiors/KR_Z02_MoonCliffs_Int01_03")
    assert not teamup.in_team_list("Khrysalis/Interiors/KR_Z02_MoonCliffs_Int01_01")
    teamup.add_team_dungeon("Khrysalis/Interiors/KR_Z04_I03CanyonWatchTower_01", "Take Me In")
    assert teamup.in_team_list("Khrysalis/Interiors/KR_Z04_I03_CanyonWatchTower")
