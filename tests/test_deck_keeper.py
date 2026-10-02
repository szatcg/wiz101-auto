from wiz101_auto.deck_keeper import deck_changes, target_deck

GENERAL = {
    "deck": {"Humongofrog": 3, "ColossusStone_Trainable": 3, "Mythblade": 3, "Pixie": 3},
    "when_learned": {"Orthrus": {"Orthrus": 5, "Humongofrog": 0, "ColossusStone_Trainable": 1}},
}
KNOWN = {"Humongofrog", "ColossusStone_Trainable", "Mythblade", "Pixie", "Ramp_Myth_01A"}


def test_spells_the_game_added_go():
    current = {"Humongofrog": 3, "ColossusStone_Trainable": 3, "Mythblade": 3, "Pixie": 3, "Ramp_Myth_01A": 4,
               "Reshuffle": 1}
    target = target_deck(GENERAL, KNOWN)
    assert target == GENERAL["deck"]  # (Orthrus not learned yet: no change)
    assert deck_changes(current, target) == {"Ramp_Myth_01A": (4, 0)}  # Reshuffle stays


def test_orthrus_replaces_humongofrog_and_most_colossus_once_learned():
    target = target_deck(GENERAL, KNOWN | {"Orthrus"})
    assert target == {"Orthrus": 5, "ColossusStone_Trainable": 1, "Mythblade": 3, "Pixie": 3}
