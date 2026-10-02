from wiz101_auto.bring_out import is_pickup, next_kind


def test_pickups():
    assert is_pickup("Crate")
    assert is_pickup("Clockwork Parts")
    assert not is_pickup("Planks")
    assert not is_pickup("Power Lever")
    assert is_pickup("Firebender's Chest") and is_pickup("Crystal Stand")  # (Malistaire's locked door)
    assert not is_pickup("Monkey Bandstand")


def test_order_boss_pickups_npcs_switches():
    assert next_kind(True, 3, 1, False) == "boss"
    assert next_kind(False, 3, 1, False) == "pickup"
    assert next_kind(False, 0, 1, False) == "npc"
    assert next_kind(False, 0, 0, False) == "switches"
    assert next_kind(False, 0, 0, True) is None
