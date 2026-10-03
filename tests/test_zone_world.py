from wiz101_auto.quest import zone_world


def test_book_world_names_match_zone_prefixes():
    assert zone_world("Dragonspyre") == "DragonSpire"
    assert zone_world("Wizard City") == "WizardCity"
    assert zone_world("DragonSpire") == "DragonSpire"
    assert zone_world("Grizzleheim") == "Grizzleheim"
    assert zone_world(None) is None


def test_investigable_objects_are_selectable_non_people():
    from wiz101_auto.quest import investigable

    sel = ["BasicObjectStateBehavior", "AnimationBehavior", "CollisionBehavior", "WizardSelectBehavior"]
    assert investigable("MB_KT-PreCel_Relic_02", sel)
    assert not investigable("MB_KT-PreCel_Relic_05", ["BasicObjectStateBehavior", "RenderBehavior"])
    assert not investigable("MB-Standin-BurglarB", ["NPCBehavior", "WizardSelectBehavior"])


def test_jotuns_brothers_come_first():
    from wiz101_auto.quest import pending_pre_bosses

    assert [b for b, _z, _s in pending_pre_bosses("Jotun", set())] == ["Ullik", "Grettir"]
    assert [b for b, _z, _s in pending_pre_bosses("Jotun", {"Ullik"})] == ["Grettir"]
    assert pending_pre_bosses("Jotun", {"Ullik", "Grettir"}) == []
    assert pending_pre_bosses("Malistaire Drake", set()) == []


def test_the_boss_deck_is_used_when_asked_and_present():
    from wiz101_auto.deck_keeper import target_deck

    general = {"deck": {"Orthrus": 3, "Pixie": 1}, "boss_deck": {"Orthrus": 3, "Pixie": 4, "Feint": 3}}
    known = {"Orthrus", "Pixie", "Feint"}
    assert target_deck(general, known) == {"Orthrus": 3, "Pixie": 1}
    assert target_deck(general, known, boss=True) == {"Orthrus": 3, "Pixie": 4, "Feint": 3}
    assert target_deck({"deck": {"Pixie": 1}}, known, boss=True) == {"Pixie": 1}
