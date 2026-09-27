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


def test_combat_objectives():
    from wiz101_auto.quest import is_combat_objective

    assert is_combat_objective("Summon Myth Minion in Unicorn Way")
    assert is_combat_objective("Defeat Scarlet Screamer and Collect Primary Coil in Triton Avenue (0 of 3)")
    assert not is_combat_objective("Talk To Cyrus Drake in Ravenwood")
    assert not is_combat_objective("Collect Cog in Triton Avenue (1 of 3)")


def test_keeps_the_tracked_questline_unless_a_spell_quest_is_waiting():
    from wiz101_auto.quest import choose_quest

    tracked = QuestEntry(0, "Clear as Crystal", mainline=True, active=True, hops=3)
    near_side = QuestEntry(1, "Looking Sharp!", hops=0, reward=99)
    assert choose_quest([tracked, near_side]) is tracked
    spell = QuestEntry(2, "Not So Welcome to Myth", activity=True, hops=2)
    assert choose_quest([tracked, near_side, spell]) is spell


def test_nearer_quest_wins_when_nothing_is_tracked():
    from wiz101_auto.quest import choose_quest

    far = QuestEntry(0, "Far", mainline=True, hops=4, reward=50)
    near = QuestEntry(1, "Near", mainline=True, hops=1, reward=10)
    assert choose_quest([far, near]) is near


def test_fight_needed():
    from wiz101_auto.quest import fight_needed

    street = "WizardCity/WC_Streets/WC_Triton"
    screamer = "Defeat Scarlet Screamer and Collect Primary Coil in Triton Avenue (1 of 3)"
    assert fight_needed(screamer, ["Scarlet Screamer", "Living Puppet"], street, False)
    assert not fight_needed(screamer, ["Rotting Fodder"], street, False)
    assert not fight_needed("Collect Cog in Triton Avenue (0 of 3)", ["Rotting Fodder"], street, False)
    assert fight_needed("Defeat Haunted Minions in Triton Avenue (0 of 4)", ["Haunted Minion"], street, False)
    assert fight_needed("Summon Myth Minion in Unicorn Way", ["Lost Soul"], street, False)
    assert fight_needed("Talk To Harold", ["Rotting Fodder"], street, True)  # bosses always count
    assert fight_needed("Talk To Harold", ["Rotting Fodder"], "WizardCity/Interiors/WC_Cyclops_T2", False)
