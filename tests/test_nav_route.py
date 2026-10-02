from wiz101_auto.route import DORM, RAVENWOOD, WORLD_TREE, gate_path, plan_route, zone_label

GATES = {
    "W/Hub": [(None, "W/A"), (None, "W/B")],
    "W/A": [(None, "W/Hub"), (None, "W/C")],
    "W/C": [(None, "W/A"), (None, "W/D")],
    "W/D": [(None, "W/C")],
    "W/B": [(None, "W/Hub")],
    RAVENWOOD: [(None, "WizardCity/Interiors/WC_SchoolMyth"), (None, WORLD_TREE)],
}


def test_gate_path():
    assert gate_path("W/Hub", "W/D", GATES) == ["W/Hub", "W/A", "W/C", "W/D"]
    assert gate_path("W/D", "W/Nowhere", GATES) is None


def test_basilica_to_cyrus_drake():
    route = plan_route("DragonSpire/DS_Hub_Cathedral", RAVENWOOD, GATES,
                       final="WizardCity/Interiors/WC_SchoolMyth")
    labels = [zone_label(z, [("the basilica", "DragonSpire/DS_Hub_Cathedral")]) for z in route]
    assert labels == ["The Basilica", "Dorm", "Ravenwood", "Myth School"]


def test_hub_button_when_the_hub_is_nearer():
    # from B, D is B-Hub-A-C-D on foot; the hub button saves the walk to the hub
    assert plan_route("W/B", "W/D", GATES, hub="W/Hub") == ["W/B", "W/Hub", "W/A", "W/C", "W/D"]
    assert plan_route("W/C", "W/D", GATES, hub="W/Hub") == ["W/C", "W/D"]


def test_to_another_world_by_the_world_tree():
    assert plan_route(RAVENWOOD, "W/D", GATES, arrival="W/Hub") == [
        RAVENWOOD, WORLD_TREE, "W/Hub", "W/A", "W/C", "W/D"]
    assert plan_route("W/D", "W/D", GATES) == []  # already there: no graph


def test_labels_tidy_zone_ids():
    assert zone_label("DragonSpire/DS_A1_Knowledge/DS_A1Hub_Library", []) == "Library"
    assert zone_label(DORM, []) == "Dorm"
