from wiz101_auto import quest as q
from wiz101_auto.quest import QuestEntry, Quester


def _quester(monkeypatch, pin):
    monkeypatch.setattr(q, "save_pin", lambda name: None)
    monkeypatch.setattr(q, "load_pin", lambda: pin)
    me = Quester.__new__(Quester)
    me._pin = pin
    me._spell_first_logged = None
    return me


def test_a_new_pick_needs_two_misreads_before_it_counts_as_done(monkeypatch):
    me = _quester(monkeypatch, "Positively Absolutely")
    old = QuestEntry(0, "Positively Absolutely", mainline=True)
    nxt = QuestEntry(1, "Signs and Portents", mainline=True)
    side = QuestEntry(2, "Seek out the Source")
    me._apply_pin([old, side], None, set())
    me._apply_pin([side], None, set())  # missed once ...
    me._apply_pin([nxt, side], None, set(), before={"Positively Absolutely", "Seek out the Source"})
    assert me._pin == "Signs and Portents"  # ... twice: done, on to the next in its line
    got = me._apply_pin([side], None, set())  # one misread of the new pick
    assert me._pin == "Signs and Portents" and got is None
