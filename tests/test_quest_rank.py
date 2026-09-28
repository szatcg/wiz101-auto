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


def test_clears_the_earliest_area_first_easy_quests_first():
    from wiz101_auto.quest import choose_quest

    tracked = QuestEntry(0, "Throwing Nightshade", mainline=True, active=True, world="Haunted Cave",
                         goal="Defeat Lord Nightshade in Haunted Cave", hops=0)
    unicorn_fight = QuestEntry(1, "Dreadful Assignment", world="Unicorn Way",
                               goal="Defeat Lost Souls in Unicorn Way", hops=4)
    unicorn_talk = QuestEntry(2, "Mail Call", world="Unicorn Way", goal="Talk To Private Stillson", hops=4)
    assert choose_quest([tracked, unicorn_fight]) is unicorn_fight  # earlier area wins
    assert choose_quest([tracked, unicorn_fight, unicorn_talk]) is unicorn_talk  # no fight first
    spell = QuestEntry(3, "Not So Welcome to Myth", activity=True, world="Triton Avenue", hops=2)
    assert choose_quest([tracked, unicorn_talk, spell]) is spell


def test_hub_quests_count_as_the_current_area():
    from wiz101_auto.quest import choose_quest

    triton = QuestEntry(0, "Cog Collection", world="Triton Avenue", goal="Collect Cog", hops=2)
    commons = QuestEntry(1, "Talk Shop", world="The Commons", goal="Talk To Dworgyn", hops=1)
    assert choose_quest([triton, commons]) is commons  # same tier, nearer


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


def test_defeat_target():
    from wiz101_auto.quest import defeat_target

    assert defeat_target("Defeat Gobbler Gorger in Colossus Boulevard (0 of 2)") == "Gobbler Gorger"
    assert defeat_target("Defeat Lost Soul in Unicorn Way") == "Lost Soul"
    two_part = "Defeat Scarlet Screamer and Collect Primary Coil in Triton Avenue (0 of 3)"
    assert defeat_target(two_part) == "Scarlet Screamer"
    assert defeat_target("Defeat Any Nirini and Collect Key in Royal Hall") == "Nirini"
    assert defeat_target("Talk To Merle Ambrose in Commons") is None


def test_safe_landing_avoids_enemies_and_prefers_our_side():
    from wizwalker import XYZ

    from wiz101_auto.quest import clear_of, safe_landing

    target, start = XYZ(0, 0, 0), XYZ(-5000, 0, 0)
    mobs = [XYZ(100, 0, 0)]
    assert not clear_of(target, mobs, 700)
    spot = safe_landing(target, start, mobs, 700)
    assert clear_of(spot, mobs, 700) and spot.x < 0  # on our side of the target
    assert safe_landing(target, start, [XYZ(0, 0, 0)] + [XYZ(x, y, 0) for x in range(-3000, 3001, 500)
                                                          for y in range(-3000, 3001, 500)], 700) is None
