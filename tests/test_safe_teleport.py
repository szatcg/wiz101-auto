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
