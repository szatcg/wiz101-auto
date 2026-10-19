from wiz101_auto.combat.brain import decide
from wiz101_auto.combat.model import ActionKind, Battle, Card, Combatant


def me():
    return Combatant("Me", 10000, 10000, is_client=True, school="myth")


def boss():
    ward = [("w", sch, -9.99) for sch in ("balance", "death", "life", "myth", "storm", "ice")]
    return Combatant("The Headmaster", 9040, 9040, is_enemy=True, incoming_effects=ward)


def lesson(names):
    cards = [Card(i, n, pip_cost=0, castable=False) for i, n in enumerate(names)]
    return Battle(me=me(), allies=[], enemies=[boss()], cards=cards, pips=4)


def test_open_school_is_picked():
    schools = ["Storm School", "Ice School", "Fire School", "Death School", "Life School", "Myth School"]
    a = decide(lesson(schools))
    assert a.kind is ActionKind.CAST and a.card.name == "Fire School"


def test_attendance_and_homework_played():
    assert decide(lesson(["Attendance", "Attendance"])).kind is ActionKind.PASS
    assert decide(lesson(["Homework"] * 7)).card.name == "Homework"


def test_class_card_never_snack_break():
    a = decide(lesson(["Listen", "Snack Break", "Ask Questions", "Participate", "Take Notes"]))
    assert a.card.name == "Participate"


def test_ultra_assail_never_played():
    from wiz101_auto.combat.model import Effect, EffectKind, Target, normalize_card

    ua = Card(0, "Ultra Assail", pip_cost=0, effects=[Effect(EffectKind.DAMAGE, Target.ENEMY_SINGLE, 300)])
    assert normalize_card(ua).banned


def test_empyrea_boss_bans():
    from wiz101_auto.combat.brain import BOSS_BANS
    from wiz101_auto.combat.model import Effect, EffectKind, Target

    blade = Card(1, "Mythblade", pip_cost=0, effects=[Effect(EffectKind.BLADE, Target.ALLY_SINGLE, 35)])
    trap = Card(2, "Feint", pip_cost=1, effects=[Effect(EffectKind.TRAP, Target.ENEMY_SINGLE, 70)])
    assert BOSS_BANS["ghost dog"](blade) and not BOSS_BANS["ghost dog"](trap)
    assert BOSS_BANS["medulla"](trap) and not BOSS_BANS["medulla"](blade)
