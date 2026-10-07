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


def test_the_line_goes_on_when_the_next_quest_came_with_the_hand_in(monkeypatch):
    # 'Goblin Up' handed in and 'Hey Verne!' given at once: by the second
    # reading without the pick, 'Hey Verne!' was already in the previous book
    # and the pin was dropped instead of following the line.
    me = _quester(monkeypatch, "Goblin Up")
    pick = QuestEntry(0, "Goblin Up")
    nxt = QuestEntry(1, "Hey Verne!")
    side = QuestEntry(2, "Art History")
    me._apply_pin([pick, side], None, set(), before={"Goblin Up", "Art History"})
    me._apply_pin([nxt, side], None, set(), before={"Goblin Up", "Art History"})  # missed once
    me._apply_pin([nxt, side], None, set(), before={"Hey Verne!", "Art History"})  # twice
    assert me._pin == "Hey Verne!"


def test_the_line_goes_on_at_once_when_its_world_has_a_new_quest(monkeypatch):
    me = _quester(monkeypatch, "Hobble Gobble")
    pick = QuestEntry(0, "Hobble Gobble", world="Wysteria")
    nxt = QuestEntry(1, "Peg-A-Portal", world="Wysteria")
    side = QuestEntry(2, "Art History", world="Zafaria")
    me._apply_pin([pick, side], None, set(), before={"Hobble Gobble", "Art History"})
    got = me._apply_pin([nxt, side], None, set(), before={"Hobble Gobble", "Art History"})
    assert me._pin == "Peg-A-Portal" and got is nxt
