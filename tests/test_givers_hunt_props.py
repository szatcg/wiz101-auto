from wiz101_auto.givers import looks_like_person


def test_avalon_props_are_not_people():
    for prop in ("Hay Stack", "Mill Wheel Piece", "Shrine To Loyalty", "Hanging Bridge Stone",
                 "Book of Heraldry", "Pixie Cage", "Sacks of Grain", "Short Shrub"):
        assert not looks_like_person(prop), prop
    for person in ("Sir Guy Gascoigne", "Friar Nolan", "Monstrologist Burke", "Innes Idle"):
        assert looks_like_person(person), person
