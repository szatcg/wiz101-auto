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
