

def test_a_boss_resisting_myth_seeds_prisms():
    from wiz101_auto.combat import sim
    from wiz101_auto.combat.deckopt import seed_prisms

    haru = sim.Foe("Haru", 1480, "myth", {"myth": 0.8, "storm": -0.5}, [], boss=True)
    grunt = sim.Foe("Imitsu Defouler", 675, "myth", {"myth": 0.8}, [], boss=False)
    cards = ["Myth Prism", "Humongofrog"]
    assert seed_prisms({"Humongofrog": 3}, cards, [([haru], 1)])["Myth Prism"] == 3
    assert "Myth Prism" not in seed_prisms({"Humongofrog": 3}, cards, [([grunt], 1)])
