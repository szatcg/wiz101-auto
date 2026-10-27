from wiz101_auto.combat.brain import decide
from wiz101_auto.combat.model import ActionKind, Battle, Card, Combatant, Effect, EffectKind, Target


def cards(pips_hit=5):
    big = Card(0, "Orthrus", pip_cost=pips_hit, school="myth",
               effects=[Effect(EffectKind.DAMAGE, Target.ENEMY_SINGLE, 1500)])
    small = Card(1, "Myth Minotaur", pip_cost=2, school="myth",
                 effects=[Effect(EffectKind.DAMAGE, Target.ENEMY_SINGLE, 400)])
    blade = Card(2, "Mythblade", pip_cost=0, school="myth",
                 effects=[Effect(EffectKind.BLADE, Target.ALLY_SINGLE, 35)])
    return [big, small, blade]


def fight(name, rnd=2, pips=2, hp=9000):
    me = Combatant("Me", 6000, 6000, is_client=True, school="myth")
    foe = Combatant(name, hp, 9000, is_enemy=True, is_boss=True)
    return Battle(me=me, allies=[], enemies=[foe], cards=cards(), pips=pips, round=rnd)


def test_treemugger_no_saving():
    a = decide(fight("Treemugger"))
    assert not (a.kind is ActionKind.PASS and "saving" in (a.reason or ""))


def test_quake_charmer_no_blade_in_round_1():
    a = decide(fight("The Quake Charmer", rnd=1))
    assert not (a.card is not None and a.card.name == "Mythblade")


def test_bundozer_no_blade():
    a = decide(fight("The Bundozer", rnd=3))
    assert not (a.card is not None and a.card.name == "Mythblade")


def feint():
    return Card(5, "Feint", pip_cost=1, school="myth",
                effects=[Effect(EffectKind.TRAP, Target.ENEMY_SINGLE, 70)])


def test_snow_queen_no_feint():
    b = fight("Snow Queen", rnd=2)
    b.cards.append(feint())
    a = decide(b)
    assert not (a.card is not None and a.card.name == "Feint")


def test_snow_queen_round_one_hit():
    b = fight("Snow Queen", rnd=1, pips=2)
    a = decide(b)
    assert a.kind is ActionKind.CAST and a.card is not None and a.card.is_damage


def test_malughast_no_feint():
    b = fight("The Malughast", rnd=2)
    b.cards.append(feint())
    a = decide(b)
    assert not (a.card is not None and a.card.name == "Feint")


def test_malughast_no_malughasts_feint_either():
    b = fight("The Malughast", rnd=2)
    b.cards.append(Card(6, "Malughast's Feint", pip_cost=0, school="shadow",
                        effects=[Effect(EffectKind.TRAP, Target.ENEMY_SINGLE, 70)]))
    a = decide(b)
    assert not (a.card is not None and a.card.name == "Malughast's Feint")


def test_malughast_feint_not_cast_as_a_nova_on_a_shadow_pip():
    b = fight("The Malughast", rnd=7, pips=3)
    b.shadow_pips = 1
    b.cards.append(Card(7, "Malughast's Feint", pip_cost=1, school="shadow",
                        effects=[Effect(EffectKind.TRAP, Target.ENEMY_ALL, 70)]))
    a = decide(b)
    assert not (a.card is not None and a.card.name == "Malughast's Feint")


def test_snow_queen_no_dot_and_never_passes():
    b = fight("Snow Queen", rnd=3)
    b.cards.append(Card(7, "Dot", pip_cost=1, school="myth",
                        effects=[Effect(EffectKind.DOT, Target.ENEMY_SINGLE, 100)]))
    a = decide(b)
    assert a.kind is ActionKind.CAST and a.card is not None and a.card.name != "Dot"


def test_booth_no_single_hits_in_the_setup_rounds():
    for rnd in (1, 2, 5):
        a = decide(fight("General Benedict Brutus von Booth", rnd=rnd, pips=7))
        hit = a.kind is ActionKind.CAST and a.card is not None and a.card.is_damage
        assert not hit


def test_booth_hit_in_round_three():
    b = fight("General Benedict Brutus von Booth", rnd=3, pips=7)
    b.cards = [c for c in b.cards if c.is_damage]
    a = decide(b)
    assert a.kind is ActionKind.CAST and a.card is not None and a.card.is_damage


def test_digs_for_the_finisher_binning_traps_against_ordinary_enemies():
    me = Combatant("Me", 6000, 6000, is_client=True, school="myth")
    foes = [Combatant("Falcon", 5000, 5000, is_enemy=True), Combatant("Falcon", 5000, 5000, is_enemy=True)]
    hand = [feint(), Card(2, "Mythblade", pip_cost=0, school="myth",
                          effects=[Effect(EffectKind.BLADE, Target.ALLY_SINGLE, 35)])]
    orthrus = Card(9, "Orthrus", pip_cost=7, school="myth",
                   effects=[Effect(EffectKind.DAMAGE, Target.ENEMY_SINGLE, 1500)])
    b = Battle(me=me, allies=[], enemies=foes, cards=hand, pips=2, round=1, upcoming=[orthrus, orthrus])
    b.deck_known = True
    a = decide(b)
    assert a.kind is ActionKind.DISCARD and a.card.name == "Feint"


def test_no_digging_against_a_boss():
    b = fight("Treemugger", rnd=1)
    b.cards.append(feint())
    b.upcoming = [Card(9, "Orthrus", pip_cost=7, school="myth",
                       effects=[Effect(EffectKind.DAMAGE, Target.ENEMY_SINGLE, 1500)])]
    b.deck_known = True
    a = decide(b)
    assert not (a.kind is ActionKind.DISCARD and "finisher" in (a.reason or ""))


def test_thins_a_pack_when_health_is_short():
    me = Combatant("Me", 1500, 6000, is_client=True, school="myth")
    foes = [Combatant(f"Guard{i}", 400, 3000, is_enemy=True) for i in range(4)]
    hit = Card(1, "Humongofrog", pip_cost=2, school="myth",
               effects=[Effect(EffectKind.DAMAGE, Target.ENEMY_SINGLE, 3000)])
    blade = Card(2, "Mythblade", pip_cost=0, school="myth",
                 effects=[Effect(EffectKind.BLADE, Target.ALLY_SINGLE, 35)])
    b = Battle(me=me, allies=[], enemies=foes, cards=[blade, hit], pips=3, round=3)
    a = decide(b)
    assert a.kind is ActionKind.CAST and a.card is not None and a.card.name == "Humongofrog"


def test_basil_no_blades_or_traps():
    b = fight("Basil the Brigand", rnd=2)
    b.cards.append(feint())
    a = decide(b)
    assert not (a.card is not None and a.card.name in ("Mythblade", "Feint"))
