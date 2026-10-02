from wizwalker import XYZ

from wiz101_auto import walkmap


def test_retreat_points_step_back_toward_the_start():
    pts = walkmap.retreat_points(XYZ(0, 1000, 0), XYZ(0, 0, 0))
    assert [round(p.y) for p in pts] == [930, 860, 770, 650, 480]
    assert walkmap.retreat_points(XYZ(0, 100, 0), XYZ(0, 0, 0))[-1].y == 30  # only steps shorter than the gap


def test_snap_keeps_far_answers_out():
    assert walkmap.SNAP_MAX < 1000


def test_parse_nav_reads_vertices_in_sequence():
    import struct

    data = struct.pack("<hhh", 2, 2, 0)
    data += struct.pack("<fffh", 1.0, 2.0, 3.0, 0) + struct.pack("<fffh", 4.0, 5.0, 6.0, 1)
    data += struct.pack("<i", 0)
    assert walkmap.parse_nav(data) == [(1.0, 2.0, 3.0), (4.0, 5.0, 6.0)]


def test_chunk_centers_cover_each_square_once_nearest_first():
    side = walkmap.CHUNK
    pts = [(10, 10, 0), (20, 30, 10), (side + 5, 5, 100), (3 * side + 5, 5, 0)]
    tour = walkmap.chunk_centers(pts, (0, 0))
    assert len(tour) == 3
    assert tour[0][:2] == (side / 2, side / 2) and tour[0][2] == 5  # mean height of its points
    assert [round(c[0] / side - 0.5) for c in tour] == [0, 1, 3]
