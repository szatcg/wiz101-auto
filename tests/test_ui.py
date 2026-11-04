

def test_a_dungeons_boss_goal_comes_first():
    from wiz101_auto.ui import boss_goal

    texts = ["Defeat Doom Trooper in Doom Moon (0 of 3)", "Defeat Mr. Montgomery in Doom Moon"]
    assert boss_goal(texts, ["Mr. Montgomery"]) == texts[1]
    assert boss_goal(texts, ["Someone Else"]) is None
    assert boss_goal(["Talk To Mr. Montgomery"], ["Mr. Montgomery"]) is None
