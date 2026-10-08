

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
