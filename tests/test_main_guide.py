from wiz101_auto.main_guide import giver, later_in_book, load, next_to_pick_up, parse

GUIDE = """Nastrond
44. Bones of the Earth - Talk to Grandmother Raven; Enter the Nastrond Dungeon
45. Take the Low Road - Talk to Dulin Helmsplitter + Defeat Winter Skulls + Talk to Dulin Helmsplitter
46. Hammer Don't Hurt 'Em - Talk to Dulin Helmsplitter + Use Dulin's Hammer + Talk to Truda Stoutheart
47. Bridge to Nowhere - Talk to Truda Stoutheart + Cross Ice Bridge + Talk to Hauk Horncaller
1. Cold News (given by Headmaster Ambrose) - Talk to Bjorn Ironclaws in Northguard
"""


def test_parse_and_givers():
    g = parse(GUIDE)
    assert [q.index for q in g] == [44, 45, 46, 47, 1]
    hammer = g[2]
    assert hammer.name == "Hammer Don't Hurt 'Em"
    assert giver(g, hammer) == "Dulin Helmsplitter"
    assert giver(g, g[3]) == "Truda Stoutheart"
    assert giver(g, g[4]) == "Ambrose"


def test_umbrella_quest_waits_on_the_next_one():
    g = parse(GUIDE)[:4]
    # Bones of the Earth open, Take the Low Road done: Hammer is next, from Dulin.
    nxt = next_to_pick_up(g, done={"Take the Low Road"}, book={"Bones of the Earth"})
    assert nxt is not None and nxt.index == 46
    # Picked up already, or no earlier quest waiting: nothing to fetch.
    assert next_to_pick_up(g, {"Take the Low Road"}, {"Bones of the Earth", "Hammer Don't Hurt 'Em"}) is None
    assert next_to_pick_up(g, {"Take the Low Road"}, set()) is None


def test_later_story_quest_first():
    g = parse(GUIDE)
    book = ["Bones of the Earth", "Hammer Don't Hurt 'Em", "The Spiral Cup"]
    assert later_in_book(g, "Bones of the Earth", book) == "Hammer Don't Hurt 'Em"
    assert later_in_book(g, "Hammer Don't Hurt 'Em", book) is None
    assert later_in_book(g, "The Spiral Cup", book) is None


def test_the_real_wintertusk_guide():
    g = load("Wintertusk")
    nxt = next_to_pick_up(g, {"Take the Low Road"}, {"Bones of the Earth"})
    assert nxt is not None and nxt.index == 46 and giver(g, nxt) == "Dulin Helmsplitter"
