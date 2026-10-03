from wiz101_auto.quest import zone_world


def test_book_world_names_match_zone_prefixes():
    assert zone_world("Dragonspyre") == "DragonSpire"
    assert zone_world("Wizard City") == "WizardCity"
    assert zone_world("DragonSpire") == "DragonSpire"
    assert zone_world("Grizzleheim") == "Grizzleheim"
    assert zone_world(None) is None
