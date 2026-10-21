from wiz101_auto.quest import queue_moved_on

Q = {"dungeon": "Karamelle/Interiors/KM_Z03_I02_PipelineCave", "quest": "The Proofing is in the Pudding"}


def test_moved_on_to_another_area():
    assert queue_moved_on(Q, "Nibbleheim")


def test_still_in_the_dungeon():
    assert not queue_moved_on(Q, "Pipeline Cave")


def test_unknown_area_keeps_the_queue():
    assert not queue_moved_on(Q, "")
    assert not queue_moved_on({}, "Nibbleheim")
