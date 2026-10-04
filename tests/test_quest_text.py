

def test_use_spots_start_on_the_object_then_widen():
    from wiz101_auto.quest import use_spots

    spots = use_spots()
    assert spots[0] == (0.0, 0.0) and len(spots) == 5 + 24
    assert max(abs(x) for x, _ in spots) == 300.0


def test_same_object_name_one_or_many():
    from wiz101_auto.quest import same_object_name

    assert same_object_name("Grain Sack", "Grain Sacks")
    assert same_object_name("Grain Sacks", "Grain Sack")
    assert same_object_name("Counterweight Lever", "Counterweight Lever")
    assert not same_object_name("Clockwork Warrior", "Clockwork")


def test_closest_name_finds_what_the_zone_calls_it():
    from wiz101_auto.quest import closest_name

    zone = ["Henrek Graincutter", "Grain Sack", "Fish Racks", "Vestrilund Gate"]
    assert closest_name("Grain Sacks", zone) == "Grain Sack"
    assert closest_name("Fish Rack", zone) == "Fish Racks"
    assert closest_name("Red Crystal Sample", ["Crystal Sample", "Bat"]) == "Crystal Sample"
    assert closest_name("Golden Axe", zone) is None


def test_closest_name_not_an_npc_sharing_the_first_word():
    from wiz101_auto.quest import closest_name

    assert closest_name("Dulin's Hammer", ["Dulin Helmsplitter", "Icy Wall"]) is None


def test_closest_name_reasons_out_one_word_matches():
    from wiz101_auto.quest import closest_name

    stations = ["Conflict Station", "Configuration Station", "Contrivance Device"]
    # The telling word wins over the one every station shares.
    assert closest_name("Contrivance Station", stations) == "Contrivance Device"
    # Only shared words: the stations used already are ruled out.
    two = ["Conflict Station", "Configuration Station", "Security Plinth"]
    assert closest_name("Contrivance Station", two, used=["Conflict Station"]) == "Configuration Station"
    assert closest_name("Contrivance Station", two, used=two[:2]) is None
    # A word is still a match when nothing better exists.
    assert closest_name("Golden Seals", ["Seal of Ymir", "Ore"]) == "Seal of Ymir"
    assert closest_name("Golden Axe", ["Ore", "Icy Wall"]) is None
