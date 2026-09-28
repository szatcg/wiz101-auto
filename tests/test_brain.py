from wiz101_auto.combat.brain import Strategy, decide, expected_damage
from wiz101_auto.combat.model import (
    ActionKind,
    Battle,
    Card,
    Combatant,
    Effect,
    EffectKind,
    Target,
)


def dmg_card(i, name, dmg, pips=1, target=Target.ENEMY_SINGLE, castable=True):
    return Card(i, name, pip_cost=pips, effects=[Effect(EffectKind.DAMAGE, target, dmg)], castable=castable)


def heal_card(i, name, amount, target=Target.SELF):
    return Card(i, name, pip_cost=2, effects=[Effect(EffectKind.HEAL, target, amount)])


def blade_card(i, pct=35):
    return Card(i, "Fireblade", pip_cost=0, effects=[Effect(EffectKind.BLADE, Target.ALLY_SINGLE, pct)])


def trap_card(i, pct=30):
    return Card(i, "Fire Trap", pip_cost=0, effects=[Effect(EffectKind.TRAP, Target.ENEMY_SINGLE, pct)])


def enchant_card(i, amount=100):
    return Card(i, "Strong", pip_cost=0, effects=[Effect(EffectKind.ENCHANT_DAMAGE, Target.SPELL, amount)])


def me(hp=500, max_hp=500, **kw):
    return Combatant("Me", hp, max_hp, is_client=True, **kw)


def enemy(name, hp, boss=False, **kw):
    return Combatant(name, hp, hp, is_enemy=True, is_boss=boss, **kw)


def battle(cards, enemies, my=None):
    return Battle(me=my or me(), allies=[], enemies=enemies, cards=cards, pips=3)


def test_kills_the_enemy_it_can_finish():
    b = battle([dmg_card(0, "Fire Cat", 100)], [enemy("Big", 400), enemy("Small", 90)])
    a = decide(b)
    assert a.kind is ActionKind.CAST
    assert a.target.name == "Small"


def test_prefers_aoe_against_groups():
    cards = [dmg_card(0, "Fire Cat", 100), dmg_card(1, "Meteor", 90, pips=3, target=Target.ENEMY_ALL)]
    b = battle(cards, [enemy("A", 80), enemy("B", 80), enemy("C", 80)])
    a = decide(b)
    assert a.card.name == "Meteor"
    assert a.target is None


def test_heals_when_low():
    cards = [dmg_card(0, "Fire Cat", 100), heal_card(1, "Fairy", 400)]
    b = battle(cards, [enemy("A", 300)], my=me(hp=100))
    a = decide(b)
    assert a.card.name == "Fairy"


def test_does_not_heal_when_healthy():
    cards = [dmg_card(0, "Fire Cat", 500), heal_card(1, "Fairy", 400)]
    a = decide(battle(cards, [enemy("A", 300)]))
    assert a.card.name == "Fire Cat"


def test_enchants_before_attacking():
    cards = [dmg_card(0, "Fire Cat", 100), enchant_card(1)]
    a = decide(battle(cards, [enemy("A", 150)]))
    assert a.kind is ActionKind.ENCHANT
    assert a.card.name == "Strong" and a.target_card.name == "Fire Cat"


def test_does_not_enchant_already_enchanted_card():
    cat = dmg_card(0, "Fire Cat", 200)
    cat.enchanted = True
    a = decide(battle([cat, enchant_card(1)], [enemy("A", 150)]))
    assert a.kind is ActionKind.CAST


def test_blades_before_hitting_a_boss():
    cards = [dmg_card(0, "Fire Cat", 100), blade_card(1)]
    a = decide(battle(cards, [enemy("Boss", 2000, boss=True)]))
    assert a.card.name == "Fireblade"


def test_stops_blading_at_limit():
    cards = [dmg_card(0, "Fire Cat", 100), blade_card(1), trap_card(2)]
    b = battle(cards, [enemy("Boss", 2000, boss=True, trap_count=2)], my=me(blade_count=2))
    a = decide(b)
    assert a.card.name == "Fire Cat"


def test_sets_up_while_waiting_for_pips():
    cards = [dmg_card(0, "Meteor", 300, pips=5, castable=False), blade_card(1)]
    a = decide(battle(cards, [enemy("A", 200)]))
    assert a.card.name == "Fireblade"


