from wiz101_auto import detour

WORLDS = [
    {"world": "Grizzleheim", "finish": "Blood Brother"},
    {"world": "Wintertusk", "finish": "Winter News",
     "start": {"quest": "Cold News", "npc": "Merle Ambrose",
               "zone": "WizardCity/Interiors/WC_Headmistress_House"}},
]


def test_the_first_unfinished_world_is_the_detour():
    assert detour.active(WORLDS, set())["world"] == "Grizzleheim"
    assert detour.active(WORLDS, {"Blood Brother"})["world"] == "Wintertusk"
    assert detour.active(WORLDS, {"Blood Brother", "Winter News"}) is None


def test_the_start_npc_is_visited_only_before_the_world_begins():
    w = WORLDS[1]
    assert detour.needs_start(w, False, {"Blood Brother"}) == {
        "npc": "Merle Ambrose", "zone": "WizardCity/Interiors/WC_Headmistress_House"}
    assert detour.needs_start(w, True, set()) is None  # its quest is in the book
    assert detour.needs_start(w, False, {"Cold News"}) is None  # started already
    assert detour.needs_start(WORLDS[0], False, set()) is None  # no start NPC listed
