import json

from wiz101_auto.deck_keeper import PRISM_COPIES, boss_prism, deck_changes, school_on_file, target_deck

KNOWN = {"Orthrus", "Myth Prism", "Mythblade"}


def test_myth_boss_gets_myth_prisms():
    assert boss_prism("myth", "Myth", KNOWN) == {"Myth Prism": PRISM_COPIES}


def test_other_schools_and_unknown_prism_get_none():
    assert boss_prism("ice", "Myth", KNOWN) == {}
    assert boss_prism("", "Myth", KNOWN) == {}
    assert boss_prism("myth", "Myth", {"Orthrus"}) == {}


def test_prisms_go_on_top_of_the_boss_deck_and_come_out_after():
    general = {"deck": {"Orthrus": 3}, "boss_deck": {"Orthrus": 3, "Mythblade": 3}}
    with_prisms = target_deck(general, KNOWN, boss=True, extra={"Myth Prism": 2})
    assert with_prisms == {"Orthrus": 3, "Mythblade": 3, "Myth Prism": 2}
    after = target_deck(general, KNOWN, boss=True)
    assert deck_changes(with_prisms, after) == {"Myth Prism": (2, 0)}


def test_school_on_file(tmp_path):
    f = tmp_path / "enemy_stats.json"
    f.write_text(json.dumps({"enemies": {"Gnar": {"school": "ice"}, "Bob": {}}}))
    assert school_on_file("gnar", f) == "ice"
    assert school_on_file("Bob", f) == ""
    assert school_on_file("Nobody", f) == ""
