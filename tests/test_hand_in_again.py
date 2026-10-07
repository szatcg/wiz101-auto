from wiz101_auto import quest
from wiz101_auto.quest import Quester


def test_completed_quest_sends_the_bot_back_to_its_hand_in_npc(tmp_path, monkeypatch):
    # 'Head Held Low' ended on "Talk To Ceara Ashbury in The Wild"; its
    # completion was read minutes later: she's still asked for the next one.
    monkeypatch.setattr(quest, "HAND_INS_FILE", tmp_path / "hand_ins.json")
    monkeypatch.setattr(quest, "RETALK_FILE", tmp_path / "retalk.json")
    q = Quester.__new__(Quester)
    q.quest_order = {"headheldlow": None}  # (a story quest)
    q._active_quest = "Head Held Low"
    q._note_hand_in("Talk To Sir Jean-Paul Jouster in Caer Lyon", "Avalon/AV_Z03_CaerLyon")
    q._note_hand_in("Talk To Ceara Ashbury in The Wild", "Avalon/Interiors/AV_Z03_WILD_C16")
    q._talk_again_after_completion({"Head Held Low"})
    queue = quest.load_retalk()
    assert [npc for npc, _ in queue] == ["Ceara Ashbury", "Sir Jean-Paul Jouster"]  # (latest first)
    assert all(zone.startswith("Avalon/") for _, zone in queue)
    assert quest.load_hand_ins() == {}  # (asked once)


def test_no_visit_for_a_quest_without_a_talk_objective(tmp_path, monkeypatch):
    monkeypatch.setattr(quest, "HAND_INS_FILE", tmp_path / "hand_ins.json")
    monkeypatch.setattr(quest, "RETALK_FILE", tmp_path / "retalk.json")
    q = Quester.__new__(Quester)
    q.quest_order = {}
    q._active_quest = "Salad Days"
    q._note_hand_in("Defeat Night Goblins in Caer Lyon", "Avalon/AV_Z03_CaerLyon")
    q._talk_again_after_completion({"Salad Days"})
    assert quest.load_retalk() == []


def test_no_retalk_for_a_class_or_side_quest(tmp_path, monkeypatch):
    # 'Mark Your Calendar' (a class quest) sent the bot to Cyrus Drake's
    # school and Captain O'Hare in Wysteria while the main quest waited.
    monkeypatch.setattr(quest, "HAND_INS_FILE", tmp_path / "hand_ins.json")
    monkeypatch.setattr(quest, "RETALK_FILE", tmp_path / "retalk.json")
    q = Quester.__new__(Quester)
    q.quest_order = {}
    q._active_quest = "Mark Your Calendar"
    q._note_hand_in("Talk To Cyrus Drake in Ravenwood", "WizardCity/WC_Ravenwood")
    q._talk_again_after_completion({"Mark Your Calendar"})
    assert quest.load_retalk() == []
