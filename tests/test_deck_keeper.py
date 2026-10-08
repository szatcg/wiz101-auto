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


def test_a_bosss_extra_cards_come_out_down_to_the_players_own_count(tmp_path, monkeypatch):
    # (Belloq's 3 Humongofrogs came out with the deck's own one: the boss
    # deck was left with no attack card.)
    import json

    from wiz101_auto import deck, deck_keeper

    general = tmp_path / "general.json"
    general.write_text(json.dumps({"deck": {"Orthrus": 1}, "player_decks": True}), encoding="utf-8")
    progress = tmp_path / "progress.json"
    progress.write_text(json.dumps({"known_spells": ["Humongofrog", "Mythblade"]}), encoding="utf-8")
    monkeypatch.setattr(deck_keeper, "GENERAL_FILE", general)
    monkeypatch.setattr(deck_keeper, "PROGRESS_FILE", progress)
    monkeypatch.setattr(deck_keeper, "ADDED_FILE", tmp_path / "added.json")
    counts = {"Humongofrog": 1, "Mythblade": 2}
    monkeypatch.setattr(deck, "load_deck_counts", lambda *a, **k: dict(counts))
    keeper = deck_keeper.DeckKeeper()
    keeper._worn = "single"
    target, _ = keeper.due(boss=True, extra={"Humongofrog": 3})
    assert target["Humongofrog"] == 3
    counts["Humongofrog"] = 3
    keeper._checked = -1e9
    target, _ = keeper.due(boss=True, extra={})
    assert target["Humongofrog"] == 1 and target["Mythblade"] == 2


def test_a_bosss_school_by_the_objectives_short_name(tmp_path):
    import json

    from wiz101_auto.deck_keeper import school_on_file

    stats = tmp_path / "stats.json"
    stats.write_text(json.dumps({"enemies": {"Blighted Yaxche": {"school": "Myth"}}}), encoding="utf-8")
    assert school_on_file("Yaxche", stats) == "myth"
    assert school_on_file("Blighted Yaxche", stats) == "myth"
    assert school_on_file("Axche", stats) == ""
