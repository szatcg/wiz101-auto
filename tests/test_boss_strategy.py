from test_brain import battle, blade_card, enemy, heal_card, me

from wiz101_auto.combat import brain
from wiz101_auto.combat.model import ActionKind


def test_a_boss_fight_never_digs_away_a_heal():
    pixies = [heal_card(0, "Pixie", 400), heal_card(1, "Pixie", 400)]
    b = battle(pixies, [enemy("Malistaire Drake", 8000, boss=True)], my=me(2300, 2380))
    b.upcoming = [blade_card(10 + i) for i in range(10)]
    b.deck_known = True
    a = brain.decide(b)
    assert not (a.kind is ActionKind.DISCARD and a.card.is_heal)



def test_a_hit_all_waits_for_the_blade_that_lets_it_kill_everyone():
    from test_brain import dmg_card, trap_card

    from wiz101_auto.combat.model import Target

    frog = dmg_card(0, "Humongofrog", 470, pips=4, target=Target.ENEMY_ALL)
    blade = blade_card(1, 35)
    a_ = enemy("Clawcutter", 610)
    a_.incoming_effects, a_.trap_count = [("trap", "", 0.3)], 1
    b = battle([frog, blade], [a_, enemy("Clawcutter 2", 610)], my=me(2400, 2400))
    b.pips = 4
    act = brain.decide(b)
    assert act.card is blade  # not the frog that kills one and leaves the other at ~140
    boss = enemy("Boss", 3000, boss=True)
    b2 = battle([frog, blade], [a_, boss], my=me(2400, 2400))
    b2.pips = 4
    assert brain.decide(b2).card is frog  # a bigger boss: kill what it can now
    _ = trap_card


def test_a_castable_hit_all_beats_waiting_for_a_far_off_bigger_one():
    from test_brain import dmg_card

    from wiz101_auto.combat.model import Target

    orthrus = dmg_card(0, "Orthrus", 1300, pips=7, target=Target.ENEMY_ALL, castable=False)
    frog = dmg_card(1, "Humongofrog", 600, pips=4, target=Target.ENEMY_ALL)
    b = battle([orthrus, frog], [enemy("Waverunner", 1230), enemy("Waverunner 2", 1230)], my=me(1156, 2402))
    b.pips, b.power_pips = 2, 1
    act = brain.decide(b)
    assert act.kind is not ActionKind.PASS and act.card is not orthrus


def test_a_second_copy_of_a_blade_that_is_up_is_wasted():
    from wiz101_auto.combat.model import Action, Card, Effect, EffectKind, Target

    blade = Card(0, "Mythblade", pip_cost=0, school="Myth",
                 effects=[Effect(EffectKind.BLADE, Target.ALLY_SINGLE, 35, school="myth")])
    my = me(2000, 2424)
    my.school = "myth"
    b = battle([blade], [enemy("Water Servant", 840)], my=my)
    assert not brain.wasted_setup(Action(ActionKind.CAST, blade, my), b)
    my.outgoing_effects = [("mythblade", "myth", 0.35)]
    assert brain.wasted_setup(Action(ActionKind.CAST, blade, my), b)


def _myth(i, name, dmg, pips, aoe=False, castable=True):
    from wiz101_auto.combat.model import Card, Effect, EffectKind, Target

    t = Target.ENEMY_ALL if aoe else Target.ENEMY_SINGLE
    return Card(i, name, pip_cost=pips, school="myth", castable=castable,
                effects=[Effect(EffectKind.DAMAGE, t, dmg, school="myth")])


def test_a_small_hit_on_a_boss_waits_for_the_big_one():
    frog = _myth(0, "Humongofrog", 600, 4, aoe=True)
    orthrus = _myth(1, "Orthrus", 1300, 7, aoe=True, castable=False)
    my = me(1300, 2221)
    my.school = "myth"
    b = battle([frog, orthrus], [enemy("Runed Annihilator", 2008, boss=True)], my=my)
    b.pips, b.power_pips = 0, 3
    assert brain.decide(b, discards_left=2).card is not frog  # Orthrus in hand: keep the pips
    b2 = battle([frog], [enemy("Runed Annihilator", 2008, boss=True)], my=my)
    b2.pips, b2.power_pips = 0, 3
    b2.upcoming = [_myth(5, "Orthrus", 1300, 7, aoe=True)]
    b2.deck_known = True
    a = brain.decide(b2, discards_left=2)
    assert a.kind is ActionKind.DISCARD and a.card is frog  # Orthrus in the deck: dig for it


def test_a_one_pip_off_school_setup_goes_up_rather_than_a_pass():
    # (Was: it waited for a regular pip. The player: passing with Feints in
    # hand against Porrich wasted turns; set up now.)
    from wiz101_auto.combat.model import Action, Card, Effect, EffectKind, Target

    feint = Card(0, "Feint", pip_cost=1, school="death",
                 effects=[Effect(EffectKind.TRAP, Target.ENEMY_SINGLE, 70)])
    orthrus = _myth(1, "Orthrus", 1300, 7, aoe=True, castable=False)
    my = me(2000, 2221)
    my.school = "myth"
    foe = enemy("Water Servant", 840)
    b = battle([feint, orthrus], [foe, enemy("Water Servant 2", 840)], my=my)
    b.pips, b.power_pips = 0, 3  # 6 now; Feint by a power pip leaves 4, +1 next round = 5 < 7
    assert brain._pip_wise_setup(b, Action(ActionKind.CAST, feint, foe), brain.Strategy()) is None
    b.pips, b.power_pips = 1, 3  # a regular pip pays for it
    assert brain._pip_wise_setup(b, Action(ActionKind.CAST, feint, foe), brain.Strategy()) is None


def test_discards_make_room_to_draw_the_whole_deck_when_a_card_left_ends_it():
    filler = [_myth(i, f"Hit{i}", 50, 2) for i in range(7)]
    my = me(2000, 2221)
    my.school = "myth"
    b = battle(filler, [enemy("Last One", 900)], my=my)
    b.pips, b.power_pips = 1, 3  # 7 next round
    b.upcoming = [_myth(10, "Pixie-ish", 10, 2), _myth(11, "Orthrus", 1300, 7, aoe=True),
                  _myth(12, "Junk", 10, 1)]
    b.deck_known = True
    a = brain.decide(b, discards_left=0)
    assert a.kind is ActionKind.DISCARD and "draw the whole deck" in a.reason
    b.cards = filler[:4]  # room for 3: every card left comes anyway
    assert brain._dig_for_sure_kill(b) is None


