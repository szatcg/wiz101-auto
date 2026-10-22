from wiz101_auto.upkeep import is_unreachable


def test_maintenance_box_is_unreachable():
    assert is_unreachable("Server has been brought down for maintenance.  Please try again later!")


def test_other_text_is_not():
    assert not is_unreachable("Are you sure you want to quit?")
