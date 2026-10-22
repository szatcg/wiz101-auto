from wiz101_auto.quest import in_same_area


def test_flat_interiors_are_not_one_dungeon():
    assert not in_same_area("Karamelle/Interiors/KM_Z00_ProductionJunction",
                            "Karamelle/Interiors/KM_Z08_CinnamonTrollCave")


def test_rooms_of_one_dungeon():
    assert in_same_area("Avalon/Interiors/AV_Z05_PyramidMotherMoon_Room02",
                        "Avalon/Interiors/AV_Z05_PyramidMotherMoon_Room01")
