import json

from wiz101_auto.quest import is_known_boss


def test_bosses_heal_to_full_first(tmp_path):
    stats = tmp_path / "enemy_stats.json"
    enemies = {"Runed Annihilator": {"boss": True}, "Wing-Fish": {"boss": False}}
    stats.write_text(json.dumps({"enemies": enemies}))
    assert is_known_boss("Runed Annihilator", stats)
    assert not is_known_boss("Wing-Fish", stats)
    assert is_known_boss("Jotun", stats) and is_known_boss("Ullik", stats)  # (PRE_BOSSES)


def test_each_deck_has_its_own_deck_item():
    from wiz101_auto.deck_keeper import item_role

    assert item_role(boss=True) == "single" and item_role(boss=False) == "aoe"
