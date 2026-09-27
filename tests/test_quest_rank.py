from wiz101_auto.quest import QuestEntry, quest_rank


def test_spell_quests_beat_main_story_which_beats_side_quests():
    quests = [
        QuestEntry(0, "Clear as Crystal", mainline=True, reward=14),
        QuestEntry(1, "100% Not That Witch", mainline=True, reward=23),
        QuestEntry(2, "Looking Sharp!", reward=13),
        QuestEntry(3, "Not So Welcome to Myth", activity=True, reward=30),
    ]
    ranked = sorted(quests, key=quest_rank, reverse=True)
    assert [q.name for q in ranked] == [
        "Not So Welcome to Myth",
        "100% Not That Witch",
        "Clear as Crystal",
        "Looking Sharp!",
    ]