def test_draw_chance():
    assert abs(brain.draw_chance(3, 1, 1) - 1 / 3) < 1e-9
    assert brain.draw_chance(3, 1, 3) == 1.0
    assert abs(brain.draw_chance(10, 2, 2) - (1 - 28 / 45)) < 1e-9
    assert brain.draw_chance(10, 0, 5) == 0.0


def test_no_dig_or_reshuffle_with_the_killer_in_hand_a_pip_short():
    from wiz101_auto.combat.model import Card

    orthrus = _myth(0, "Orthrus", 1600, 7, aoe=True, castable=False)
    pixies = [heal_card(i, "Pixie", 400) for i in (1, 2, 3)]
    reshuffle = Card(4, "Reshuffle", pip_cost=4, school="balance")
    my = me(1026, 2221)
    my.school = "myth"
    foes = [enemy("Sand Behemoth", 1500, boss=True), enemy("Sand Spider", 660), enemy("Sand Spider 2", 660)]
    b = battle([orthrus, *pixies, reshuffle], foes, my=my)
    b.pips, b.power_pips = 2, 2
    b.upcoming = [_myth(10 + i, f"Card{i}", 50, 1) for i in range(8)]
    b.deck_known = True
    a = brain.decide(b, discards_left=2)
    assert a.kind is not ActionKind.DISCARD or not a.card.is_heal
    assert not (a.card is not None and a.card.name == "Reshuffle")


def test_never_heals_at_full_health():
    pixie = heal_card(0, "Pixie", 400)
    my = me(2221, 2221)
    my.school = "myth"
    foes = [enemy("Woodland Watcher", 660), enemy("Brownwood Tormentor", 3320, boss=True)]
    b = battle([pixie, blade_card(1)], foes, my=my)
    a = brain.decide(b)
    assert a.card is not pixie


def _spider_fight(hand, upcoming, pips=5):
    from wiz101_auto.combat.model import Card, Effect, EffectKind, Target

    my = me(2000, 2424)
    my.school = "myth"
    foes = [enemy("Sand Behemoth", 3000, boss=True), enemy("Sand Spider", 660), enemy("Sand Spider 2", 660)]
    trap = Card(20, "Myth Trap", pip_cost=0, school="myth",
                effects=[Effect(EffectKind.TRAP, Target.ENEMY_SINGLE, 30, school="myth")])
    b = battle([*hand, trap], foes, my=my)
    b.pips = pips
    b.upcoming, b.deck_known = upcoming, True
    return b, trap


def test_digs_for_the_orthrus_that_clears_the_adds_instead_of_trapping_them():
    # The player: Orthrus (~700) kills the 660 Sand Spiders by itself; the
    # bot trapped the spiders so Humongofrog would kill 2 of 3.
    frog = _myth(0, "Humongofrog", 600, 4, aoe=True)
    orthrus = _myth(30, "Orthrus", 700, 5, aoe=True)
    b, trap = _spider_fight([frog], [orthrus, *[blade_card(31 + i) for i in range(5)]])
    act = brain.decide(b)
    assert act.kind is ActionKind.DISCARD and act.card is frog


def test_with_orthrus_on_its_way_set_up_goes_on_the_boss():
    frog = _myth(0, "Humongofrog", 600, 4, aoe=True)
    orthrus = _myth(30, "Orthrus", 700, 5, aoe=True, castable=False)
    b, trap = _spider_fight([frog, orthrus], [blade_card(31 + i) for i in range(5)], pips=3)
    act = brain.decide(b)
    assert act.card is not frog
    if act.card is trap:
        assert act.target.is_boss


def test_medusa_stuns_the_last_enemy():
    from wiz101_auto.combat.brain import _stun_the_last_one
    from wiz101_auto.combat.model import (
        Action,
        ActionKind,
        Battle,
        Card,
        Combatant,
        Effect,
        EffectKind,
        Target,
    )

    medusa = Card(0, "Medusa", school="myth", pip_cost=5,
                  effects=[Effect(EffectKind.DAMAGE, Target.ENEMY_SINGLE, 500),
                           Effect(EffectKind.STUN, Target.ENEMY_SINGLE, 2)])
    blade = Card(1, "Mythblade", school="myth", pip_cost=0,
                 effects=[Effect(EffectKind.BLADE, Target.ALLY_SINGLE, 35)])
    me = Combatant("me", 2600, 2600, is_client=True, school="myth")
    boss = Combatant("Boss", 6000, 6000, is_enemy=True, is_boss=True, resist={})
    b = Battle(me=me, allies=[], enemies=[boss], cards=[medusa, blade], pips=1, power_pips=3)
    got = _stun_the_last_one(b, Action(ActionKind.CAST, blade, me))
    assert got is not None and got.card is medusa and got.target is boss
    # Two enemies left, or one already stunned: as chosen.
    add = Combatant("Add", 800, 800, is_enemy=True, resist={})
    assert _stun_the_last_one(Battle(me=me, allies=[], enemies=[boss, add], cards=[medusa, blade],
                                     pips=1, power_pips=3), Action(ActionKind.CAST, blade, me)) is None
    stunned = Combatant("Boss", 6000, 6000, is_enemy=True, is_boss=True, resist={}, is_stunned=True)
    assert _stun_the_last_one(Battle(me=me, allies=[], enemies=[stunned], cards=[medusa, blade],
                                     pips=1, power_pips=3), Action(ActionKind.CAST, blade, me)) is None


def test_a_sun_enchant_goes_on_the_chosen_hit_first():
    from wiz101_auto.combat.brain import decide
    from wiz101_auto.combat.model import (
        ActionKind,
        Battle,
        Card,
        Combatant,
        Effect,
        EffectKind,
        Target,
    )

    orthrus = Card(0, "Orthrus", school="myth", pip_cost=7,
                   effects=[Effect(EffectKind.DAMAGE, Target.ENEMY_ALL, 700)])
    giant = Card(1, "Giant", school="sun", pip_cost=0, treasure=True,
                 effects=[Effect(EffectKind.ENCHANT_DAMAGE, Target.SPELL, 125)])
    me = Combatant("me", 2600, 2600, is_client=True, school="myth")
    foes = [Combatant(f"Add{i}", 600, 600, is_enemy=True, resist={}) for i in range(2)]
    a = decide(Battle(me=me, allies=[], enemies=foes, cards=[orthrus, giant], pips=3, power_pips=2))
    assert a.kind is ActionKind.ENCHANT and a.card is giant and a.target_card is orthrus


