from wiz101_auto.quest_steps import base, record, view


def test_steps_are_recorded_in_order_and_counts_update_in_place(tmp_path):
    f = tmp_path / "steps.json"
    record("Burn It", "Burn Scout Tower in Tatakai Outpost (0 of 3)", f)
    record("Burn It", "Burn Scout Tower in Tatakai Outpost (2 of 3)", f)
    record("Burn It", "Talk To Sanisai Fukido in Tatakai Outpost", f)
    v = view("Burn It", "Talk To Sanisai Fukido in Tatakai Outpost", path=f)
    assert v["done"] == ["Burn Scout Tower in Tatakai Outpost (2 of 3)"]
    assert v["now"] == "Talk To Sanisai Fukido in Tatakai Outpost"


def test_base_drops_the_count():
    assert base("Defeat Cursed Ronins (3 of 10)") == "Defeat Cursed Ronins"