def test_passes_with_nothing_to_do():
    cards = [dmg_card(0, "Meteor", 300, pips=5, castable=False)]
    a = decide(battle(cards, [enemy("A", 200)]))
    assert a.kind is ActionKind.PASS


def test_discards_dead_card_when_hand_full():
    junk = Card(6, "Weird", pip_cost=4, effects=[Effect(EffectKind.OTHER, Target.NONE, 0)], castable=False)
    cards = [dmg_card(i, f"Big{i}", 300, pips=6, castable=False) for i in range(6)] + [junk]
    a = decide(battle(cards, [enemy("A", 200)]))
    assert a.kind is ActionKind.DISCARD and a.card.name == "Weird"
    a2 = decide(battle(cards, [enemy("A", 200)]), discards_left=0)
    assert a2.kind is ActionKind.PASS


def test_never_discards_damage_or_treasure():
    tc = Card(0, "TC", treasure=True, effects=[Effect(EffectKind.OTHER, Target.NONE, 0)], castable=False)
    cards = [tc] + [dmg_card(i, f"Big{i}", 300, pips=6, castable=False) for i in range(1, 7)]
    assert decide(battle(cards, [enemy("A", 200)])).kind is ActionKind.PASS


def test_expected_damage_includes_buffs_and_accuracy():
    card = dmg_card(0, "Fire Cat", 100)
    card.accuracy = 80
    attacker = me(outgoing_boost=0.35)
    target = enemy("A", 500, incoming_boost=0.3)
    assert abs(expected_damage(card, attacker, target) - 100 * 1.35 * 1.3 * 0.8) < 1e-6


def test_no_enemies_passes():
    assert decide(battle([dmg_card(0, "Cat", 100)], [enemy("A", 0)])).kind is ActionKind.PASS


def test_custom_strategy_threshold():
    cards = [dmg_card(0, "Fire Cat", 100), heal_card(1, "Fairy", 400)]
    b = battle(cards, [enemy("A", 300)], my=me(hp=300))
    assert decide(b, Strategy(heal_threshold=0.7)).card.name == "Fairy"
    assert decide(b, Strategy(heal_threshold=0.5)).card.name == "Fire Cat"


def summon_card(i, pips=1):
    return Card(i, "Golem Minion", pip_cost=pips, effects=[Effect(EffectKind.SUMMON, Target.SELF, 0)])


def test_summons_a_minion_before_attacking():
    b = battle([dmg_card(0, "Blood Bat", 90), summon_card(1)], [enemy("Troll", 500)])
    action = decide(b)
    assert action.kind is ActionKind.CAST and action.card.name == "Golem Minion" and action.target is None


def test_does_not_summon_while_a_minion_is_out_or_when_healing_is_urgent():
    b = battle([dmg_card(0, "Blood Bat", 90), summon_card(1)], [enemy("Troll", 500)])
    b.allies = [Combatant("Golem", 300, 300, is_minion=True)]
    assert decide(b).card.name == "Blood Bat"
    hurt = battle([heal_card(0, "Pixie", 400), summon_card(1)], [enemy("Troll", 500)], my=me(hp=100))
    assert decide(hurt).card.name == "Pixie"


def school_card(i, name, school, dmg):
    effects = [Effect(EffectKind.DAMAGE, Target.ENEMY_SINGLE, dmg)]
    return Card(i, name, school=school, pip_cost=1, effects=effects)


def test_prefers_a_spell_the_target_does_not_resist():
    myth_bat = school_card(0, "Blood Bat", "Myth", 90)
    fire_cat = school_card(1, "Fire Cat", "Fire", 80)
    boss = enemy("General Akilles", 1000, boss=True, school="myth", resist={"myth": 0.4})
    action = decide(battle([myth_bat, fire_cat], [boss]), Strategy(boss_setup=False))
    assert action.card.name == "Fire Cat"


def test_falls_back_to_school_opposites_without_stats():
    storm = school_card(0, "Thunder Snake", "Storm", 100)
    myth = school_card(1, "Blood Bat", "Myth", 100)
    target = enemy("Troll", 1000, school="myth")
    assert expected_damage(storm, me(), target) > expected_damage(myth, me(), target)


def test_summons_the_newest_minion():
    summon = [Effect(EffectKind.SUMMON, Target.SELF, 0)]
    puppet = Card(0, "Golem Minion", effects=summon, template_name="Minion Myth 000")
    troll = Card(1, "Troll Minion", effects=summon, template_name="Minion Myth 001")
    assert decide(battle([puppet, troll], [enemy("Troll", 500)])).card.name == "Troll Minion"