def test_the_keeper_never_touches_treasure_cards():
    from wiz101_auto.deck_keeper import deck_changes

    current = {"Orthrus": 3, "Giant": 4}
    known = {"Orthrus"}
    target = {"Orthrus": 3}
    changes = {n: c for n, c in deck_changes(current, target).items() if n in known}
    assert changes == {}


def test_no_setup_when_the_next_round_could_kill_us():
    from wiz101_auto.combat.brain import _hit_all_setup
    from wiz101_auto.combat.model import ActionKind, Battle, Card, Combatant, Effect, EffectKind, Target

    orthrus = Card(0, "Orthrus", school="myth", pip_cost=7,
                   effects=[Effect(EffectKind.DAMAGE, Target.ENEMY_ALL, 700)])
    trap = Card(1, "Myth Trap", school="myth", pip_cost=0,
                effects=[Effect(EffectKind.TRAP, Target.ENEMY_SINGLE, 30, "myth")])
    foes = [Combatant(f"Never Seen {i}", 2190, 2190, is_enemy=True, resist={}) for i in range(4)]
    hurt = Combatant("me", 1196, 2552, is_client=True, school="myth")
    b = Battle(me=hurt, allies=[], enemies=foes, cards=[orthrus, trap], pips=1, power_pips=3)
    got = _hit_all_setup(b, orthrus)
    assert got is not None and got.kind is ActionKind.CAST and got.card is orthrus


def test_no_single_target_spells_at_luska():
    from wiz101_auto.combat.brain import decide
    from wiz101_auto.combat.model import ActionKind, Battle, Card, Combatant, Effect, EffectKind, Target

    bolt = Card(0, "Myth Bolt", school="myth", pip_cost=1,
                effects=[Effect(EffectKind.DAMAGE, Target.ENEMY_SINGLE, 900)])
    trap = Card(1, "Myth Trap", school="myth", pip_cost=0,
                effects=[Effect(EffectKind.TRAP, Target.ENEMY_SINGLE, 30, "myth")])
    me = Combatant("me", 2500, 2500, is_client=True, school="myth")
    luska = Combatant("Luska Charmbeak", 16920, 16920, is_enemy=True, is_boss=True, resist={})
    a = decide(Battle(me=me, allies=[], enemies=[luska], cards=[bolt, trap], pips=3, power_pips=0))
    assert not (a.kind is ActionKind.CAST and a.target is luska)


def test_giant_goes_on_orthrus_before_it_is_castable():
    from wiz101_auto.combat.brain import decide
    from wiz101_auto.combat.model import ActionKind, Battle, Card, Combatant, Effect, EffectKind, Target

    orthrus = Card(0, "Orthrus", school="myth", pip_cost=7, castable=False,
                   effects=[Effect(EffectKind.DAMAGE, Target.ENEMY_ALL, 700)])
    frog = Card(1, "Humongofrog", school="myth", pip_cost=4, castable=False,
                effects=[Effect(EffectKind.DAMAGE, Target.ENEMY_ALL, 400)])
    giant = Card(2, "Giant", school="sun", pip_cost=0,
                 effects=[Effect(EffectKind.ENCHANT_DAMAGE, Target.SPELL, 125)])
    me = Combatant("me", 2500, 2500, is_client=True, school="myth")
    foe = Combatant("Foe", 3000, 3000, is_enemy=True, resist={})
    a = decide(Battle(me=me, allies=[], enemies=[foe], cards=[frog, orthrus, giant], pips=1, power_pips=0))
    assert a.kind is ActionKind.ENCHANT and a.card is giant and a.target_card is orthrus
    a2 = decide(Battle(me=me, allies=[], enemies=[foe], cards=[frog, giant], pips=1, power_pips=0))
    assert a2.kind is ActionKind.ENCHANT and a2.target_card is frog


def test_sylster_cycles_and_what_each_allows():
    from wiz101_auto.combat.brain import _sylster_rules, sylster_cycle
    from wiz101_auto.combat.model import Battle, Card, Combatant, Effect, EffectKind, Target

    assert [sylster_cycle(r) for r in (1, 3, 4, 7, 8, 11, 12)] == [
        "light", "light", "dark", "dark", "light", "light", "dark"]
    blade = Card(0, "Mythblade", school="myth", pip_cost=0,
                 effects=[Effect(EffectKind.BLADE, Target.ALLY_SINGLE, 35, "myth")])
    trap = Card(1, "Myth Trap", school="myth", pip_cost=0,
                effects=[Effect(EffectKind.TRAP, Target.ENEMY_SINGLE, 30, "myth")])
    bolt = Card(2, "Myth Bolt", school="myth", pip_cost=1,
                effects=[Effect(EffectKind.DAMAGE, Target.ENEMY_SINGLE, 900)])
    orthrus = Card(3, "Orthrus", school="myth", pip_cost=7,
                   effects=[Effect(EffectKind.DAMAGE, Target.ENEMY_ALL, 700)])
    me = Combatant("me", 2500, 2500, is_client=True, school="myth")
    boss = Combatant("Sylster Glowstorm", 9000, 9000, is_enemy=True, is_boss=True, resist={})

    def ok(round_):
        b = _sylster_rules(Battle(me=me, allies=[], enemies=[boss], cards=[blade, trap, bolt, orthrus],
                                  pips=7, power_pips=0, round=round_))
        return {c.name for c in b.cards if c.castable}

    assert ok(2) == {"Myth Trap", "Orthrus"}   # light: traps, hit-alls; no blade, no bare single hit
    assert ok(5) == {"Mythblade", "Orthrus"}   # dark: blades, hit-alls; no trap, no unbladed single hit


def _setup(i, name, kind, n=1):
    from wiz101_auto.combat.model import Card, Effect, EffectKind, Target

    k = EffectKind.BLADE if kind == "blade" else EffectKind.TRAP
    t = Target.ALLY_SINGLE if kind == "blade" else Target.ENEMY_SINGLE
    return Card(i, name, pip_cost=0, school="myth", effects=[Effect(k, t, 35, school="myth")])


def test_no_hit_in_hand_digs_with_a_spare_blade():
    from wiz101_auto.combat.model import EffectKind

    blades = [_setup(0, "Mythblade", "blade"), _setup(1, "Mythblade", "blade")]
    my = me(600, 2600)
    my.school = "myth"
    my.outgoing_effects = []
    boss = enemy("Ildrede", 2300, boss=True)
    b = battle(blades, [boss], my=my)
    b.pips, b.power_pips = 2, 5
    b.upcoming = [_myth(5, "Orthrus", 1300, 7, aoe=True)]
    a = brain._dig_for_a_hit(b)
    assert a is not None and a.kind is ActionKind.DISCARD and EffectKind.BLADE in a.card.kinds


