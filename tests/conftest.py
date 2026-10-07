"""Shared test setup."""

import pytest


@pytest.fixture(autouse=True)
def _no_logged_hit_rates(monkeypatch, tmp_path):
    # (The crit gamble reads the fight logs' hit rates, state/enemy_stats.json,
    # rewritten at every bot start: a test running then failed. Tests use
    # the cards' own accuracy.)
    from wiz101_auto.combat import lookahead

    monkeypatch.setattr(lookahead, "RATES_FILE", tmp_path / "no_stats.json")
    monkeypatch.setattr(lookahead, "_RATES", [None, {}])