def test_finishing_the_last_enemy_beats_healing_and_summoning():
    summon = Card(2, "Troll Minion", effects=[Effect(EffectKind.SUMMON, Target.SELF, 0)])
    hand = [dmg_card(0, "Blood Bat", 90), heal_card(1, "Pixie", 400), summon]
    b = battle(hand, [enemy("Troll", 500)], my=me(hp=300))  # hurt, but not low
    b.enemies[0].health = 40
    action = decide(b)
    assert action.card.name == "Blood Bat" and action.reason.startswith("finish")


def test_low_health_heals_before_finishing_the_last_enemy():
    # A Desert Golem killed the wizard at 267/815 while it cast Troll for the kill:
    # the enemy can act first, and spells fizzle. Low on health, heal first.
    hand = [dmg_card(0, "Troll", 400, pips=2), heal_card(1, "Pixie", 400)]
    b = battle(hand, [enemy("Desert Golem", 395)], my=me(hp=100))
    b.pips = 2
    assert decide(b).card.name == "Pixie"


def test_kill_counts_traps_and_ignores_accuracy():
    hit = [Effect(EffectKind.DAMAGE, Target.ENEMY_SINGLE, 80)]
    bat = Card(0, "Blood Bat", pip_cost=1, accuracy=80, effects=hit)
    target = enemy("Troll", 100, incoming_boost=0.3)  # trapped: 80 * 1.3 = 104 >= 100
    action = decide(battle([bat], [target]))
    assert action.reason.startswith("finish")


def test_focuses_the_weakest_of_several_enemies():
    b = battle([dmg_card(0, "Blood Bat", 90)], [enemy("Big", 600), enemy("Small", 200)])
    assert decide(b, Strategy(boss_setup=False)).target.name == "Small"


def test_heals_before_a_kill_that_does_not_end_the_fight():
    hand = [dmg_card(0, "Blood Bat", 90), heal_card(1, "Pixie", 400)]
    b = battle(hand, [enemy("A", 50), enemy("B", 500)], my=me(hp=100))
    assert decide(b).card.name == "Pixie"


def test_out_of_mana_detection():
    from wiz101_auto.combat.fighter import out_of_mana

    grayed = [dmg_card(0, "Blood Bat", 90, castable=False)]
    assert out_of_mana(battle(grayed, [enemy("Harvest Lord", 900)], my=me(mana=0)))
    assert not out_of_mana(battle(grayed, [enemy("Harvest Lord", 900)], my=me(mana=20)))
    assert not out_of_mana(battle([dmg_card(0, "Blood Bat", 90)], [enemy("X", 90)], my=me(mana=2)))


def test_saves_pips_for_a_heal_when_low():
    summon = Card(2, "Troll Minion", pip_cost=1, effects=[Effect(EffectKind.SUMMON, Target.SELF, 0)])
    pixie = Card(1, "Pixie", pip_cost=2, castable=False, effects=[Effect(EffectKind.HEAL, Target.SELF, 400)])
    hand = [dmg_card(0, "Blood Bat", 90), pixie, summon]
    b = battle(hand, [enemy("Alicane", 480, boss=True)], my=me(hp=68, max_hp=628))
    b.pips = 1
    action = decide(b)
    assert action.kind is ActionKind.PASS and "Pixie" in action.reason
    # ...but a blow that ends the fight still wins
    b.enemies[0].health = 60
    assert decide(b).card.name == "Blood Bat"


def test_heals_earlier_against_a_boss():
    hand = [dmg_card(0, "Blood Bat", 90), heal_card(1, "Pixie", 400)]
    boss_fight = battle(hand, [enemy("Alicane", 480, boss=True)], my=me(hp=325, max_hp=628))
    assert decide(boss_fight).card.name == "Pixie"
    normal = battle(hand, [enemy("Magma Man", 235)], my=me(hp=325, max_hp=628))
    assert decide(normal).card.name == "Blood Bat"


