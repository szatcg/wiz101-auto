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


def test_people_to_ask_instead_of_grinding():
    from wiz101_auto.givers import QuestGivers, looks_like_person

    assert looks_like_person("Thornton Lewis") and looks_like_person("The Archivist")
    assert not looks_like_person("CL-Chest-Common-001") and not looks_like_person("Ore")
    assert not looks_like_person("DS_WispHealth")

    class Q:
        client = None

    g = QuestGivers.__new__(QuestGivers)
    g._talked, g._zone_checks = {}, {}
    zones = {"Celestia/CL_A": {"Thornton Lewis": [], "Ore": []},
             "Celestia/CL_B": {"Edith Benchley": [], "Dalton Prescott": [], "Piscean Trooper": []},
             "Grizzleheim/GH_Hero": {"Bjorn Ironclaws": [], "Hrafn Lorespeaker": [], "X Y": []}}
    assert g.hunt_target("Celestia", zones, {"Piscean Trooper"}) == ("Edith Benchley", "Celestia/CL_B")
    g._zone_checks["Celestia/CL_B"] = __import__("time").time()
    assert g.hunt_target("Celestia", zones, set()) == ("Thornton Lewis", "Celestia/CL_A")


def test_talk_targets_come_from_the_activity_log(tmp_path):
    from wiz101_auto.givers import talk_targets

    log = tmp_path / "activity.log"
    log.write_text("20:34:14 | SUCCESS | objective done -> now: 'Talk To Yaxche in Cloudburst Forest'\n"
                   "20:52:08 | SUCCESS | objective done -> now: 'Talk To Tezcat Threestar in The Zocalo'\n")
    assert talk_targets(log) == {"Yaxche", "Tezcat Threestar"}
