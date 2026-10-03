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
