

def test_a_boss_resisting_myth_seeds_prisms():
    from wiz101_auto.combat import sim
    from wiz101_auto.combat.deckopt import seed_prisms

    haru = sim.Foe("Haru", 1480, "myth", {"myth": 0.8, "storm": -0.5}, [], boss=True)
    grunt = sim.Foe("Imitsu Defouler", 675, "myth", {"myth": 0.8}, [], boss=False)
    cards = ["Myth Prism", "Humongofrog"]
    assert seed_prisms({"Humongofrog": 3}, cards, [([haru], 1)])["Myth Prism"] == 3
    assert "Myth Prism" not in seed_prisms({"Humongofrog": 3}, cards, [([grunt], 1)])


def test_boss_decks_keep_two_heals():
    from wiz101_auto.combat.deckopt import allowed, with_heals

    assert not allowed({"Humongofrog": 3, "Myth Prism": 3}, general=False)
    assert allowed({"Humongofrog": 3, "Pixie": 2}, general=False)
    assert with_heals({"Humongofrog": 3}, ["Pixie", "Humongofrog"], 2) == {"Humongofrog": 3, "Pixie": 2}


def test_deck_rules_ban_death_but_feint_and_all_minions(tmp_path):
    from wiz101_auto.combat.deckopt import deck_rules, is_banned

    cfg = tmp_path / "c.yaml"
    cfg.write_text("deck_search:\n  banned: [Earthquake]\n  banned_schools: [death]\n  allowed: [Feint]\n",
                   encoding="utf-8")
    rules = deck_rules(cfg)
    assert is_banned("Banshee", rules) and is_banned("Ghoul", rules) and is_banned("Earthquake", rules)
    assert not is_banned("Feint", rules) and is_banned("Troll Minion", rules)
    assert not is_banned("Humongofrog", rules)
