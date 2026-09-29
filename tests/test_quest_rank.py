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


def test_main_quests_only_side_quests_when_the_main_one_is_set_aside():
    from wiz101_auto.quest import choose_quest

    main = QuestEntry(0, "Throwing Nightshade", mainline=True, active=True, world="Haunted Cave",
                      goal="Defeat Lord Nightshade in Haunted Cave", hops=0)
    side_fight = QuestEntry(1, "Dreadful Assignment", world="Unicorn Way",
                            goal="Defeat Lost Souls in Unicorn Way", hops=4)
    side_talk = QuestEntry(2, "Mail Call", world="Unicorn Way", goal="Talk To Private Stillson", hops=4)
    assert choose_quest([main, side_fight, side_talk]) is main  # side quests are ignored
    # Stuck on the main quest: side quests (earliest area, easy first) until a level-up.
    assert choose_quest([main, side_fight, side_talk], {"Throwing Nightshade"}) is side_talk
    spell = QuestEntry(3, "Not So Welcome to Myth", activity=True, world="Triton Avenue", hops=2)
    assert choose_quest([main, side_talk, spell]) is spell  # spell quests teach spells


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


def test_fight_needed_for_any_named_enemy():
    from wiz101_auto.quest import fight_needed

    obj = "Defeat Any Nirini and Collect Key in Royal Hall"
    street = "Krokotopia/KT_Pyramid/KT_Hall"
    assert fight_needed(obj, ["Nirini Warrior"], street, False)
    warriors = "Defeat Nirini Warrior in Palace of Fire (2 of 3)"
    assert not fight_needed(warriors, ["Desert Golem"], street, False)


def test_side_quests_are_finished_one_at_a_time():
    from wiz101_auto.quest import choose_quest

    main = QuestEntry(0, "Give 'em Another Round", mainline=True, goal="Defeat Akori Nirini")
    tracked = QuestEntry(1, "So Many Nirini", active=True, world="The Oasis", goal="Talk To Zan'ne", hops=3)
    other = QuestEntry(2, "Tool Time", world="Chamber of Fire", goal="Talk To Danforth", hops=1)
    assert choose_quest([main, tracked, other], {"Give 'em Another Round"}) is tracked


def test_side_quests_stay_in_this_world_and_prefer_bigger_rewards():
    from wiz101_auto.quest import choose_quest

    main = QuestEntry(0, "Give 'em Another Round", mainline=True, goal="Defeat Akori Nirini")
    far = QuestEntry(1, "A Foul Decree", active=True, world="Colossus Boulevard", reward=500, hops=None)
    small = QuestEntry(2, "Overdue Scrolls", world="The Oasis", reward=90, hops=1)
    big = QuestEntry(3, "So Many Nirini", world="Palace of Fire", reward=250, hops=3)
    pick = choose_quest([main, far, small, big], {"Give 'em Another Round"}, world="Krokotopia")
    assert pick is big  # not the tracked Wizard City one; the bigger Krokotopia reward


def test_a_dungeons_own_quest_comes_before_the_main_quest():
    from wiz101_auto.quest import QuestEntry, dungeon_quest

    zone = "Krokotopia/KT_Pyramid/KT_ThroneRoom"
    zones = {"Throne Room of Fire": zone, "Palace of Fire": "Krokotopia/KT_Pyramid/KT_PalaceOfFire"}
    quests = [
        QuestEntry(0, "Into the Map Room", mainline=True, active=True, world="Throne Room of Fire"),
        QuestEntry(1, "Collecting Gems", world="Palace of Fire"),
        QuestEntry(2, "Serpent Staff", world="Throne Room of Fire"),
    ]
    assert dungeon_quest(quests, zone, zones.get).name == "Serpent Staff"
    assert dungeon_quest(quests, zone, zones.get, {"Serpent Staff"}) is None
    assert dungeon_quest(quests, "Krokotopia/KT_Hub", zones.get) is None


def test_any_matches_the_creature_kind():
    from wiz101_auto.quest import defeat_names, fight_needed

    obj = "Defeat Any Sphinx Sokkwi in Hall of Champions (0 of 4)"
    assert defeat_names(obj) == ["Sphinx Sokkwi", "Sokkwi"]
    assert fight_needed(obj, ["Sokkwi Crusher"], "Krokotopia/KT_Krokosphinx/KT_ChampHall", False)
    assert not fight_needed(obj, ["Glacial Avenger"], "Krokotopia/KT_Krokosphinx/KT_ChampHall", False)
    assert defeat_names("Defeat Gobbler Gorger in Colossus Boulevard") == ["Gobbler Gorger"]


def test_is_hub():
    from wiz101_auto.quest import is_hub

    assert is_hub("Krokotopia/KT_Hub") and is_hub("WizardCity/WC_Hub")
    assert not is_hub("Krokotopia/KT_Hub_Sphinx") and not is_hub("Krokotopia/KT_Tomb/KT_DjeseritTomb")


def test_errands_are_no_fight_steps():
    from wiz101_auto.quest import is_errand

    assert is_errand("Talk To Sergeant Major Talbot in The Oasis")
    assert is_errand("Go To Wolfminster Abbey")
    assert not is_errand("Defeat Krokopatra in Temple of Storms")
    assert not is_errand("Collect Flame Gems")
    assert not is_errand("")


def test_nearby_side_errand_comes_before_the_main_quest():
    from wiz101_auto.quest import QuestEntry, errand_detour

    main = QuestEntry(0, "Triumphant Return!", mainline=True, active=True, goal="Defeat Rat Thief", hops=2)
    turn_in = QuestEntry(1, "Lost Hat", goal="Talk To Sergeant Major Talbot", hops=1)
    far = QuestEntry(2, "Far Away", goal="Talk To Someone", hops=6)
    fight = QuestEntry(3, "Pests", goal="Defeat Ratbeasts", hops=0)
    unknown = QuestEntry(4, "Somewhere", goal="Talk To Nobody")
    mb = "Marleybone"
    turn_in.world = "Regent's Square"
    assert errand_detour([main, turn_in, far, fight, unknown], main, world=mb) is turn_in
    assert errand_detour([main, far, fight, unknown], main, world=mb) is None
    assert errand_detour([main, turn_in], main, {"Lost Hat"}, world=mb) is None
    # a side quest back in an earlier world isn't worth the trip
    old = QuestEntry(5, "Thirst Day", world="The Oasis", goal="Talk To Someone", hops=1)
    assert errand_detour([main, old], main, world=mb) is None


def test_book_steps_without_verbs():
    from wiz101_auto.quest import QuestEntry, errand_detour, quest_is_errand

    talk = QuestEntry(1, "Back Up for Bones", zone="Marleybone", world="Hyde Park", target="Ms. Conrail",
                      hops=1)
    fight = QuestEntry(2, "Rats", zone="Marleybone", target="Rat Thief", fight=True, hops=0)
    collect = QuestEntry(3, "Bones", zone="Marleybone", target="Bone", counted=True, hops=0)
    done = QuestEntry(4, "Advanced Combat", zone="Wizard City", goal="Complete", hops=1)
    assert quest_is_errand(talk) and quest_is_errand(done)
    assert not quest_is_errand(fight) and not quest_is_errand(collect)
    main = QuestEntry(0, "Not So Fast...", mainline=True, active=True, zone="Marleybone", fight=True)
    assert errand_detour([main, talk, fight, collect, done], main, world="Marleybone") is talk
