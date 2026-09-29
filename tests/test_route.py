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
