"""Tests for shared (multi-worker) rate-limit state.

These cover the two properties that make multi-worker deployment safe: the
limiter key cannot be forged by the caller, and losing Redis degrades to
per-process counting rather than to an outage or to no limit at all.
"""
import time
import pytest

import ratelimit

DEAD_REDIS = "redis://127.0.0.1:1/0"   # nothing listens on port 1


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


def test_api_key_counter_survives_an_unreachable_redis(monkeypatch):
    """The real redis-py failure path, not a stand-in object."""
    monkeypatch.setattr(ratelimit, "REDIS_URL", DEAD_REDIS)
    counts = [ratelimit.incr_window("unreachable", 60) for _ in range(3)]
    assert counts == [1, 2, 3]


# ── slowapi route limits (login, signup, pairing) ─────────────────────
# Redis was up when the worker started and has since gone away. These build
# the limiter the way main.py does and point it at a Redis that is gone.

def _limited_app(monkeypatch, redis_url, limit="3/minute"):
    from fastapi import FastAPI, Request
    from fastapi.testclient import TestClient
    from slowapi import _rate_limit_exceeded_handler
    from slowapi.errors import RateLimitExceeded

    monkeypatch.setattr(ratelimit, "REDIS_URL", redis_url)
    limiter = ratelimit.build_limiter(True)
    app = FastAPI()
    app.state.limiter = limiter
    app.add_exception_handler(RateLimitExceeded, _rate_limit_exceeded_handler)

    # Sync, like every limited route in main.py, so it runs in the threadpool.
    @app.get("/limited")
    @limiter.limit(limit)
    def limited(request: Request) -> dict:
        return {"ok": True}

    return limiter, TestClient(app, raise_server_exceptions=False)


def test_route_limit_degrades_to_per_process_when_redis_dies(monkeypatch):
    """A Redis outage must not 500 every login -- and must still limit."""
    _, client = _limited_app(monkeypatch, DEAD_REDIS)
    codes = [client.get("/limited").status_code for _ in range(4)]
    assert codes == [200, 200, 200, 429]


def test_redis_failing_as_another_request_fails_over_is_not_a_500(monkeypatch):
    """slowapi's switch to memory is one shared flag, and the limited routes
    run on concurrent threads. When Redis dies every in-flight request fails,
    but only the first finds the flag unset and retries in memory; the rest
    find it already set, and slowapi re-raises theirs."""
    limiter, client = _limited_app(monkeypatch, DEAD_REDIS)
    redis_hit = limiter._limiter.hit

    def hit_after_another_thread_failed_over(*args, **kwargs):
        limiter._storage_dead = True
        return redis_hit(*args, **kwargs)

    monkeypatch.setattr(limiter._limiter, "hit", hit_after_another_thread_failed_over)
    codes = [client.get("/limited").status_code for _ in range(4)]
    assert codes == [200, 200, 200, 429], "the raced request must count too"


def test_unresponsive_redis_does_not_hang_the_request(monkeypatch):
    """A hung Redis (accepts the connection, never answers) must cost a request
    a timeout and a fallback, not hold a worker thread indefinitely."""
    import socket
    import threading

    srv = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    srv.bind(("127.0.0.1", 0))
    srv.listen(16)   # the kernel completes handshakes; nothing ever replies
    try:
        _, client = _limited_app(
            monkeypatch, f"redis://127.0.0.1:{srv.getsockname()[1]}/0")
        result = {}
        t = threading.Thread(
            target=lambda: result.update(code=client.get("/limited").status_code),
            daemon=True)
        t.start()
        t.join(5)
        assert not t.is_alive(), "request hung on an unresponsive Redis"
        assert result["code"] == 200
    finally:
        srv.close()


def test_app_limiter_is_built_to_degrade():
    import main
    assert isinstance(main.limiter, ratelimit._FailoverLimiter)
    assert main.limiter._in_memory_fallback_enabled
