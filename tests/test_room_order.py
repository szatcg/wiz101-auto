from wiz101_auto.teamup import advance_room, next_room, room_order

W = "WizardCity/Gauntlets/WC_Triton_Gauntlet1/WC_Triton_Gauntlet_"


def test_a_waterworks_run_goes_room_by_room():
    order = room_order(W + "01")
    pos = -1
    seen = []
    for z in ("01", "01a", "02", "01", "03", "04", "01", "05", "06", "01", "07", "08", "01"):
        pos = advance_room(order, pos, W + z)
        seen.append(pos)
    assert seen == list(range(13))
    assert next_room(order, pos) is None


def test_next_room_after_each_passage_is_back_to_the_entrance():
    order = room_order(W + "02")
    pos = advance_room(order, -1, W + "01")
    assert next_room(order, pos) == W + "01a"
    pos = advance_room(order, pos, W + "01a")  # followed the team on
    pos = advance_room(order, pos, W + "02")
    assert next_room(order, pos) == W + "01"
    for z in ("01", "03", "04"):  # Luska's room
        pos = advance_room(order, pos, W + z)
    assert next_room(order, pos) == W + "01"
    pos = advance_room(order, pos, W + "01")
    assert next_room(order, pos) == W + "05"
    assert advance_room(order, pos, W + "08") == pos  # never several rooms on at once


def test_back_at_the_entrance_after_a_defeat_keeps_the_place():
    order = room_order(W + "01")
    pos = advance_room(order, -1, W + "06")
    assert advance_room(order, pos, W + "05") == pos  # a room passed before
    assert room_order("Zafaria/ZF_Z00_Hub") == []


def test_team_potions_keep_one_for_the_final_boss():
    from wiz101_auto.upkeep import team_potion

    assert not team_potion(0.5, 3)  # above 35%: saved
    assert team_potion(0.3, 3)
    assert team_potion(0.3, 2)
    assert not team_potion(0.2, 1)  # the last one is Sylster's
    assert team_potion(0.6, 1, before_final=True)
    assert not team_potion(0.95, 1, before_final=True)
    assert not team_potion(0.1, 0, before_final=True)


def test_a_sigil_key_marks_its_dungeon_as_a_team_zone(tmp_path, monkeypatch):
    # The Pendragon's keep put on the team list by its sigil before it was
    # ever entered ("zone@x,y"): once learned, its rooms are team play.
    from wiz101_auto import dungeons, teamup

    monkeypatch.setattr(teamup, "TEAM_LIST_FILE", tmp_path / "team_dungeons.json")
    teamup.add_team_dungeon("Avalon/AV_Z07_OuterYard@1200,-300", "The Guns of Avalon")
    mem = dungeons.DungeonMemory(tmp_path / "dungeons.json")
    mem.record_entry("Avalon/Interiors/AV_Z13_Keep", dungeons.DungeonEntry(
        outside="Avalon/AV_Z07_OuterYard", sigil=(1210, -290, 0), spawn=(0, 0, 0), yaw=0.0))
    monkeypatch.setattr(dungeons.DungeonMemory, "load", classmethod(lambda cls, *a, **k: mem))
    assert teamup.is_team_up_zone("Avalon/Interiors/AV_Z13_Keep")
    assert not teamup.is_team_up_zone("Avalon/AV_Z07_OuterYard")


def test_no_door_at_the_origin(tmp_path):
    from wiz101_auto.entitymap import DoorMemory

    mem = DoorMemory(tmp_path / "doors.json")
    tree, school = "WizardCity/WC_Ravenwood_Teleporter", "WizardCity/Interiors/WC_SchoolMyth"
    mem.record(tree, (0, 0), (648, 14, 73), school)
    assert mem.leading_to(tree, school) == []