def test_set_up_enough_hits_instead_of_another_blade():
    from wiz101_auto.combat.model import Action

    orthrus = _myth(0, "Orthrus", 1300, 7, aoe=True)
    blade = _setup(1, "Mythblade", "blade")
    my = me(2000, 2600)
    my.school = "myth"
    my.blade_count = 2
    boss = enemy("Ildrede", 4000, boss=True)
    boss.trap_count = 2
    b = battle([orthrus, blade], [boss], my=my)
    b.pips, b.power_pips = 0, 4
    got = brain._hit_when_set_up(b, Action(ActionKind.CAST, blade, my))
    assert got is not None and got.card is orthrus
    my.blade_count = 1  # not set up yet: the blade as chosen
    assert brain._hit_when_set_up(b, Action(ActionKind.CAST, blade, my)) is None


def test_belloq_is_hit_every_round():
    from wiz101_auto.combat.model import Action

    orthrus = _myth(0, "Orthrus", 1300, 7, aoe=True, castable=False)
    bolt = _myth(1, "Myth Bolt", 300, 1)
    blade = _setup(2, "Mythblade", "blade")
    my = me(2000, 2700)
    my.school = "myth"
    belloq = enemy("Belloq", 5070, boss=True)
    merc = enemy("Greyhorn Mercenary", 1390)
    b = battle([orthrus, bolt, blade], [merc, belloq], my=my)
    b.pips, b.power_pips = 1, 0
    got = brain._keep_hitting(b, Action(ActionKind.CAST, blade, my))
    assert got is not None and got.card is bolt and got.target is belloq
    b2 = battle([orthrus, blade], [merc, belloq], my=my)  # nothing reaches him: as chosen
    assert brain._keep_hitting(b2, Action(ActionKind.CAST, blade, my)) is None


def test_zafaria_cheaters():
    from wiz101_auto.combat.brain import Strategy, _cheat_rules
    from wiz101_auto.combat.model import Action

    orthrus = _myth(0, "Orthrus", 1300, 7, aoe=True)
    bolt = _myth(1, "Myth Bolt", 300, 1)
    blade = _setup(2, "Mythblade", "blade")
    my = me(2500, 2700)
    my.school = "myth"
    # Nergal: no hit that leaves him alive; one that kills him goes.
    nergal = enemy("Nergal, the Burned Lion", 4000, boss=True)
    b = battle([bolt, blade], [nergal], my=my)
    held = _cheat_rules(b, Action(ActionKind.CAST, bolt, nergal), Strategy())
    assert held is not None and held.card is not bolt
    weak = enemy("Nergal, the Burned Lion", 100, boss=True)
    b1 = battle([bolt, blade], [weak], my=my)
    assert _cheat_rules(b1, Action(ActionKind.CAST, bolt, weak), Strategy()) is None
    # Shaka Zebu: his minion is never the target while he lives.
    shaka, minion = enemy("Shaka Zebu", 5000, boss=True), enemy("Carrion Flower Man", 1700)
    b2 = battle([bolt, blade], [shaka, minion], my=my)
    got = _cheat_rules(b2, Action(ActionKind.CAST, bolt, minion), Strategy())
    assert got is not None and got.target is not minion
    # The Spectral Elephant: the single hit goes to another guardian.
    eleph, rhino = enemy("Spectral Elephant", 3000), enemy("Spectral Rhino", 2000)
    b3 = battle([bolt, orthrus], [eleph, rhino], my=my)
    got = _cheat_rules(b3, Action(ActionKind.CAST, bolt, eleph), Strategy())
    assert got is not None and got.target is rhino


def test_last_stand_hits_instead_of_a_blade():
    from wiz101_auto.combat.model import Action

    orthrus = _myth(0, "Orthrus", 1300, 7, aoe=True)
    blade = _setup(1, "Spirit Blade", "blade")
    my = me(54, 2736)
    my.school = "myth"
    boss = enemy("Tim-tim Snakeeye", 5663, boss=True)
    b = battle([orthrus, blade], [boss], my=my)
    b.pips, b.power_pips = 1, 3
    got = brain._last_stand(b, Action(ActionKind.CAST, blade, my))
    assert got is not None and got.card is orthrus
    my.health = 2700  # healthy: the blade as chosen
    assert brain._last_stand(b, Action(ActionKind.CAST, blade, my)) is None


def test_an_untouched_boss_is_hit_by_round_four():
    from wiz101_auto.combat.model import Action

    orthrus = _myth(0, "Orthrus", 1300, 7, aoe=True)
    blade = _setup(1, "Spirit Blade", "blade")
    my = me(1800, 2736)
    my.school = "myth"
    my.blade_count = 1
    boss = enemy("Tim-tim Snakeeye", 5675, boss=True)
    b = battle([orthrus, blade], [boss], my=my)
    b.pips, b.power_pips = 1, 3
    b.round = 2
    assert brain._hit_when_set_up(b, Action(ActionKind.CAST, blade, my)) is None  # early: set up
    b.round = 4
    got = brain._hit_when_set_up(b, Action(ActionKind.CAST, blade, my))
    assert got is not None and got.card is orthrus


def test_tim_tim_heal_trick():
    from wiz101_auto.combat.model import Action, Card, Effect, EffectKind, Target

    orthrus = _myth(0, "Orthrus", 1300, 7, aoe=True)
    blade = _setup(1, "Mythblade", "blade")
    pixie = Card(2, "Pixie", school="myth", pip_cost=2, effects=[Effect(EffectKind.HEAL, Target.SELF, 400)])
    my = me(2000, 2736)
    my.school = "myth"
    my.blade_count = 2
    tim = enemy("Tim-tim Snakeeye", 5675, boss=True)
    tim.incoming_effects = [("shield", "", -0.9)]
    b = battle([orthrus, blade, pixie], [tim], my=my)
    b.pips, b.power_pips = 2, 3
    got = brain._heal_trick(b, Action(ActionKind.CAST, orthrus, None))
    assert got is not None and got.card is pixie  # shielded: the heal, not Orthrus into -90%
    tim.incoming_effects = []
    got = brain._heal_trick(b, Action(ActionKind.CAST, blade, my))
    assert got is not None and got.card is orthrus  # bare: the big hit now


