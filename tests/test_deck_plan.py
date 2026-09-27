from wiz101_auto.combat.model import Card, Effect, EffectKind, Target
from wiz101_auto.deck_plan import DeckPolicy, SpellInfo, plan_adds_cards, plan_deck, unknown_deck_spells


def spell(name, kind, value, pips=1, target=Target.ENEMY_SINGLE, school="Myth", max_copies=4, acc=80):
    card = Card(0, name, school=school, pip_cost=pips, accuracy=acc, effects=[Effect(kind, target, value)])
    return SpellInfo(card, max_copies)


BAT = spell("Blood Bat", EffectKind.DAMAGE, 85, pips=1)
TROLL = spell("Troll", EffectKind.DAMAGE, 250, pips=3)
CYCLOPS = spell("Cyclops", EffectKind.DAMAGE, 330, pips=4)
QUAKE = spell("Earthquake", EffectKind.DAMAGE, 200, pips=4, target=Target.ENEMY_ALL)
BLADE = spell("Mythblade", EffectKind.BLADE, 35, pips=0, target=Target.ALLY_SINGLE)
TRAP = spell("Myth Trap", EffectKind.TRAP, 30, pips=0)
SHIELD = spell("Myth Shield", EffectKind.SHIELD, -50, pips=0, target=Target.ALLY_SINGLE)
MINION = spell("Troll Minion", EffectKind.OTHER, 0, pips=4, target=Target.NONE)


def test_level_one_deck_is_just_the_starter_attack():
    plan = plan_deck([BAT], "Myth")
    assert plan.totals == {"Blood Bat": 4}


def test_keeps_cheapest_attack_and_best_others():
    plan = plan_deck([BAT, TROLL, CYCLOPS, QUAKE, BLADE, TRAP, SHIELD, MINION], "Myth")
    expected = {"Blood Bat", "Troll", "Cyclops", "Earthquake", "Mythblade", "Myth Trap", "Myth Shield"}
    assert set(plan.totals) == expected
    assert "Troll Minion" not in plan.totals  # combat brain can't use minions yet
    assert plan.totals["Mythblade"] == 2 and plan.totals["Myth Shield"] == 1


def test_limits_distinct_attacks():
    plan = plan_deck([BAT, TROLL, CYCLOPS, QUAKE], "Myth", DeckPolicy(attack_spells=2))
    attacks = [n for n in plan.totals if n in {"Blood Bat", "Troll", "Cyclops", "Earthquake"}]
    assert len(attacks) == 2 and "Blood Bat" in attacks


def test_respects_max_copies():
    plan = plan_deck([spell("Rare", EffectKind.DAMAGE, 100, max_copies=1)], "Myth")
    assert plan.totals == {"Rare": 1}


def test_capacity_covers_every_role_before_topping_up():
    plan = plan_deck([BAT, TROLL, BLADE, TRAP, SHIELD], "Myth", DeckPolicy(capacity=8))
    assert sum(plan.totals.values()) == 8
    assert all(n in plan.totals for n in ("Blood Bat", "Troll", "Mythblade", "Myth Trap", "Myth Shield"))


def test_steps_do_core_pass_first():
    plan = plan_deck([BAT, TROLL], "Myth")
    assert sorted(plan.steps[:2]) == [("Blood Bat", 1), ("Troll", 1)]
    assert sum(c for _, c in plan.steps) == 8


def test_exclude_and_include():
    pixie = spell("Pixie", EffectKind.HEAL, 300, pips=2, target=Target.SELF, school="Life")
    policy = DeckPolicy(exclude=["troll"], include={"Pixie": 1})
    plan = plan_deck([BAT, TROLL, pixie], "Myth", policy)
    assert "Troll" not in plan.totals
    assert plan.totals["Pixie"] == 1
    assert plan.steps[0][0] == "Pixie"


def test_empty_spellbook():
    assert plan_deck([], "Myth").totals == {}


def test_unknown_deck_spells_flags_an_incomplete_spellbook_read():
    deck = ["Pixie", "Pixie", "Bloodbat", "Minion Myth 000"]
    assert unknown_deck_spells(deck, ["Scarab - Starter Wand"]) == ["Pixie", "Bloodbat", "Minion Myth 000"]
    assert unknown_deck_spells(deck, ["Pixie", "Bloodbat", "Minion Myth 000", "Troll"]) == []


def test_plan_adds_cards_only_when_it_wants_more_than_the_deck_has():
    deck = ["Pixie"] * 3 + ["Bloodbat"] * 3 + ["Minion Myth 000"] * 3
    assert not plan_adds_cards({"Bloodbat": 3, "Pixie": 3}, deck)
    assert plan_adds_cards({"Bloodbat": 3, "Pixie": 3, "Troll": 2}, deck)


def test_plan_keeps_a_minion():
    from wiz101_auto.combat.model import Card, Effect, EffectKind, Target

    hit = [Effect(EffectKind.DAMAGE, Target.ENEMY_SINGLE, 90)]
    summon = [Effect(EffectKind.SUMMON, Target.SELF, 0)]
    bat = SpellInfo(Card(0, "Bloodbat", school="Myth", pip_cost=1, effects=hit), 3)
    golem = SpellInfo(Card(0, "Minion Myth 000", school="Myth", pip_cost=1, effects=summon), 3)
    plan = plan_deck([bat, golem], "Myth", DeckPolicy())
    assert plan.totals.get("Minion Myth 000") == 2


def test_cards_to_add_only_adds_what_is_missing():
    from wiz101_auto.deck_plan import cards_to_add

    deck = ["Pixie"] * 3 + ["Bloodbat"] * 3 + ["Minion Myth 000"] * 3
    plan = {"Bloodbat": 3, "Troll": 2, "Pixie": 3, "Minion Myth 000": 2}
    assert cards_to_add(plan, deck) == [("Troll", 2)]
