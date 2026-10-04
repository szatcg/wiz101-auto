import asyncio

import pytest

from wiz101_auto.bot import obey_controller
from wiz101_auto.safety import BotStopped, Controller


class _Mouse:
    async def click(self, *a, **k):
        return "clicked"


class _Client:
    def __init__(self):
        self.mouse_handler = _Mouse()
        self.moves = 0

    async def teleport(self, *a, **k):
        self.moves += 1

    async def send_key(self, *a, **k):
        self.moves += 1


def test_paused_actions_wait_and_stopped_ones_end_the_step():
    async def go():
        c, ctl = _Client(), Controller("ctrl+shift+q", "ctrl+shift+p", 0)
        obey_controller(c, ctl)
        await c.teleport(1)
        assert c.moves == 1
        ctl._resume.clear()  # paused
        pending = asyncio.create_task(c.send_key("w"))
        await asyncio.sleep(0.05)
        assert c.moves == 1 and not pending.done()  # waits while paused
        ctl.stop("stop key pressed")
        with pytest.raises(BotStopped):
            await pending
        with pytest.raises(BotStopped):
            await c.mouse_handler.click(1, 2)
        assert c.moves == 1

    asyncio.run(go())
