from wiz101_auto.combat.brain import decide
from wiz101_auto.combat.model import ActionKind, Battle, Card, Combatant


def me():
    return Combatant("Me", 10000, 10000, is_client=True, school="myth")


def boss():
    return Combatant("The Headmaster", 9040, 9040, is_enemy=True, is_boss=True)


def lesson(names):
    cards = [Card(i, n, pip_cost=0, castable=False) for i, n in enumerate(names)]
    return Battle(me=me(), allies=[], enemies=[boss()], cards=cards, pips=4)


def test_our_school_is_picked():
    schools = ["Storm School", "Ice School", "Fire School", "Death School", "Life School", "Myth School"]
    a = decide(lesson(schools))
    assert a.kind is ActionKind.CAST and a.card.name == "Myth School"


def test_attendance_and_homework_played():
    assert decide(lesson(["Attendance", "Attendance"])).card.name == "Attendance"
    assert decide(lesson(["Homework"] * 7)).card.name == "Homework"


def test_class_card_never_snack_break():
    a = decide(lesson(["Listen", "Snack Break", "Ask Questions", "Participate", "Take Notes"]))
    assert a.card.name == "Participate"
