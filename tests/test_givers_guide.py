from wiz101_auto.givers import parse_guide, pending_givers, same_quest

GUIDE = """REGENT'S SQUARE
(MAIN QUEST)

Sergeant Major Talbot
Missing Souls(150 XP, 1 extra potion)
-Talk to Private Kinchley in Wolfminster Abbey

Sherlock Bones
The Last Meow(88 Gold, 1640 XP, Ring)
-Defeat Meowiarty in Big Ben

Sherlock Bones
Bad News...(176 Gold, 265 XP, Telescope) (after finishing “The Last Meow”)
- Talk to Merle Ambrose

(SIDE QUEST)

Mayor Pimsbury
Under the weather(83 gold, 245 XP)
-Talk to Houghe Warner

Houghe Warner
Down in the park(83 gold, 500 XP)(after finishing “Under the weather”)
-Talk back to Houghe Warner

Officer Darby
Crazy Cats(55 Gold, 850 XP)
-Defeat 8 O’Leary Scurrier

Officer Darby
More Crazy Cats(55 Gold, 865 XP) (after finishing “Crazy Cats”)
-Defeat 8 O’Leary Burglars

THE IRONWORKS

Baxter
Gate Crushers(63 Gold, 1360 XP)
-Talk to Baxter
"""


def test_parse_guide_reads_givers_and_what_comes_first():
    g = parse_guide(GUIDE)
    assert [(q.giver, q.name) for q in g][:2] == [("Sergeant Major Talbot", "Missing Souls"),
                                                  ("Sherlock Bones", "The Last Meow")]
    assert g[2].after == "The Last Meow" and g[2].main
    assert not g[3].main and g[4].after == "Under the weather"


def test_same_quest_forgives_typos_not_sequels():
    assert same_quest("Gate Crushers", "Gate Crashers")
    assert same_quest("Mail Calll", "Mail Call")
    assert not same_quest("More Crazy Cats", "Crazy Cats")


def test_pending_givers():
    g = parse_guide(GUIDE)
    # The Last Meow held: the story before it is done; Bad News waits on it.
    # Gate Crashers skipped (held); Under the Weather logged done.
    out = pending_givers(g, {"The Last Meow", "Gate Crashers"}, {"Under the Weather"})
    assert out == {"houghewarner": ["Down in the park"], "officerdarby": ["Crazy Cats"]}
    # More Crazy Cats held: Crazy Cats (what it came after) is done too.
    out = pending_givers(g, {"The Last Meow", "Gate Crashers", "More Crazy Cats"}, {"Under the Weather"})
    assert "officerdarby" not in out
