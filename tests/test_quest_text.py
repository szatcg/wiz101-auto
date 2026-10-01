

def test_use_spots_start_on_the_object_then_widen():
    from wiz101_auto.quest import use_spots

    spots = use_spots()
    assert spots[0] == (0.0, 0.0) and len(spots) == 5 + 24
    assert max(abs(x) for x, _ in spots) == 300.0
