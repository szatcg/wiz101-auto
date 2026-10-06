from wiz101_auto.gatewatch import walked_through

A = "WizardCity/Gauntlets/WC_Triton_Gauntlet1/WC_Triton_Gauntlet_01"
B = "WizardCity/Gauntlets/WC_Triton_Gauntlet1/WC_Triton_Gauntlet_01a"


def test_a_walk_into_the_next_room_is_learned():
    samples = [(t / 4, A, -2000 - 40 * t, 50.0, 0.0) for t in range(12)]  # walking west
    door, start = walked_through(samples, B)
    assert door[0] < samples[-1][2] and abs(door[1] - 50) < 1
    assert start[0] > samples[-1][2]


def test_standing_still_or_a_jump_is_not_a_door():
    still = [(t / 4, A, 100.0, 100.0, 0.0) for t in range(12)]  # a Recall: standing still
    assert walked_through(still, B) is None
    jump = [(t / 4, A, 100.0 + (5000 if t > 8 else 0), 100.0, 0.0) for t in range(12)]
    assert walked_through(jump, B) is None
    assert walked_through(still, "Zafaria/ZF_Z00_Hub") is None  # another world: never a door
