import math

from wizwalker import XYZ

from wiz101_auto.smoothwalk import MAX_TURN, path_length, pursuit_index, turn_toward


def test_turns_a_little_at_a_time_the_short_way():
    assert turn_toward(0.0, 0.1) == 0.1                       # small: straight there
    assert math.isclose(turn_toward(0.0, 2.0), MAX_TURN)      # big: capped
    # From just under +pi to just over -pi is a small turn through pi, not a full circle.
    got = turn_toward(math.pi - 0.05, -math.pi + 0.05)
    assert math.isclose((got - (math.pi - 0.05)) % (2 * math.pi), 0.1, abs_tol=1e-9)


def test_steers_at_a_point_ahead_on_the_path():
    path = [XYZ(100, 0, 0), XYZ(200, 0, 0), XYZ(300, 0, 0), XYZ(600, 0, 0)]
    here = XYZ(0, 0, 0)
    assert pursuit_index(path, here, 0, lookahead=250) == 2   # past the close ones
    assert pursuit_index(path, XYZ(590, 0, 0), 2, lookahead=250) == 3  # the end
    assert path_length(here, path) == 600


def test_walk_only_zones_dont_make_other_zones_walk():
    import asyncio

    from wiz101_auto.safe_teleport import _walk_instead

    class Body:
        async def position(self):
            return XYZ(0, 0, 0)

    class C:
        _walk = False
        _walk_only = ("WizardCity/Gauntlets/WC_Triton_Gauntlet1",)
        body = Body()

        async def zone_name(self):
            return "WizardCity/WC_Streets/WC_OldeTown"

        async def in_battle(self):
            return False

    assert asyncio.run(_walk_instead(C(), XYZ(5000, 0, 0))) is None  # teleport as usual
