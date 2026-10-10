

def test_an_inner_end_idle_keeps_the_outer_window():
    import time

    from wiz101_auto.safety import Controller

    c = Controller.__new__(Controller)
    c.idle_until = 0.0
    c.allow_idle(100)
    outer = c.idle_until
    c.allow_idle(5)
    c.end_idle()
    assert c.idle_until == outer and outer > time.monotonic() + 50
    c.end_idle()
    assert c.idle_until == 0.0
