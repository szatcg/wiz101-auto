from wiz101_auto.teamup import RESUME_AFTER_DEFEAT, defeated_inside_recently

HUB = "Aquila/AQ_Z00_Hub"


def test_resume_only_after_a_recent_defeat_inside():
    room = (100.0, "Aquila/Interiors/AQ_Z01_Apollo_Room")
    assert defeated_inside_recently(room, HUB, 200.0)
    assert not defeated_inside_recently(room, HUB, 100.0 + RESUME_AFTER_DEFEAT + 1)
    assert not defeated_inside_recently((100.0, HUB), HUB, 200.0)
    assert not defeated_inside_recently((100.0, "Marleybone/Big_Ben"), HUB, 200.0)
    assert not defeated_inside_recently(None, HUB, 200.0)