def test_saves_a_pip_for_a_much_stronger_attack():
    wand = dmg_card(0, "Fire Cat", 70)
    troll = dmg_card(1, "Troll", 190, pips=2, castable=False)
    trap = trap_card(2)
    b = battle([wand, troll], [enemy("Alicane", 480, boss=True)])
    b.pips = 1
    a = decide(b, Strategy(boss_setup=False))
    assert a.kind is ActionKind.DISCARD and a.card.name == "Fire Cat"  # a chip hit: bin it, draw
    assert decide(b, Strategy(boss_setup=False), discards_left=0).kind is ActionKind.PASS
    b.cards.append(trap)  # a free trap is played while waiting
    assert decide(b, Strategy(boss_setup=False), discards_left=0).card.name == "Fire Trap"
    b2 = battle([dmg_card(0, "Blood Bat", 90), troll], [enemy("Imp", 80)])
    b2.pips = 1
    assert decide(b2).card.name == "Blood Bat"  # a kill now beats waiting


def test_traps_a_mob_that_would_survive_the_hit():
    b = battle([dmg_card(0, "Troll", 190, pips=2), trap_card(1)], [enemy("Skeletal Warrior", 235)])
    assert decide(b).card.name == "Fire Trap"
    b2 = battle([dmg_card(0, "Troll", 190, pips=2), trap_card(1)], [enemy("Imp", 150)])
    assert decide(b2).card.name == "Troll"  # kills outright: no trap needed


def _myth_me(**kw):
    return me(school="myth", **kw)


def test_discards_off_school_gear_cards():
    hit = [Effect(EffectKind.DAMAGE, Target.ENEMY_SINGLE, 65)]
    wand = Card(0, "Frost Beetle", school="Ice", pip_cost=1, item=True, effects=hit)
    heal = [Effect(EffectKind.HEAL, Target.SELF, 100)]
    heartbeat = Card(1, "Heartbeat", school="Life", item=True, effects=heal)
    troll = dmg_card(2, "Troll", 190, pips=2)
    troll.school = "Myth"
    bats = [dmg_card(3, "Blood Bat", 90), dmg_card(4, "Blood Bat", 90)]  # two cheap hits kept anyway
    for bat in bats:
        bat.school = "Myth"
    b = battle([wand, heartbeat, troll, *bats], [enemy("Alicane", 480, boss=True)], my=_myth_me())
    action = decide(b)
    assert action.kind is ActionKind.DISCARD and action.card.name == "Frost Beetle"
    assert decide(b, discards_left=0).kind is not ActionKind.DISCARD


def test_holds_troll_until_the_target_is_trapped():
    troll = dmg_card(0, "Troll", 190, pips=2)
    b = battle([troll], [enemy("Alicane", 480, boss=True)], my=_myth_me())
    b.pips = 2
    assert decide(b).card.name == "Troll"  # nothing could trap it: don't wait
    b.allies.append(Combatant("Troll Defender", 300, 300, is_minion=True))  # our minion traps
    assert decide(b).kind is ActionKind.PASS
    b.enemies[0].trap_count, b.enemies[0].incoming_boost = 1, 0.4  # trapped (by us or the minion)
    assert decide(b).card.name == "Troll"
    b.enemies[0].trap_count, b.enemies[0].incoming_boost = 0, 0.0
    b.pips = 4  # don't wait forever
    assert decide(b).card.name == "Troll"


def test_does_not_wait_for_a_trap_on_a_shielded_target():
    troll = dmg_card(0, "Troll", 190, pips=2)
    boss = enemy("Harvest Lord", 510, boss=True, shield_count=2)
    boss.incoming_effects = [("spell:7", "", -0.5), ("spell:8", "myth", -0.4)]
    b = battle([troll], [boss], my=_myth_me())
    b.allies.append(Combatant("Troll Defender", 300, 300, is_minion=True))
    b.pips = 2
    assert decide(b).card.name == "Troll"  # waiting never removes shields; hitting does


def test_quick_fight_skips_the_minion_and_plans_trap_then_troll():
    from wiz101_auto.combat.brain import plan_fight

    troll = dmg_card(0, "Troll", 190, pips=2)
    summon = Card(1, "Troll Minion", pip_cost=1, effects=[Effect(EffectKind.SUMMON, Target.SELF, 0)])
    myth_trap = Card(2, "Myth Trap", effects=[Effect(EffectKind.TRAP, Target.ENEMY_SINGLE, 40)])
    b = battle([troll, summon, myth_trap], [enemy("Fire Elf Hunter", 250)])
    b.pips = 1
    plan = plan_fight(b)
    assert plan.skip_summon and plan.rounds == 2 and "Myth Trap, then Troll" in plan.text
    assert decide(b).card.name == "Myth Trap"
    boss = battle([troll, summon, myth_trap], [enemy("Alicane", 480, boss=True)])
    assert not plan_fight(boss).skip_summon


