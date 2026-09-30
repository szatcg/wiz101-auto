from wiz101_auto.tpspots import TeleportSpots


def test_spots_that_worked_are_kept_once_and_found_nearest_first(tmp_path):
    tp = TeleportSpots(tmp_path / "spots.json")
    assert tp.add("Z", (100, 100, 0))
    assert not tp.add("Z", (150, 120, 5))  # the same spot
    assert tp.add("Z", (1000, 100, 0))
    again = TeleportSpots(tmp_path / "spots.json")
    assert again.near("Z", (900, 100, 0), 1500)[0] == (1000, 100, 0)
    assert again.near("Z", (900, 100, 900), 1500) == []  # another floor
    assert again.near("Other", (900, 100, 0), 1500) == []
