from wiz101_auto.travel_data import find_gate, named_zone, parse_display_zones, parse_gates, parse_spots

GATES_TEXT = """WORLD - WizardCity
standard;-87.87;1760.75;-27.99;WizardCity/WC_Hub;WizardCity/WC_Ravenwood
standard;-6302.26;2653.47;229.00;WizardCity/WC_Hub;WizardCity/WC_Golem_Tower
WORLD - Krokotopia
standard;1.0;2.0;3.0;Krokotopia/KT_Hub;Krokotopia/KT_Marketplace
"""

DISPLAY_TEXT = """WizardCity;WizardCity/WC_Ravenwood;ravenwood
WizardCity;WizardCity/WC_Golem_Tower;golem court
Krokotopia;Krokotopia/KT_Marketplace;marketplace
"""

SPOTS_TEXT = """WORLD - WizardCity
zekeObject;-373.31;4841.82;30.10;WizardCity/WC_Golem_Tower
eloiseObject;1.0;2.0;3.0;WizardCity/WC_Golem_Tower
zekeObject;-5104.18;-2753.32;257.18;WizardCity/WC_Hub
END
"""


def as_tuples(gate_list):
    return [(tuple(pos), zone) for pos, zone in gate_list]


def test_parse_gates_groups_by_from_zone_and_skips_world_headers():
    gates = parse_gates(GATES_TEXT)
    assert set(gates) == {"WizardCity/WC_Hub", "Krokotopia/KT_Hub"}
    assert as_tuples(gates["WizardCity/WC_Hub"]) == [
        ((-87.87, 1760.75, -27.99), "WizardCity/WC_Ravenwood"),
        ((-6302.26, 2653.47, 229.00), "WizardCity/WC_Golem_Tower"),
    ]


def test_parse_display_zones_sorts_longest_name_first():
    zones = parse_display_zones(DISPLAY_TEXT)
    assert zones[0] == ("golem court", "WizardCity/WC_Golem_Tower")
    assert all(len(zones[i][0]) >= len(zones[i + 1][0]) for i in range(len(zones) - 1))


def test_parse_spots_groups_by_zone():
    spots = parse_spots(SPOTS_TEXT)
    assert set(spots) == {"WizardCity/WC_Golem_Tower", "WizardCity/WC_Hub"}
    golem = [tuple(p) for p in spots["WizardCity/WC_Golem_Tower"]]
    assert golem == [(-373.31, 4841.82, 30.10), (1.0, 2.0, 3.0)]


def test_find_gate_matches_destination_named_in_objective():
    gates = parse_gates(GATES_TEXT)
    zones = parse_display_zones(DISPLAY_TEXT)
    pos, dest_zone = find_gate("Go To Golem Court Smith in Golem Court", "WizardCity/WC_Hub", gates, zones)
    assert (tuple(pos), dest_zone) == ((-6302.26, 2653.47, 229.00), "WizardCity/WC_Golem_Tower")


def test_find_gate_returns_first_hop_of_a_multi_zone_path():
    gates = parse_gates(
        "standard;1;1;0;WizardCity/WC_Golem_Tower;WizardCity/WC_Hub\n"
        "standard;2;2;0;WizardCity/WC_Hub;WizardCity/WC_Golem_Tower\n"
        "standard;3;3;0;WizardCity/WC_Hub;WizardCity/WC_Streets/WC_Unicorn\n"
    )
    zones = [("unicorn way", "WizardCity/WC_Streets/WC_Unicorn")]
    objective = "Go To Unicorn Way Smith in Unicorn Way"
    pos, next_zone = find_gate(objective, "WizardCity/WC_Golem_Tower", gates, zones)
    assert (tuple(pos), next_zone) == ((1.0, 1.0, 0.0), "WizardCity/WC_Hub")


def test_named_zone_picks_the_longest_matching_place():
    zones = parse_display_zones(DISPLAY_TEXT)
    assert named_zone("Go To Golem Court Smith in Golem Court", zones) == "WizardCity/WC_Golem_Tower"
    assert named_zone("Defeat 3 Rattlebones", zones) is None


