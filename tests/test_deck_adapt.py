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