def test_tim_tim_heals_only_with_the_hit_affordable_next_round():
    from wiz101_auto.combat.model import Action, Card, Effect, EffectKind, Target

    orthrus = _myth(0, "Orthrus", 1300, 7, aoe=True, castable=False)
    pixie = Card(2, "Pixie", school="myth", pip_cost=2, effects=[Effect(EffectKind.HEAL, Target.SELF, 400)])
    blade = _setup(1, "Mythblade", "blade")
    my = me(2000, 2736)
    my.school = "myth"
    my.blade_count = 2
    tim = enemy("Tim-tim Snakeeye", 5675, boss=True)
    tim.incoming_effects = [("shield", "", -0.9)]
    b = battle([orthrus, pixie, blade], [tim], my=my)
    b.pips, b.power_pips = 0, 1  # 2 pips: the heal leaves 0, +1 next round: no Orthrus
    got = brain._heal_trick(b, Action(ActionKind.PASS))
    assert got is None or got.card is not pixie
    b.pips, b.power_pips = 0, 4  # 8: the heal leaves 6, +1: Orthrus next round
    got = brain._heal_trick(b, Action(ActionKind.PASS))
    assert got is not None and got.card is pixie


def test_blades_from_different_spells_stack():
    from wiz101_auto.combat.brain import _is_duplicate
    from wiz101_auto.combat.model import Card, Effect, EffectKind, Target

    def mythblade(tid):
        return Card(0, "Mythblade", school="myth", template_id=tid,
                    effects=[Effect(EffectKind.BLADE, Target.ALLY_SINGLE, 35, school="myth")])

    up = [("spell:111", "myth", 0.35)]  # the trained Mythblade hanging
    assert _is_duplicate(mythblade(111), EffectKind.BLADE, up, "myth")       # its copy: no
    assert not _is_duplicate(mythblade(222), EffectKind.BLADE, up, "myth")   # the pet's: stacks


def test_the_plan_preview_enchants_once():
    from wiz101_auto.combat.model import Action, Card, Effect, EffectKind, Target
    from wiz101_auto.combat.sim import plan_preview

    orthrus = _myth(0, "Orthrus", 700, 4, aoe=True)
    garg = Card(1, "Gargantuan", school="sun", pip_cost=0, treasure=True,
                effects=[Effect(EffectKind.ENCHANT_DAMAGE, Target.SPELL, 225)])
    my = me(2500, 2700)
    my.school = "myth"
    b = battle([orthrus, garg], [enemy("Ogun Daggertooth", 1850, boss=True)], my=my)
    b.pips, b.power_pips = 0, 2
    first = Action(ActionKind.ENCHANT, garg, target_card=orthrus)
    plan = plan_preview(b, first, None)
    spells = [s["spell"] for s in plan["steps"]]
    assert spells.count("Gargantuan") == 1 and "Orthrus" in spells


def test_a_second_copy_of_feint_doesnt_stack_in_the_sim():
    from wiz101_auto.combat.brain import _is_duplicate
    from wiz101_auto.combat.model import Card, Effect, EffectKind, Target
    from wiz101_auto.combat.sim import _fx_key

    def feint(tid):
        return Card(0, "Feint", school="death", template_id=tid,
                    effects=[Effect(EffectKind.TRAP, Target.ENEMY_SINGLE, 70)])

    up = [(_fx_key(feint(10)), "", 0.7), ("spell:999", "storm", -0.2)]  # + a game effect of the boss's own
    assert _is_duplicate(feint(10), EffectKind.TRAP, up, "myth")      # the trained Feint again: no
    assert not _is_duplicate(feint(20), EffectKind.TRAP, up, "myth")  # the necklace's: stacks


def test_traps_first_when_they_make_the_hit_kill_the_boss():
    from wiz101_auto.combat.model import Action, Card, Effect, EffectKind, Target

    orthrus = _myth(0, "Orthrus", 2200, 7, aoe=True)  # x1.7 x1.7 with two Feints: kills 5910
    feint = Card(1, "Feint", school="death", template_id=10,
                 effects=[Effect(EffectKind.TRAP, Target.ENEMY_SINGLE, 70)])
    feint2 = Card(2, "Feint", school="death", template_id=20,
                  effects=[Effect(EffectKind.TRAP, Target.ENEMY_SINGLE, 70)])
    blade = _setup(3, "Mythblade", "blade")
    my = me(2500, 3000)
    my.school = "myth"
    my.blade_count = 2
    boss = enemy("Kallah Silverback", 5910, boss=True)
    witch = enemy("Gorilla Spider Witch", 2110)
    b = battle([orthrus, feint, feint2, blade], [boss, witch], my=my)
    b.pips, b.power_pips, b.round = 1, 3, 4
    import unittest.mock as um
    with um.patch.object(brain, "incoming_per_round", return_value=300.0):
        assert brain._hit_when_set_up(b, Action(ActionKind.CAST, feint, boss)) is None  # Feints first
    b2 = battle([orthrus, blade], [boss, witch], my=my)  # no Feint in hand: hit now
    b2.pips, b2.power_pips, b2.round = 1, 3, 4
    with um.patch.object(brain, "incoming_per_round", return_value=300.0):
        got = brain._hit_when_set_up(b2, Action(ActionKind.CAST, blade, my))
    assert got is not None and got.card is orthrus


def test_the_bigger_hit_is_cast_when_castable_not_saved_for():
    frog = _myth(0, "Humongofrog", 600, 4, aoe=True)
    orthrus = _myth(1, "Orthrus", 1500, 7, aoe=True)
    my = me(478, 3019)
    my.school = "myth"
    b = battle([frog, orthrus], [enemy("Kallah Silverback", 2035, boss=True)], my=my)
    b.pips, b.power_pips = 0, 7
    a = brain.decide(b, discards_left=0)
    assert a.kind is ActionKind.CAST and a.card is orthrus


def test_feint_on_a_tough_boss_is_paid_with_a_power_pip():
    from wiz101_auto.combat.brain import Strategy, _pip_wise_setup
    from wiz101_auto.combat.model import Action, Card, Effect, EffectKind, Target

    orthrus = _myth(0, "Orthrus", 1500, 7, aoe=True)
    feint = Card(1, "Feint", school="death", pip_cost=1, template_id=10,
                 effects=[Effect(EffectKind.TRAP, Target.ENEMY_SINGLE, 70)])
    my = me(2500, 3000)
    my.school = "myth"
    boss = enemy("Kallah Silverback", 5910, boss=True)
    b = battle([orthrus, feint], [boss], my=my)
    b.pips, b.power_pips = 0, 2
    import unittest.mock as um
    with um.patch.object(brain, "incoming_per_round", return_value=300.0):
        # 1500 x1.7 = 2550 short of 5910: no kill yet, but no pass either: Feint now
        assert _pip_wise_setup(b, Action(ActionKind.CAST, feint, boss), Strategy()) is None
        boss.health = 2400  # one Feint and Orthrus kill him: Feint now, on a power pip
        assert _pip_wise_setup(b, Action(ActionKind.CAST, feint, boss), Strategy()) is None


