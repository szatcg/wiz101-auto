from wiz101_auto.givers import looks_like_person


def test_avalon_props_are_not_people():
    for prop in ("Hay Stack", "Mill Wheel Piece", "Shrine To Loyalty", "Hanging Bridge Stone",
                 "Book of Heraldry", "Pixie Cage", "Sacks of Grain", "Short Shrub"):
        assert not looks_like_person(prop), prop
    for person in ("Sir Guy Gascoigne", "Friar Nolan", "Monstrologist Burke", "Innes Idle"):
        assert looks_like_person(person), person


def test_hunt_matches_a_place_that_is_a_zone():
    from wiz101_auto.givers import QuestGivers

    h = QuestGivers.__new__(QuestGivers)
    h._zone_checks, h._asked = {}, {}
    h._asked_recently = lambda zone, n: False
    zones = {"Zafaria/ZF_Z00_Hub": {"Mamba Ngozi": [], "Hay Stack": []}}
    assert h.hunt_target("Zafaria/ZF_Z00_Hub", zones, set()) == ("Mamba Ngozi", "Zafaria/ZF_Z00_Hub")
    assert h.hunt_target("Zafaria", zones, set()) == ("Mamba Ngozi", "Zafaria/ZF_Z00_Hub")
