from wiz101_auto.givers import is_named_npc

NPC = ["BasicEquipmentBehavior", "NPCBehavior", "AnimationBehavior"]


def test_named_npcs_are_asked():
    assert is_named_npc("MB-AIRHub-NPC03", "Tracy", NPC)
    assert is_named_npc("WC_Ambrose", "Headmaster Ambrose", NPC)


def test_ambient_townsfolk_and_objects_are_not():
    assert not is_named_npc("MB-AmbLady-I", "Lady", NPC)
    assert not is_named_npc("MB-AmbWalker10", "Gent", NPC)
    assert not is_named_npc("Cattail", "Cattail", ["RenderBehavior", "CollisionBehavior"])
    assert not is_named_npc("MB-Dumby-TicketNPC", "", NPC)
    assert not is_named_npc("DynaTrigger_MB_StrayCat", "Stray Cat", NPC)
