from wiz101_auto.prompt_watch import should_talk


def test_talks_only_to_the_person_the_objective_names():
    assert should_talk("Talk To Junho Shan in Hametsu Village", "Press X or  to Talk", "Junho Shan")
    assert should_talk("Locate Joo-Young in Hametsu Village", "Press X or  to Talk", "Joo-Young")
    assert not should_talk("Locate Junho Shan in Hametsu Village", "Press X or  to Activate", "Teleporter")
    assert not should_talk("Talk To Junho Shan in Hametsu Village", "Press X or  to Talk", "Ma Chieh")
    assert not should_talk("Defeat 10 Cursed Ronins", "Press X or  to Talk", "Ken Shui")