def test_identical_traps_trigger_once_and_only_for_their_school():
    from wiz101_auto.combat.brain import hit_damage

    troll = dmg_card(0, "Troll", 100)
    troll.school = "Myth"
    storm = dmg_card(1, "Thunder Snake", 100)
    storm.school = "Storm"
    target = enemy("Alicane", 480)
    target.incoming_effects = [("spell:1", "myth", 0.4), ("spell:1", "myth", 0.4)]  # two Myth Traps
    assert round(hit_damage(troll, me(), target)) == 140  # not 180 or 196
    assert round(hit_damage(storm, me(), target)) == 100  # a Myth Trap doesn't boost Storm
    target.incoming_effects.append(("spell:2", "", 0.3))  # a different, all-school trap
    assert round(hit_damage(troll, me(), target)) == 182  # 1.4 * 1.3


def test_hits_a_trapped_target_instead_of_summoning():
    summon = Card(1, "Troll Minion", pip_cost=0, effects=[Effect(EffectKind.SUMMON, Target.SELF, 0)])
    troll = dmg_card(0, "Troll", 190, pips=2)
    boss = enemy("Harvest Lord", 900, boss=True, trap_count=2, incoming_boost=0.8)
    b = battle([troll, summon], [boss])
    b.pips = 2
    assert decide(b).card.name == "Troll"
    untrapped = battle([troll, summon], [enemy("Harvest Lord", 900, boss=True)])
    assert decide(untrapped).card.name == "Troll Minion"


def test_no_summon_when_the_boss_is_nearly_dead():
    summon = Card(1, "Troll Minion", pip_cost=0, effects=[Effect(EffectKind.SUMMON, Target.SELF, 0)])
    bat = dmg_card(0, "Blood Bat", 100)
    b = battle([bat, summon], [enemy("Foulgaze", 550, boss=True)])
    b.enemies[0].health = 180  # two Blood Bats
    b.pips = 1
    assert decide(b).card.name == "Blood Bat"
    b.enemies[0].health = 550
    assert decide(b).card.name == "Troll Minion"


def test_uses_a_free_trap_instead_of_just_passing():
    pixie = Card(0, "Pixie", pip_cost=2, effects=[Effect(EffectKind.HEAL, Target.SELF, 400)], castable=False)
    trap = Card(1, "Myth Trap", pip_cost=0, effects=[Effect(EffectKind.TRAP, Target.ENEMY_SINGLE, 40)])
    boss = enemy("Edo Nirini", 545, boss=True)
    b = battle([pixie, trap], [boss], my=me(275, 815))
    b.pips = 1  # Pixie next round: save the pip, but the trap is free
    action = decide(b)
    assert action.card.name == "Myth Trap" and action.target is boss
    b.cards = [pixie]
    assert decide(b).kind is ActionKind.PASS


def test_keeps_two_cheap_hits_instead_of_discarding_them():
    myth_me = _myth_me()
    fire_cat = dmg_card(0, "Fire Cat", 80)
    fire_cat.school, fire_cat.item = "Fire", True
    imp = dmg_card(1, "Imp", 70)
    imp.school, imp.item = "Fire", True
    troll = dmg_card(2, "Troll", 190, pips=2)
    troll.school = "Myth"
    b = battle([fire_cat, imp, troll], [enemy("Golem", 400)], my=myth_me)
    b.pips = 0
    assert decide(b).kind is not ActionKind.DISCARD  # only 2 cheap hits: keep both
    thunder = dmg_card(3, "Thunder Snake", 75)
    thunder.school, thunder.item = "Storm", True
    b.cards.append(thunder)
    assert decide(b).kind is ActionKind.DISCARD  # a third can go


def test_no_summon_when_a_card_in_hand_finishes_the_last_enemy():
    summon = Card(1, "Troll Minion", pip_cost=0, effects=[Effect(EffectKind.SUMMON, Target.SELF, 0)])
    cyclops = dmg_card(0, "Cyclops", 400, pips=3, castable=False)
    b = battle([cyclops, summon], [enemy("Edo Nirini", 545, boss=True)])
    b.enemies[0].health = 14
    b.pips = 1
    assert decide(b).kind is not ActionKind.CAST or decide(b).card.name != "Troll Minion"


