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