def test_find_gate_returns_none_without_a_matching_destination_or_zone():
    gates = parse_gates(GATES_TEXT)
    zones = parse_display_zones(DISPLAY_TEXT)
    assert find_gate("Defeat 3 Storm Snakes", "WizardCity/WC_Hub", gates, zones) is None
    assert find_gate("Go To Golem Court Smith", "WizardCity/WC_Unknown", gates, zones) is None
    assert find_gate("Go To Golem Court Smith", "WizardCity/WC_Golem_Tower", gates, zones) is None


def test_find_gate_routes_around_blocked_gates():
    gates = parse_gates(
        "standard;1;1;0;Cyclops;Colossus\n"
        "standard;2;2;0;Cyclops;OldeTown\n"
        "standard;3;3;0;OldeTown;Shop\n"
        "standard;4;4;0;Shop;Colossus\n"
    )
    zones = [("colossus boulevard", "Colossus")]
    objective = "Go To Colossus Boulevard Smith in Colossus Boulevard"
    assert find_gate(objective, "Cyclops", gates, zones)[1] == "Colossus"
    assert find_gate(objective, "Cyclops", gates, zones, {("Cyclops", "Colossus")})[1] == "OldeTown"


def test_gate_behind_is_opposite_the_facing_direction():
    from wizwalker import XYZ
    from wizwalker.utils import calculate_perfect_yaw

    from wiz101_auto.travel_data import gate_behind

    me = XYZ(1000.0, 1000.0, 0.0)
    ahead = XYZ(1000.0, 2000.0, 0.0)  # facing +y
    g = gate_behind(me, calculate_perfect_yaw(me, ahead), 250)
    assert abs(g.x - 1000) < 20 and abs(g.y - 750) < 20


def test_add_gate_keeps_known_gates():
    from wizwalker import XYZ

    from wiz101_auto.travel_data import add_gate, first_hop_toward

    gates = {"A": [(XYZ(0, 0, 0), "B")]}
    assert not add_gate(gates, "A", "B", XYZ(5, 5, 0))
    assert add_gate(gates, "C", "A", XYZ(1, 2, 0))
    pos, via = first_hop_toward("C", "B", gates)
    assert via == "A" and (pos.x, pos.y) == (1, 2)


def test_floor_points_drop_cameras_in_the_air():
    from wiz101_auto.collect import floor_points

    pts = [(0, 0, 48), (100, 0, 330), (200, 0, 1133), (300, 0, -20)]
    assert floor_points(pts, 48) == [(0, 0, 48), (100, 0, 330), (300, 0, -20)]


def test_collectables():
    from wiz101_auto.collect import is_collectable

    for name in ("Parchment", "Wood", "Cattail", "FrostedFlax_01", "WC-Chest-Common-001", "WC-Chest-Rare-001",
                 "COLLECT_HOUSE_WC_decor_painting_03"):
        assert is_collectable(name), name
    for name in ("WC_WispHealth", "Basic Positional", "WC_Myth_Desk", "Wooden Door", "Ghoul-Purple-L02",
                 "WC_Gardening_Dirt_Normal", "Stonehenge_Arch"):
        assert not is_collectable(name), name


def test_zones_around_skips_interiors():
    from wiz101_auto.travel_data import XYZ, zones_around

    p = XYZ(0, 0, 0)
    gates = {
        "K/Hall": [(p, "K/Entrance")],
        "K/Entrance": [(p, "K/Hall"), (p, "K/Streets"), (p, "K/Interiors/Shop")],
        "K/Streets": [(p, "K/Entrance"), (p, "K/Far")],
    }
    assert zones_around("K/Hall", gates, 2) == ["K/Hall", "K/Entrance", "K/Streets"]
    assert zones_around("K/Hall", gates, 3)[-1] == "K/Far"