def super_strike(i=0, dmg=40):
    return Card(i, "Super Strike", school="myth", pip_cost=0, item=True,
                effects=[Effect(EffectKind.DAMAGE, Target.ENEMY_SINGLE, dmg)])


def myth_hit(i, dmg=300, pips=2, castable=True):
    c = dmg_card(i, "Troll", dmg, pips=pips, castable=castable)
    c.school = "myth"
    return c


def test_super_strike_instead_of_passing():
    b = battle([super_strike(), myth_hit(1, pips=2)], [enemy("Gobbler", 900)])
    b.pips = 1
    b.cards[-1].castable = False  # Troll next round: nothing else to do but pass
    a = decide(b)
    assert a.kind is ActionKind.CAST and a.card.name == "Super Strike"


def test_trap_or_blade_before_super_strike():
    b = battle([super_strike(), trap_card(1), myth_hit(2, pips=2)], [enemy("Gobbler", 900)])
    b.pips = 1
    b.cards[-1].castable = False
    assert decide(b).card.name == "Fire Trap"


def test_super_strike_does_not_waste_a_trap():
    trapped = enemy("Gobbler", 900, trap_count=1, incoming_effects=[("Myth Trap", "myth", 0.3)])
    b = battle([super_strike(), myth_hit(1, pips=2)], [trapped])
    b.pips = 1
    b.cards[-1].castable = False
    assert decide(b).kind is ActionKind.PASS


def test_super_strike_on_a_heavily_trapped_enemy():
    traps = [(f"t{i}", "myth", 0.3) for i in range(3)]
    trapped = enemy("Gobbler", 900, trap_count=3, incoming_effects=traps)
    b = battle([super_strike(), myth_hit(1, pips=2)], [trapped])
    b.pips = 1
    b.cards[-1].castable = False
    assert decide(b).card.name == "Super Strike"


def test_super_strike_breaks_a_shield_before_trapping():
    shielded = enemy("Troll", 900, shield_count=1, incoming_effects=[("Myth Shield", "myth", -0.5)])
    b = battle([trap_card(0), super_strike(1), myth_hit(2, pips=2)], [shielded])
    b.pips = 1
    b.cards[-1].castable = False
    a = decide(b)
    assert a.card.name == "Super Strike" and "shield" in a.reason


def test_super_strike_finishes_an_enemy():
    b = battle([super_strike(), myth_hit(1, pips=2)], [enemy("Big", 900), enemy("Weak", 30)])
    a = decide(b)
    assert a.card.name == "Super Strike" and a.target.name == "Weak"


def test_super_strike_is_not_the_turns_attack():
    b = battle([super_strike(), myth_hit(1, pips=2)], [enemy("Gobbler", 250)])
    assert decide(b).card.name == "Troll"


def test_super_strike_keeps_our_blade_for_the_real_hit():
    b = battle([super_strike(), myth_hit(1, pips=2)], [enemy("Gobbler", 900)],
               my=me(blade_count=1, outgoing_effects=[("Mythblade", "myth", 0.35)]))
    b.pips = 1
    b.cards[-1].castable = False
    assert decide(b).kind is ActionKind.PASS


def test_discards_spare_super_strikes_but_keeps_one():
    b = battle([super_strike(0), super_strike(1), myth_hit(2, pips=2)], [enemy("Gobbler", 900)],
               my=_myth_me())
    b.pips = 1
    b.cards[-1].castable = False
    a = decide(b)
    assert a.kind is ActionKind.DISCARD and a.card.name == "Super Strike"
    b.cards = [c for c in b.cards if c is not a.card]
    assert decide(b).card.name == "Super Strike"  # the last one is played, not binned
    assert decide(b).kind is ActionKind.CAST


def mythblade(i):
    blade = [Effect(EffectKind.BLADE, Target.ALLY_SINGLE, 35)]
    return Card(i, "Mythblade", school="myth", pip_cost=0, effects=blade)


def myth_trap(i):
    trap = [Effect(EffectKind.TRAP, Target.ENEMY_SINGLE, 30)]
    return Card(i, "Myth Trap", school="myth", pip_cost=0, effects=trap)


def test_no_second_copy_of_a_blade_that_is_up():
    bladed = me(blade_count=1, outgoing_effects=[("spell:1", "myth", 0.35)])
    boss = enemy("Krokenkahmen", 695, boss=True)
    b = battle([mythblade(0), myth_trap(1), myth_hit(2, dmg=300)], [boss], my=bladed)
    a = decide(b)
    assert a.card.name == "Myth Trap"  # not the same Mythblade again


