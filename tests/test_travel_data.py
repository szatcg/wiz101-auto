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
