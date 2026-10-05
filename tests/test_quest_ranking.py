

def test_a_building_belongs_to_its_area():
    from wiz101_auto.quest import room_of

    assert room_of("Zafaria/Interiors/ZF_Z10_I03_Drum_House", "Zafaria/ZF_Z10_Elephant_Graveyard")
    assert not room_of("Zafaria/Interiors/ZF_Z09_I02_Gorilla_Shack", "Zafaria/ZF_Z10_Elephant_Graveyard")
    assert not room_of("Zafaria/ZF_Z09_Drum_Jungle", "Zafaria/ZF_Z10_Elephant_Graveyard")
