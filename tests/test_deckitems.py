from wiz101_auto.deckitems import pick_roles


def test_pick_roles_worn_is_aoe():
    items = [("Deck of Heroes", False), ("Deck of Champions", True)]
    assert pick_roles(items) == ("Deck of Champions", "Deck of Heroes")
    assert pick_roles(items, aoe="Deck of Heroes", single="Deck of Champions") == (
        "Deck of Heroes", "Deck of Champions")
    assert pick_roles([("Only Deck", True)]) is None
    assert pick_roles(items, aoe="Nope", single="Deck of Heroes") is None


def test_pick_roles_by_part_of_the_name():
    items = [("Karuvian Deck of Eternity", True), ("Dragonfire Deck", False)]
    assert pick_roles(items, aoe="karuvian deck of eternity", single="dragonfire") == (
        "Karuvian Deck of Eternity", "Dragonfire Deck")