def test_duplicate_blade_only_after_new_effects_when_passing():
    bladed = me(blade_count=1, outgoing_effects=[("spell:1", "myth", 0.35)])
    cards = [mythblade(0), myth_trap(1), myth_hit(2, pips=3, castable=False)]
    b = battle(cards, [enemy("Gobbler", 900)], my=bladed)
    b.pips = 2
    assert decide(b).card.name == "Myth Trap"
    b.cards = [mythblade(0), myth_hit(2, pips=3, castable=False)]
    a = decide(b)
    assert a.card.name == "Mythblade" and "copy" in a.reason


def ether_shield(i):
    shields = [Effect(EffectKind.SHIELD, Target.ALLY_SINGLE, -70, school="life"),
               Effect(EffectKind.SHIELD, Target.ALLY_SINGLE, -70, school="death")]
    return Card(i, "Ether Shield", school="myth", pip_cost=0, effects=shields)


def myth_prism(i):
    return Card(i, "Myth Prism", school="myth", pip_cost=0,
                effects=[Effect(EffectKind.OTHER, Target.ENEMY_SINGLE, 0)])


def test_ether_shield_against_a_death_enemy_instead_of_passing():
    b = battle([ether_shield(0), myth_hit(1, pips=3, castable=False)], [enemy("Mummy", 900, school="death")])
    b.pips = 2
    a = decide(b)
    assert a.card.name == "Ether Shield" and "death" in a.reason


def test_ether_shield_not_twice_and_discarded_when_useless():
    shielded = me(incoming_effects=[("spell:9", "death", -0.7)])
    b = battle([ether_shield(0), myth_hit(1, pips=3, castable=False)], [enemy("Mummy", 900, school="death")],
               my=shielded)
    b.pips = 2
    assert decide(b).kind is ActionKind.PASS
    cards = [ether_shield(0), myth_hit(1, pips=3, castable=False)]
    fire = battle(cards, [enemy("Guard", 900, school="fire")], my=_myth_me())
    fire.pips = 2
    a = decide(fire)
    assert a.kind is ActionKind.DISCARD and a.card.name == "Ether Shield"


def test_prism_only_on_an_enemy_weak_to_the_other_school():
    cards = [myth_prism(0), myth_hit(1, pips=3, castable=False)]
    b = battle(cards, [enemy("Myth Troll", 900, school="myth")])
    b.pips = 2
    a = decide(b)
    assert a.card.name == "Myth Prism" and a.target.name == "Myth Troll"
    cards = [myth_prism(0), myth_hit(1, pips=3, castable=False)]
    neutral = battle(cards, [enemy("Guard", 900, school="fire")])
    neutral.pips = 2
    a = decide(neutral)
    assert a.kind is ActionKind.DISCARD and a.card.name == "Myth Prism"  # never useful in this fight
    assert decide(neutral, discards_left=0).kind is ActionKind.PASS


def test_prism_kept_while_an_enemy_is_unknown():
    cards = [myth_prism(0), myth_hit(1, pips=3, castable=False)]
    b = battle(cards, [enemy("Mystery", 900)])  # no school, no resist read
    b.pips = 2
    assert decide(b).kind is ActionKind.PASS


def test_weak_hit_does_not_waste_blade_and_trap():
    bat = dmg_card(0, "Blood Bat", 90, pips=1)
    bat.school = "myth"
    bladed = me(blade_count=1, outgoing_effects=[("spell:1", "myth", 0.35)])
    stalker = enemy("Sand Stalker", 435, trap_count=1, incoming_effects=[("spell:2", "myth", 0.4)])
    b = battle([bat, myth_hit(1, pips=3, castable=False)], [stalker], my=bladed)
    b.pips = 2
    a = decide(b)
    assert a.kind is ActionKind.DISCARD and a.card.name == "Blood Bat"
    assert decide(b, discards_left=0).kind is ActionKind.PASS


def test_weak_hit_still_finishes_an_enemy():
    bat = dmg_card(0, "Blood Bat", 90, pips=1)
    bat.school = "myth"
    bladed = me(blade_count=1, outgoing_effects=[("spell:1", "myth", 0.35)])
    b = battle([bat], [enemy("Sand Stalker", 100)], my=bladed)
    assert decide(b).card.name == "Blood Bat"


