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


def test_main_story_world_fetches_its_next_quest_alone():
    from wiz101_auto.main_guide import who_to_ask

    g = parse("""23. Tide and Tile - Search Shark Hut + Talk to Thornton Lewis
24. Turning Tiles - Use Celestian Mosaic + Talk to Thornton Lewis
25. Archivist, Revisited - Talk to The Archivist
""")
    done = {"Tide and Tile", "Turning Tiles"}
    assert next_to_pick_up(g, done, {"Land Sharks"}) is None  # (nothing waiting)
    nxt = next_to_pick_up(g, done, {"Land Sharks"}, alone=True)
    assert nxt is not None and nxt.index == 25
    assert who_to_ask(g, nxt) == ["Thornton Lewis", "The Archivist"]


def test_story_world_after_a_detour():
    from wiz101_auto.questlist import parse_quest_list, story_world

    lists = {"Celestia": parse_quest_list("Survey Camp (1 quests)\n1.\nDoor to the Stars\nTALK\n"),
             "Wintertusk": parse_quest_list("Northguard (1 quests)\n1.\nCold News\nTALK\n")}
    assert story_world(["Door to the Stars", "Cold News"], lists) == "Celestia"
    assert story_world([], lists) == ""


def test_guide_names_match_despite_articles_and_typos():
    from wiz101_auto.main_guide import same_name

    assert same_name("A Old Sea Chantry", "An Old Sea Chantry")
    assert same_name("King's Fourth", "Kings Forth")
    assert not same_name("Storm Shards", "Kraken Up")
    g = parse("""51. Storm Shards - Collect Artifacts
52. Kraken Up - Defeat Tempus Stormfist + Talk to Leland Hawkins
53. A Old Sea Chantry - Talk to The Archivist
""")
    # Kraken Up handed in, not logged yet; the next one is in the book already.
    assert next_to_pick_up(g, {"Storm Shards"}, {"An Old Sea Chantry"}, alone=True) is None
