import asyncio

from wiz101_auto import upkeep


class _Stats:
    def __init__(self, reads):
        self.reads = list(reads)
        self.now = self.reads.pop(0)

    async def current_hitpoints(self):
        return self.now[0]

    async def max_hitpoints(self):
        return self.now[1]

    async def current_mana(self):
        return self.now[2]

    async def max_mana(self):
        if self.reads:  # (next poll sees the next reading)
            nxt = self.reads.pop(0)
            m, self.now = self.now[3], nxt
            return m
        return self.now[3]


class _Client:
    def __init__(self, reads):
        self.stats = _Stats(reads)


def test_a_loading_screen_read_is_not_full_health(monkeypatch):
    # (The game reads max 0 while a zone loads: that once counted as healed.)
    real_sleep = asyncio.sleep
    monkeypatch.setattr(upkeep.asyncio, "sleep", lambda *_: real_sleep(0))
    client = _Client([(0, 0, 0, 0), (1642, 2221, 400, 400)])
    hp, mana = asyncio.run(upkeep.health_mana(client))
    assert round(hp, 2) == 0.74 and mana == 1.0


def test_no_real_read_counts_as_hurt(monkeypatch):
    monkeypatch.setattr(upkeep, "HEALTH_READ_SECONDS", 0.0)
    hp, _ = asyncio.run(upkeep.health_mana(_Client([(0, 0, 0, 0)])))
    assert hp == 0.0
