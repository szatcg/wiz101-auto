import json

import wiz101_auto.deck_adapt as da


class _Done:
    def poll(self):
        return 0


def _adapter(tmp_path, monkeypatch):
    monkeypatch.setattr(da, "MODE_FILE", tmp_path / "mode.json")
    monkeypatch.setattr(da, "ADVICE_FILE", tmp_path / "advice.json")
    monkeypatch.setattr(da, "GENERAL_FILE", tmp_path / "general.json")
    return da.DeckAdapter()


def test_a_finished_search_for_the_group_we_lost_to_is_put_in(tmp_path, monkeypatch):
    a = _adapter(tmp_path, monkeypatch)
    a._search, a._search_vs = _Done(), ["Agony Wraith", "Meowiarty"]
    (tmp_path / "advice.json").write_text(json.dumps(
        {"vs": "Agony Wraith,Meowiarty", "deck": {"Minotaur": 2}, "win": 0.4, "rounds": 12.0}))
    deck, why = a.wanted()
    assert deck == {"Minotaur": 2} and why.startswith("boss deck")
    assert a.wanted() is None  # taken once


def test_winning_brings_the_general_deck_back(tmp_path, monkeypatch):
    a = _adapter(tmp_path, monkeypatch)
    (tmp_path / "general.json").write_text(json.dumps({"deck": {"Humongofrog": 3}}))
    a.mode = {"deck": "boss", "vs": ["Meowiarty"]}
    a.on_win(["Clockwork Wizard"])  # not the group the boss deck is for
    assert a.wanted() is None
    a.on_win(["Meowiarty", "Agony Wraith"])
    assert a.wanted() == ({"Humongofrog": 3}, "general deck")


def test_a_remembered_boss_deck_goes_in_before_its_fight(tmp_path, monkeypatch):
    import json

    from wiz101_auto import deck_adapt

    monkeypatch.setattr(deck_adapt, "BOSS_DECKS", tmp_path / "boss.json")
    monkeypatch.setattr(deck_adapt, "MODE_FILE", tmp_path / "mode.json")
    monkeypatch.setattr(deck_adapt, "TACTICS_FILE", tmp_path / "tactics.json")
    (tmp_path / "boss.json").write_text(json.dumps(
        {"Haru,Ronin Blademaster": {"deck": {"Myth Prism": 3, "Pixie": 2}, "win": 0.9}}), encoding="utf-8")
    a = deck_adapt.DeckAdapter()
    a._bosses = {"Haru"}
    a.prepare_for("Ronin Blademaster")  # not a boss: nothing
    assert a.wanted() is None
    a.prepare_for("Haru")  # not in view yet: no call
    assert a.wanted() is None
    a.prepare_for("Haru", alone=True)  # alone: its single-target deck
    deck, why = a.wanted()
    assert deck == {"Myth Prism": 3, "Pixie": 2} and "remembered" in why
    a.mode = {"deck": "boss", "vs": ["Haru", "Ronin Blademaster"]}
    monkeypatch.setattr(deck_adapt, "GENERAL_FILE", tmp_path / "general.json")
    (tmp_path / "general.json").write_text(json.dumps({"deck": {"Humongofrog": 3}}), encoding="utf-8")
    a.prepare_for("Haru", alone=False)  # company: the AoE deck
    deck, why = a.wanted()
    assert deck == {"Humongofrog": 3}


def test_searching_for_only_while_the_search_runs():
    import time

    from wiz101_auto.deck_adapt import DeckAdapter

    class Running:
        def poll(self):
            return None

    a = DeckAdapter.__new__(DeckAdapter)
    a._search, a._search_vs, a._search_started = Running(), ["Sea Lord"], time.time()
    assert a.searching_for("Sea Lord")
    assert not a.searching_for("Cyrus Drake")
    a._search_started = time.time() - 3600
    assert not a.searching_for("Sea Lord")
    a._search = None
    assert not a.searching_for("Sea Lord")


def test_single_target_when_alone_or_after_three_aoe_losses():
    from wiz101_auto.deck_adapt import wants_single

    assert wants_single(True, 0) is True
    assert wants_single(False, 0) is False
    assert wants_single(False, 2) is False
    assert wants_single(False, 3) is True
    assert wants_single(None, 0) is None
    assert wants_single(None, 3) is True