def test_the_planner_finds_feints_into_orthrus():
    from wiz101_auto.combat.brain import plan_hand_use
    from wiz101_auto.combat.model import Card, Effect, EffectKind, Target

    orthrus = _myth(0, "Orthrus", 2200, 7, aoe=True, castable=False)
    feint = Card(1, "Feint", school="death", pip_cost=1, template_id=10,
                 effects=[Effect(EffectKind.TRAP, Target.ENEMY_SINGLE, 70)])
    feint2 = Card(2, "Feint", school="death", pip_cost=1, template_id=20,
                  effects=[Effect(EffectKind.TRAP, Target.ENEMY_SINGLE, 70)])
    my = me(3000, 3000)
    my.school = "myth"
    boss = enemy("Kallah Silverback", 5910, boss=True)
    b = battle([orthrus, feint, feint2], [boss], my=my)
    b.pips, b.power_pips = 0, 4  # Orthrus alone (2200) won't kill; with two Feints (x2.89) it does
    rounds, used = plan_hand_use(b)
    assert rounds < 99 and {1, 2} <= used


def test_three_different_feints_all_plan_in():
    from wiz101_auto.combat.brain import plan_hand_use
    from wiz101_auto.combat.model import Card, Effect, EffectKind, Target

    def feint(i, tid, item=False):
        return Card(i, "Feint", school="death", pip_cost=1, template_id=tid, item=item,
                    effects=[Effect(EffectKind.TRAP, Target.ENEMY_SINGLE, 70)])

    orthrus = _myth(0, "Orthrus", 1500, 7, aoe=True, castable=False)
    my = me(3000, 3000)
    my.school = "myth"
    boss = enemy("Big Boss", 7000, boss=True)  # 1500 x1.7^3 = 7370: only all three Feints kill
    b = battle([orthrus, feint(1, 10), feint(2, 20, item=True), feint(3, 30, item=True)], [boss], my=my)
    b.pips, b.power_pips = 0, 5
    rounds, used = plan_hand_use(b)
    assert rounds < 99 and {1, 2, 3} <= used


def test_a_hit_the_plan_outclasses_and_a_costly_blade_are_spare():
    from wiz101_auto.combat.brain import covered_by_plan
    from wiz101_auto.combat.model import Card, Effect, EffectKind, Target

    orthrus = _myth(0, "Orthrus", 1500, 7, aoe=True)
    frog = _myth(1, "Humongofrog", 1100, 5, aoe=True)
    spirit = Card(2, "Spirit Blade", school="balance", pip_cost=1, template_id=50,
                  effects=[Effect(EffectKind.BLADE, Target.ALLY_SINGLE, 25, school="myth")])
    feint = Card(3, "Feint", school="death", pip_cost=1, template_id=10,
                 effects=[Effect(EffectKind.TRAP, Target.ENEMY_SINGLE, 70)])
    my = me(3000, 3000)
    my.school = "myth"
    my.outgoing_effects = [("spell:30", "myth", 0.35)]  # a Mythblade up
    b = battle([orthrus, frog, spirit, feint], [enemy("Kallah Silverback", 5000, boss=True)], my=my)
    assert covered_by_plan(b, frog, {0})  # Orthrus is the plan: Humongofrog can go
    assert not covered_by_plan(b, frog, {3})  # no hit in the plan: it stays
    assert covered_by_plan(b, spirit, {0})  # a Mythblade's up: Spirit Blade can go
    assert not covered_by_plan(b, feint, {0})  # traps stay


def test_hit_all_setup_blades_before_trapping_one_enemy():
    # 4 Shadow-Web Haunts, all set-up cards 0 pips: Mythblade (boosts the hit
    # on all four) goes before Myth Trap / Feint (one enemy each).
    from wiz101_auto.combat.brain import _hit_all_setup
    from wiz101_auto.combat.model import ActionKind, Battle, Card, Combatant, Effect, EffectKind, Target

    frog = Card(0, "Humongofrog", school="myth", pip_cost=5,
                effects=[Effect(EffectKind.DAMAGE, Target.ENEMY_ALL, 1300)])
    trap = Card(1, "Myth Trap", school="myth", pip_cost=0,
                effects=[Effect(EffectKind.TRAP, Target.ENEMY_SINGLE, 40, "myth")])
    feint = Card(2, "Feint", school="death", pip_cost=0,
                 effects=[Effect(EffectKind.TRAP, Target.ENEMY_SINGLE, 70)])
    blade = Card(3, "Mythblade", school="myth", pip_cost=0,
                 effects=[Effect(EffectKind.BLADE, Target.ALLY_SINGLE, 35, "myth")])
    foes = [Combatant(f"Never Seen {i}", 2325, 2325, is_enemy=True, resist={}) for i in range(4)]
    me = Combatant("me", 3201, 3201, is_client=True, school="myth")
    b = Battle(me=me, allies=[], enemies=foes, cards=[frog, feint, trap, blade], pips=0, power_pips=2)
    got = _hit_all_setup(b, frog)
    assert got is not None and got.kind is ActionKind.CAST and got.card is blade, got


def test_the_stronger_copy_of_a_spell_is_played_and_the_weaker_discarded():
    # The Amulet's Mythblade gives +40%, the trained one +35%.
    from wiz101_auto.combat.brain import decide, strongest_copies_first
    from wiz101_auto.combat.model import ActionKind, Battle, Card, Combatant, Effect, EffectKind, Target

    def blade(i, v):
        return Card(i, "Mythblade", school="myth", pip_cost=0,
                    effects=[Effect(EffectKind.BLADE, Target.ALLY_SINGLE, v, "myth")])

    weak, strong = blade(0, 35), blade(1, 40)
    orthrus = Card(2, "Orthrus", school="myth", pip_cost=7,
                   effects=[Effect(EffectKind.DAMAGE, Target.ENEMY_ALL, 700)])
    assert strongest_copies_first([weak, orthrus, strong]) == [strong, orthrus, weak]
    me = Combatant("me", 3000, 3000, is_client=True, school="myth")
    foe = Combatant("Never Seen", 5000, 5000, is_enemy=True, resist={})
    a = decide(Battle(me=me, allies=[], enemies=[foe], cards=[weak, orthrus, strong], pips=1, power_pips=0))
    if a.kind is ActionKind.CAST and a.card is not None and a.card.name == "Mythblade":
        assert a.card is strong
    elif a.kind is ActionKind.DISCARD and a.card is not None and a.card.name == "Mythblade":
        assert a.card is weak
    else:
        raise AssertionError(a)


