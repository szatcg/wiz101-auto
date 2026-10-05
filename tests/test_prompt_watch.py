from wiz101_auto.prompt_watch import should_talk


def test_talks_only_to_the_person_the_objective_names():
    assert should_talk("Talk To Junho Shan in Hametsu Village", "Press X or  to Talk", "Junho Shan")
    assert should_talk("Locate Joo-Young in Hametsu Village", "Press X or  to Talk", "Joo-Young")
    assert not should_talk("Locate Junho Shan in Hametsu Village", "Press X or  to Activate", "Teleporter")
    assert not should_talk("Talk To Junho Shan in Hametsu Village", "Press X or  to Talk", "Ma Chieh")
    assert not should_talk("Defeat 10 Cursed Ronins", "Press X or  to Talk", "Ken Shui")


def test_use_prompt_for_the_named_object():
    from wiz101_auto.prompt_watch import should_use

    assert should_use("Use Crystal Charger in The Grand Chasm", "Press X to Interact", "Crystal Charger")
    assert should_use("Repair East Bridge in Grand Chasm Past", "Press X to Activate", "East Bridge")
    assert should_use("Lock Vault 1936 in Grand Chasm Past", "Press X to Activate", "Vault 1936")
    assert not should_use("Use Crystal Charger", "Press X to Talk", "Crystal Charger")  # never a talk
    assert not should_use("Use Crystal Charger", "Press X to Activate", "Portal to the Present")
    assert not should_use("Talk To Edrik", "Press X to Activate", "Edrik")


def test_collect_prompt_for_any_sample_of_the_kind():
    from wiz101_auto.prompt_watch import should_collect

    red = "Collect Red Crystal Sample in The Crystal Grove"
    assert should_collect(red, "Press X to collect", "Crystal Sample")
    both = "Collect Green and Purple Crystal Sample in The Crystal Grove"
    assert should_collect(both, "Press X", "Crystal Sample")
    assert not should_collect(red, "Press X to talk", "Crystal Sample")
    assert not should_collect("Talk To Zarek Pickmaster", "Press X", "Crystal Sample")


def test_a_sigil_named_after_the_item_is_not_a_pick_up():
    from wiz101_auto.prompt_watch import should_collect

    assert not should_collect("Collect Drum in Elephant Graveyard (0 of 4)", "Press X to enter", "Drum House")
    assert should_collect("Collect Drum in Elephant Graveyard (0 of 4)", "Press X to collect", "Drum")
    assert should_collect("Collect Red Crystal Sample", "Press X", "Crystal Sample")