def test_chip_hit_discarded_to_draw_a_bigger_one():
    bat = dmg_card(0, "Blood Bat", 90, pips=1)
    bat.school = "myth"
    b = battle([bat, myth_hit(1, pips=3, castable=False)], [enemy("Sand Stalker", 435)], my=_myth_me())
    b.pips = 2
    a = decide(b)
    assert a.kind is ActionKind.DISCARD and a.card.name == "Blood Bat"


def test_prism_only_once_per_enemy():
    cards = [myth_prism(0), myth_hit(1, pips=3, castable=False)]
    b = battle(cards, [enemy("Myth Troll", 900, school="myth")])
    b.pips = 2
    b.prismed = {"Myth Troll"}
    assert decide(b, discards_left=0).kind is ActionKind.PASS


def test_prism_skipped_when_myth_traps_outweigh_the_school_gain():
    traps = [("spell:1", "myth", 0.4), ("spell:2", "myth", 0.3)]
    boss = enemy("Myth Boss", 900, school="myth", trap_count=2, incoming_effects=traps)
    cards = [myth_prism(0), myth_hit(1, pips=3, castable=False)]
    b = battle(cards, [boss])
    b.pips = 2
    a = decide(b, discards_left=0)
    assert not (a.kind is ActionKind.CAST and a.card.name == "Myth Prism")


def test_troll_is_never_discarded_as_a_chip_hit():
    troll = myth_hit(0, dmg=190, pips=2)
    bladed = me(blade_count=1, outgoing_effects=[("spell:1", "myth", 0.35)])
    b = battle([troll], [enemy("Bort Malletmane", 585, boss=True)], my=bladed)
    a = decide(b)
    assert not (a.kind is ActionKind.DISCARD and a.card.name == "Troll")


def test_kills_now_with_troll_instead_of_waiting_for_cyclops():
    troll = myth_hit(0, dmg=190, pips=2)
    cyclops = myth_hit(1, dmg=295, pips=3, castable=False)
    cyclops.name = "Cyclops"
    bladed = me(blade_count=1, outgoing_effects=[("spell:1", "myth", 0.35)])
    target = enemy("Sand Stalker", 300, trap_count=1, incoming_effects=[("spell:2", "myth", 0.4)])
    b = battle([troll, cyclops], [target], my=bladed)
    b.pips = 2
    a = decide(b)
    assert a.kind is ActionKind.CAST and a.card.name == "Troll"  # ~359 kills 300 now


def test_trap_first_when_that_kills_sooner():
    troll = myth_hit(0, dmg=190, pips=2)
    b = battle([troll, myth_trap(1)], [enemy("Golem", 250)], my=_myth_me())
    b.pips = 2
    a = decide(b)
    assert a.card.name == "Myth Trap"  # trap now, Troll next round kills; Troll now doesn't


def minion_card(i):
    return Card(i, "Troll Minion", school="myth", pip_cost=0,
                effects=[Effect(EffectKind.SUMMON, Target.NONE, 1)])


def test_one_minion_per_fight_and_not_late_against_one_enemy():
    boss = enemy("Itennu Sokkwi", 590, boss=True)
    cards = [minion_card(0), myth_hit(1, dmg=295, pips=3, castable=False)]
    b = battle(cards, [boss])
    b.pips, b.round = 1, 1
    assert decide(b, discards_left=0).card.name == "Troll Minion"  # opening round: fine
    b.summoned = 1
    a = decide(b, discards_left=0)
    assert not (a.card and a.card.name == "Troll Minion")
    b.summoned, b.round = 0, 8
    a = decide(b, discards_left=0)
    assert not (a.card and a.card.name == "Troll Minion")  # one enemy left, late: set up and hit


def test_super_strike_not_used_to_chip_through_buffs():
    troll = myth_hit(1, dmg=190, pips=2)
    cyclops = myth_hit(2, dmg=295, pips=3, castable=False)
    cyclops.name = "Cyclops"
    bladed = me(blade_count=1, outgoing_effects=[("spell:1", "myth", 0.35)])
    traps = [("spell:2", "myth", 0.4), ("spell:3", "myth", 0.4)]
    boss = enemy("Itennu Sokkwi", 590, boss=True, trap_count=2, incoming_effects=traps)
    b = battle([super_strike(0, dmg=60), troll, cyclops], [boss], my=bladed)
    b.pips = 1
    a = decide(b, discards_left=0)
    assert not (a.kind is ActionKind.CAST and a.card.name == "Super Strike")