def _luska_hand():
    from wiz101_auto.combat.model import Card, Effect, EffectKind, Target

    def blade(i, name, v, school="myth"):
        return Card(i, name, school="balance" if name == "Spirit Blade" else "myth", pip_cost=0,
                    effects=[Effect(EffectKind.BLADE, Target.ALLY_SINGLE, v, school)])

    feint = Card(0, "Feint", school="death", pip_cost=0,
                 effects=[Effect(EffectKind.TRAP, Target.ENEMY_SINGLE, 70),
                          Effect(EffectKind.TRAP, Target.ALLY_SINGLE, 30)])
    trap = Card(1, "Myth Trap", school="myth", pip_cost=0,
                effects=[Effect(EffectKind.TRAP, Target.ENEMY_SINGLE, 40, "myth")])
    orthrus = Card(2, "Orthrus", school="myth", pip_cost=7,
                   effects=[Effect(EffectKind.DAMAGE, Target.ENEMY_ALL, 700)])
    blades = blade(3, "Mythblade", 35), blade(4, "Mythblade", 40), blade(5, "Spirit Blade", 25)
    return (feint, trap, orthrus, *blades)


def _luska_battle(cards, pips=0, power=5):
    from wiz101_auto.combat.model import Battle, Combatant

    me = Combatant("me", 3223, 3223, is_client=True, school="myth")
    luska = Combatant("Luska Charmbeak", 16920, 16920, is_enemy=True, is_boss=True, resist={})
    return Battle(me=me, allies=[], enemies=[luska], cards=list(cards), pips=pips, power_pips=power)


def test_luska_blades_first_then_spirit_blade_then_orthrus_never_a_trap():
    from wiz101_auto.combat.brain import decide
    from wiz101_auto.combat.model import ActionKind

    feint, trap, orthrus, mb35, mb40, spirit = _luska_hand()
    a = decide(_luska_battle([feint, trap, orthrus, mb35, mb40, spirit]))
    assert a.kind is ActionKind.CAST and a.card is mb40  # the strongest Mythblade first
    a = decide(_luska_battle([feint, trap, orthrus, spirit]))
    assert a.kind is ActionKind.CAST and a.card is spirit
    a = decide(_luska_battle([feint, trap, orthrus]))
    assert a.kind is ActionKind.CAST and a.card is orthrus
    a = decide(_luska_battle([feint, trap], pips=3, power=0))
    assert not (a.kind is ActionKind.CAST and a.card in (feint, trap))


def test_the_luska_guard_turns_any_single_target_cast_into_a_pass():
    from wiz101_auto.combat.brain import luska_guard
    from wiz101_auto.combat.model import Action, ActionKind

    feint, trap, orthrus, *_ = _luska_hand()
    b = _luska_battle([feint, trap, orthrus])
    luska = b.enemies[0]
    assert luska_guard(b, Action(ActionKind.CAST, feint, luska)).kind is ActionKind.PASS
    assert luska_guard(b, Action(ActionKind.CAST, trap, luska)).kind is ActionKind.PASS
    assert luska_guard(b, Action(ActionKind.CAST, orthrus, None)).card is orthrus


def test_avalon_boss_bans():
    from wiz101_auto.combat.brain import _no_single_target
    from wiz101_auto.combat.model import Battle, Card, Combatant, Effect, EffectKind, Target

    trap = Card(0, "Myth Trap", school="myth", pip_cost=0,
                effects=[Effect(EffectKind.TRAP, Target.ENEMY_SINGLE, 40, "myth")])
    blade = Card(1, "Mythblade", school="myth", pip_cost=0,
                 effects=[Effect(EffectKind.BLADE, Target.ALLY_SINGLE, 35, "myth")])
    orthrus = Card(2, "Orthrus", school="myth", pip_cost=7,
                   effects=[Effect(EffectKind.DAMAGE, Target.ENEMY_ALL, 700)])
    me = Combatant("me", 3000, 3000, is_client=True, school="myth")

    def castable(boss):
        foe = Combatant(boss, 12000, 12000, is_enemy=True, is_boss=True, resist={})
        b = _no_single_target(Battle(me=me, allies=[], enemies=[foe], cards=[trap, blade, orthrus],
                                     pips=7, power_pips=0))
        return {c.name for c in b.cards if c.castable}

    assert castable("Matkis Axethief") == {"Mythblade", "Orthrus"}
    assert castable("Flevur Flave") == {"Myth Trap", "Orthrus"}
    assert castable("Young Morganthe") == {"Mythblade", "Orthrus"}
    assert castable("Black Annie") == {"Mythblade", "Orthrus"}
    assert castable("The Pendragon") == {"Mythblade", "Orthrus"}


def test_poseidon_and_the_sand_squid():
    from wiz101_auto.combat.brain import _no_single_target
    from wiz101_auto.combat.model import Battle, Card, Combatant, Effect, EffectKind, Target

    trap = Card(0, "Myth Trap", school="myth", pip_cost=0,
                effects=[Effect(EffectKind.TRAP, Target.ENEMY_SINGLE, 40, "myth")])
    bolt = Card(1, "Minotaur", school="myth", pip_cost=5,
                effects=[Effect(EffectKind.DAMAGE, Target.ENEMY_SINGLE, 600)])
    efreet = Card(2, "Efreet", school="fire", pip_cost=6,
                  effects=[Effect(EffectKind.DAMAGE, Target.ENEMY_SINGLE, 600),
                           Effect(EffectKind.WEAKNESS, Target.ENEMY_SINGLE, 45)])
    heal = Card(3, "Pixie", school="life", pip_cost=2,
                effects=[Effect(EffectKind.HEAL, Target.ALLY_SINGLE, 600)])
    me = Combatant("me", 3000, 3000, is_client=True, school="myth")

    def castable(boss):
        foe = Combatant(boss, 20000, 20000, is_enemy=True, is_boss=True, resist={})
        b = _no_single_target(Battle(me=me, allies=[], enemies=[foe], cards=[trap, bolt, efreet, heal],
                                     pips=7, power_pips=0))
        return {c.name for c in b.cards if c.castable}

    assert castable("Poseidon Earth-Shaker") == {"Minotaur", "Pixie"}
    assert castable("Sand Squid Tentacle") == {"Myth Trap", "Minotaur", "Efreet"}


