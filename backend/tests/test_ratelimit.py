"""Tests for shared (multi-worker) rate-limit state.

These cover the two properties that make multi-worker deployment safe: the
limiter key cannot be forged by the caller, and losing Redis degrades to
per-process counting rather than to an outage or to no limit at all.
"""
import time
import pytest

import ratelimit


class _Req:
    def __init__(self, headers=None, host=None):
        self.headers = headers or {}
        self.client = type("C", (), {"host": host})() if host else None


@pytest.fixture(autouse=True)
def _clean():
    ratelimit.reset_for_tests()
    yield
    ratelimit.reset_for_tests()


# ── client_ip: must not be forgeable ──────────────────────────────────

def test_prefers_cloudflare_header():
    r = _Req({"cf-connecting-ip": "203.0.113.7",
              "x-forwarded-for": "1.2.3.4"}, host="172.20.0.1")
    assert ratelimit.client_ip(r) == "203.0.113.7"


def test_client_supplied_xff_prefix_cannot_shift_the_key():
    """Leftmost XFF is attacker-controlled; the key must ignore it.

    An attacker rotating the leftmost entry must land in the SAME bucket,
    otherwise they reset their own limit at will.
    """
    a = _Req({"x-forwarded-for": "9.9.9.9, 198.51.100.4"})
    b = _Req({"x-forwarded-for": "8.8.8.8, 198.51.100.4"})
    assert ratelimit.client_ip(a) == ratelimit.client_ip(b) == "198.51.100.4"


def test_cloudflare_header_wins_over_forged_xff():
    r = _Req({"cf-connecting-ip": "203.0.113.7",
              "x-forwarded-for": "evil, evil2"})
    assert ratelimit.client_ip(r) == "203.0.113.7"


def test_falls_back_to_peer_then_unknown():
    assert ratelimit.client_ip(_Req({}, host="172.20.0.9")) == "172.20.0.9"
    assert ratelimit.client_ip(_Req({})) == "unknown"
    assert ratelimit.client_ip(None) == "unknown"


# ── counters ──────────────────────────────────────────────────────────

def test_incr_window_counts_up_and_is_per_key():
    assert ratelimit.incr_window("k1", 60) == 1
    assert ratelimit.incr_window("k1", 60) == 2
    assert ratelimit.incr_window("k2", 60) == 1


def test_window_reset_in_is_within_the_window():
    for w in (60, 3600):
        v = ratelimit.window_reset_in(w)
        assert 1 <= v <= w


def test_falls_back_to_local_counter_when_redis_is_down(monkeypatch):
    """Redis unreachable must still enforce a limit, not skip counting."""
    monkeypatch.setattr(ratelimit, "get_redis", lambda: None)
    counts = [ratelimit.incr_window("down", 60) for _ in range(3)]
    assert counts == [1, 2, 3], "counting must continue without Redis"


def test_storage_uri_is_none_when_redis_is_down(monkeypatch):
    monkeypatch.setattr(ratelimit, "get_redis", lambda: None)
    assert ratelimit.storage_uri() is None


def test_redis_error_mid_flight_degrades_instead_of_raising(monkeypatch):
    """A Redis failure during INCR must not surface as a 500."""
    class _Boom:
        def pipeline(self):
            raise RuntimeError("connection reset")
    monkeypatch.setattr(ratelimit, "get_redis", lambda: _Boom())
    assert ratelimit.incr_window("boom", 60) == 1   # served by the fallback


def test_local_counter_rolls_over_after_its_window():
    ratelimit.incr_window("roll", 1)
    assert ratelimit.incr_window("roll", 1) == 2
    time.sleep(1.1)
    assert ratelimit.incr_window("roll", 1) == 1, "window did not roll over"
