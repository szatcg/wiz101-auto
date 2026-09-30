from wiz101_auto.collect import walk_route


def test_route_climbs_a_staircase_instead_of_going_straight():
    start, goal = (0, 0, 0), (0, 400, 1000)
    stairs = [(400, 0, 200), (800, 0, 400), (800, 400, 600), (400, 400, 800)]
    route = walk_route(stairs, start, goal)
    assert route[-1] == goal
    assert [p[2] for p in route] == sorted(p[2] for p in route)  # always climbing
    assert (800, 0, 400) in route


def test_no_route_when_nothing_links():
    assert walk_route([(5000, 0, 0)], (0, 0, 0), (0, 0, 2000)) == []


def test_team_up_zones():
    from wiz101_auto.teamup import is_team_up_zone

    assert is_team_up_zone("Aquila/AQ_Z01_MountOlympus")
    assert is_team_up_zone("Aquila/Interiors/AQ_Z01_Apollo_Room")
    assert not is_team_up_zone("Aquila/AQ_Z00_Hub")


def test_team_fight_is_a_circle_with_a_teammate_in_it():
    from wizwalker import XYZ

    from wiz101_auto.teamup import team_fight_at

    circles = [XYZ(0, 0, 0), XYZ(3000, 0, 0)]
    assert team_fight_at(circles, [XYZ(3100, 50, 0)]) is circles[1]
    assert team_fight_at(circles, [XYZ(1500, 0, 0)]) is None  # nobody in a circle
    assert team_fight_at(circles, []) is None


def test_players_on_sigil():
    from wizwalker import XYZ

    from wiz101_auto.teamup import players_on_sigil

    sigil = XYZ(0, 0, 0)
    assert players_on_sigil(sigil, [XYZ(100, 50, 0), XYZ(-200, 0, 0), XYZ(2000, 0, 0)]) == 2
    assert players_on_sigil(sigil, []) == 0


def test_realm_names_and_rotation():
    from wiz101_auto.realm import next_realm, realm_names

    texts = ["Realms", "Ambrose", "Cooper", "Go To Realm", "Ambrose", "Close", "12:04", "Dworgyn"]
    names = realm_names(texts)
    assert names == ["Ambrose", "Cooper", "Dworgyn"]
    assert next_realm(names, ["Ambrose"]) == "Cooper"
    assert next_realm(names, names) == "Ambrose"  # all tried: start over
    assert next_realm([], []) is None


def test_a_dungeons_rooms_share_its_area():
    from wiz101_auto.quest import in_same_area

    first = "Marleybone/MB_BigBen/MB_CounterweightEast"
    assert in_same_area("Marleybone/MB_BigBen/MB_CounterweightWest", first)
    assert not in_same_area("Marleybone/MB_Station/MB_HydePark", first)


def test_door_memory_routes_through_known_doors(tmp_path):
    from wiz101_auto.entitymap import DoorMemory

    mem = DoorMemory(tmp_path / "doors.json")
    main = "Aquila/AQ_Z01_MountOlympus"
    hall = "Aquila/Interiors/AQ_Z01_HallOfWatchfulEye"
    forge = "Aquila/Interiors/AQ_Z01_HephaestusForge"
    # seeded: the Sun Chamber door
    assert mem.route(main, "Aquila/Interiors/AQ_Z01_Apollo_Room")[0][1] == (6681, 8306)
    mem.record(hall, (8, 5068), (7, 4479, 200), forge)
    hops = mem.route(main, forge)
    assert [h[3] for h in hops] == [hall, forge]
    assert mem.route(main, "Nowhere") == []
    # an old two-element entry still answers approach()
    mem.doors["Old"] = [[[0, 0], [10, 10, 0]]]
    assert mem.approach("Old", (5, 5, 0)) == (10, 10, 0)
