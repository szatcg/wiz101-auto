import asyncio
import math

from wizwalker import XYZ

from wiz101_auto.safe_teleport import allow_engage, install


class _Mob:
    def __init__(self, x, y):
        self.p = XYZ(x, y, 0)

    async def location(self):
        return self.p


class _Body:
    async def position(self):
        return XYZ(0, 0, 0)


class FakeClient:
    def __init__(self, mobs):
        self.mobs = [_Mob(*m) for m in mobs]
        self.landed = []
        self.body = _Body()

    async def teleport(self, xyz):
        self.landed.append(xyz)

    async def in_battle(self):
        return False

    async def get_mobs(self):
        return self.mobs

    async def get_base_entity_list(self):
        return []


def test_teleport_lands_clear_of_an_enemy_at_the_spot():
    c = FakeClient([(5000, 0)])
    install(c)
    asyncio.run(c.teleport(XYZ(5000, 100, 0)))
    spot = c.landed[-1]
    assert math.dist((spot.x, spot.y), (5000, 0)) > 700


def test_clear_spot_and_engage_teleports_go_straight_there():
    c = FakeClient([(5000, 0)])
    install(c)
    asyncio.run(c.teleport(XYZ(0, 3000, 0)))
    assert (c.landed[-1].x, c.landed[-1].y) == (0, 3000)
    allow_engage(c)
    asyncio.run(c.teleport(XYZ(5000, 0, 0)))
    assert (c.landed[-1].x, c.landed[-1].y) == (5000, 0)


def test_enemies_on_another_level_are_ignored():
    from wiz101_auto.safe_teleport import same_level

    platform = XYZ(0, 0, 500)
    below = [XYZ(100, 0, 0), XYZ(200, 0, 480)]
    kept = same_level(platform, below)
    assert [(h.x, h.z) for h in kept] == [(200, 480)]


def test_off_the_map_only_past_the_known_grounds_outline():
    from wiz101_auto.safe_teleport import outside

    ground = [(x, y, 0) for x in range(-2000, 2001, 500) for y in range(-2000, 2001, 500)]
    assert not outside(XYZ(1900, -1900, 0), ground, 300)  # an unseen corner inside the map
    assert outside(XYZ(0, -2600, 0), ground, 300)  # past the edge: the clouds


class _MovingBody:
    def __init__(self):
        self.p = XYZ(0, 0, 0)

    async def position(self):
        return self.p


class LazyGame(FakeClient):
    """Drops the first teleport (the game didn't pick it up in time)."""

    def __init__(self):
        super().__init__([])
        self.body = _MovingBody()
        self.calls = 0

    async def zone_name(self):
        return "Test/Zone"

    async def teleport(self, xyz, **kwargs):
        self.calls += 1
        if self.calls > 1:
            self.body.p = xyz
        self.landed.append(xyz)


def test_a_teleport_the_game_dropped_is_tried_again(tmp_path, monkeypatch):
    import wiz101_auto.tpspots as tpspots

    monkeypatch.setattr(tpspots, "_SPOTS", tpspots.TeleportSpots(tmp_path / "spots.json"), raising=False)
    monkeypatch.setattr(tpspots, "spots", lambda: tpspots._SPOTS)
    c = LazyGame()
    install(c)
    asyncio.run(c.teleport(XYZ(3000, 0, 0)))
    assert c.calls == 2 and (c.body.p.x, c.body.p.y) == (3000, 0)
