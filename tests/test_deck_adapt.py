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
    (tmp_path / "boss.json").write_text(json.dumps(
        {"Haru,Ronin Blademaster": {"deck": {"Myth Prism": 3}, "win": 0.9}}), encoding="utf-8")
    a = deck_adapt.DeckAdapter()
    a._bosses = {"Haru"}
    a.prepare_for("Ronin Blademaster")  # not a boss: nothing
    assert a.wanted() is None
    a.prepare_for("Haru")
    deck, why = a.wanted()
    assert deck == {"Myth Prism": 3} and "remembered" in why
