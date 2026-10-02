from wizwalker import XYZ

from wiz101_auto import walkmap


def test_retreat_points_step_back_toward_the_start():
    pts = walkmap.retreat_points(XYZ(0, 1000, 0), XYZ(0, 0, 0))
    assert [round(p.y) for p in pts] == [930, 860, 770, 650, 480]
    assert walkmap.retreat_points(XYZ(0, 100, 0), XYZ(0, 0, 0))[-1].y == 30  # only steps shorter than the gap


def test_snap_keeps_far_answers_out():
    assert walkmap.SNAP_MAX < 1000