def test_digs_for_the_prism_past_extra_copies_of_a_big_hit():
    # Porrich: 80% myth resist, weak to storm; three Basilisks in hand, three
    # Myth Prisms still in the deck.
    from wiz101_auto.combat.brain import Strategy, _dig_for_setup
    from wiz101_auto.combat.model import ActionKind, Battle, Card, Combatant, Effect, EffectKind, Target

    def basilisk(i):
        return Card(i, "Basilisk", school="myth", pip_cost=6,
                    effects=[Effect(EffectKind.DAMAGE, Target.ENEMY_SINGLE, 800)])

    prism = Card(9, "Myth Prism", school="myth", pip_cost=1,
                 effects=[Effect(EffectKind.OTHER, Target.ENEMY_SINGLE, 0)])
    hand = [basilisk(0), basilisk(1), basilisk(2)]
    me = Combatant("me", 3197, 3197, is_client=True, school="myth")
    porrich = Combatant("Porrich", 8240, 8240, is_enemy=True, is_boss=True, school="myth",
                        resist={"myth": 0.8, "storm": -0.35, "death": -0.35})
    b = Battle(me=me, allies=[], enemies=[porrich], cards=hand, pips=0, power_pips=2,
               upcoming=[prism, prism, prism])
    got = _dig_for_setup(b, Strategy())
    assert got is not None and got.kind is ActionKind.DISCARD and got.card.name == "Basilisk", got


def test_a_second_prism_is_junk_once_the_boss_is_prismed():
    from wiz101_auto.combat.brain import _prism_useless
    from wiz101_auto.combat.model import Battle, Card, Combatant, Effect, EffectKind, Target

    prism = Card(0, "Myth Prism", school="myth", pip_cost=1,
                 effects=[Effect(EffectKind.OTHER, Target.ENEMY_SINGLE, 0)])
    hit = Card(1, "Orthrus", school="myth", pip_cost=7,
               effects=[Effect(EffectKind.DAMAGE, Target.ENEMY_ALL, 900)])
    me = Combatant("me", 3197, 3197, is_client=True, school="myth")
    porrich = Combatant("Porrich", 8240, 8240, is_enemy=True, is_boss=True, school="myth",
                        resist={"myth": 0.8, "storm": -0.35, "death": -0.35})
    prism2 = Card(2, "Myth Prism", school="myth", pip_cost=1,
                  effects=[Effect(EffectKind.OTHER, Target.ENEMY_SINGLE, 0)])
    b = Battle(me=me, allies=[], enemies=[porrich], cards=[prism, prism2, hit], pips=0, power_pips=2)
    assert not _prism_useless(prism, b)
    b.prismed = {"Porrich"}
    assert _prism_useless(prism, b)  # one waits on him, one more in hand: this one goes
    b.cards = [prism, hit]
    assert not _prism_useless(prism, b)  # the last one stays for after the hit


def test_a_banned_card_is_never_planned_in_a_later_round():
    # Matkis Axethief: no single-target traps on him. The search checked
    # castability for this round only, so it planned "pass > Feint > Feint >
    # Orthrus" and the brain passed four rounds waiting for those Feints.
    from wiz101_auto.combat.brain import _kill_search, _no_single_target
    from wiz101_auto.combat.model import Battle, Card, Combatant, Effect, EffectKind, Target

    orthrus = Card(0, "Orthrus", school="myth", pip_cost=7,
                   effects=[Effect(EffectKind.DAMAGE, Target.ENEMY_ALL, 3000)])
    feints = [Card(i, "Feint", school="death", pip_cost=1, template_id=10 + i,
                   effects=[Effect(EffectKind.TRAP, Target.ENEMY_SINGLE, 70)]) for i in (1, 2)]
    me = Combatant("me", 1000, 3241, is_client=True, school="myth")
    matkis = Combatant("Matkis Axethief", 6933, 9040, is_enemy=True, is_boss=True, resist={})
    b = _no_single_target(Battle(me=me, allies=[], enemies=[matkis], cards=[orthrus, *feints],
                                 pips=0, power_pips=3))
    found = _kill_search(b, b.enemies[0], 10)
    assert found is None or "Feint" not in " ".join(found[3])


def test_never_waits_for_a_trap_the_boss_forbids():
    # Black Annie: no single-target spell of 3 pips or less (Feint is one).
    # The bot passed "holding Humongofrog until Black Annie is trapped".
    from wiz101_auto.combat.brain import _no_single_target, _trap_coming
    from wiz101_auto.combat.model import Battle, Card, Combatant, Effect, EffectKind, Target

    frog = Card(0, "Humongofrog", school="myth", pip_cost=4,
                effects=[Effect(EffectKind.DAMAGE, Target.ENEMY_SINGLE, 700)])
    feint = Card(1, "Feint", school="death", pip_cost=1,
                 effects=[Effect(EffectKind.TRAP, Target.ENEMY_SINGLE, 70)])
    me = Combatant("me", 613, 3307, is_client=True, school="myth")
    annie = Combatant("Black Annie", 7446, 11400, is_enemy=True, is_boss=True, resist={})
    b = _no_single_target(Battle(me=me, allies=[], enemies=[annie], cards=[frog, feint],
                                 pips=1, power_pips=2))
    assert not _trap_coming(b)


def test_no_feint_when_the_hit_all_already_kills_everyone():
    # Three Dog Knights (2570 each), Orthrus already ~2714 into each: a Feint
    # (off school: a power pip) on one of them only delays the Orthrus.
    from wiz101_auto.combat.brain import decide
    from wiz101_auto.combat.model import ActionKind, Battle, Card, Combatant, Effect, EffectKind, Target

    orthrus = Card(0, "Orthrus", school="myth", pip_cost=7, castable=False,
                   effects=[Effect(EffectKind.DAMAGE, Target.ENEMY_ALL, 3000)])
    feint = Card(1, "Feint", school="death", pip_cost=1,
                 effects=[Effect(EffectKind.TRAP, Target.ENEMY_SINGLE, 70)])
    me = Combatant("me", 3329, 3329, is_client=True, school="myth")
    dogs = [Combatant("Dog Knight", 2570, 2570, is_enemy=True, resist={}) for _ in range(3)]
    b = Battle(me=me, allies=[], enemies=dogs, cards=[orthrus, feint], pips=0, power_pips=2)
    a = decide(b)
    assert not (a.kind is ActionKind.CAST and a.card is feint), a
