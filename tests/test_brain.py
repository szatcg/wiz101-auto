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
    b = battle(hand, [enemy("Troll", 500)], my=me(hp=100))
    b.enemies[0].health = 40
    action = decide(b)
    assert action.card.name == "Blood Bat" and action.reason.startswith("finish")


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
