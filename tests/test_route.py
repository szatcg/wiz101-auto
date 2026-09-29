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
