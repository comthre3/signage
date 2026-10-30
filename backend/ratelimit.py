"""Shared rate-limit state for multi-worker deployments.

The backend runs several uvicorn workers, each its own process. Any limiter
holding counters in process memory is therefore N independent limiters, and an
advertised "10 per minute" silently becomes 10*N. This module keeps that state
in Redis so every worker enforces one shared budget.

Two deliberate choices:

*Client identity.* Limits are keyed on the address Cloudflare reports, never on
the leftmost X-Forwarded-For entry. XFF is appended to by each hop and the
client controls what it sends first, so keying on it lets an attacker reset
their own bucket at will by rotating the header -- strictly worse than no
limit, because it looks protected. CF-Connecting-IP is written by Cloudflare
and overwrites anything the client sent.

*Degradation.* If Redis is unreachable the limiter falls back to per-process
counters rather than failing the request. Failing closed would turn a Redis
blip into a total API outage; failing open would leave brute-force protection
absent exactly when infrastructure is already unhealthy. Falling back lands on
the behavior this deployment had before Redis existed: real limits, just not
shared between workers.
"""

import logging
import os
import threading
import time
from collections import defaultdict

from slowapi import Limiter
from slowapi.errors import RateLimitExceeded

logger = logging.getLogger("khanshoof.ratelimit")

REDIS_URL = os.getenv("REDIS_URL", "redis://redis:6379/0")

# How long to stay on the in-process fallback after a Redis failure before
# probing again, so a hard-down Redis does not add a connection attempt to
# every single request.
_REDIS_RETRY_COOLDOWN_S = 10.0

_redis_client = None
_redis_unhealthy_until = 0.0
_redis_lock = threading.Lock()


# Both Redis clients -- ours and slowapi's -- get these. Without them redis-py
# waits forever on a Redis that accepts connections but never answers, which
# holds the worker thread instead of falling back.
_REDIS_TIMEOUTS = {"socket_connect_timeout": 0.25, "socket_timeout": 0.25}


def _connect():
    import redis  # imported lazily so the module is importable without redis
    return redis.Redis.from_url(
        REDIS_URL,
        **_REDIS_TIMEOUTS,
        health_check_interval=30,
        decode_responses=True,
    )


def get_redis():
    """Return a live Redis client, or None if Redis is currently unusable.

    Never raises: callers treat None as "use the fallback".
    """
    global _redis_client, _redis_unhealthy_until
    now = time.monotonic()
    if now < _redis_unhealthy_until:
        return None
    client = _redis_client
    if client is None:
        with _redis_lock:
            if _redis_client is None:
                try:
                    _redis_client = _connect()
                except Exception as exc:
                    _redis_unhealthy_until = now + _REDIS_RETRY_COOLDOWN_S
                    logger.warning("redis_connect_failed err=%s", exc)
                    return None
            client = _redis_client
    return client


def _mark_unhealthy(exc):
    global _redis_unhealthy_until
    _redis_unhealthy_until = time.monotonic() + _REDIS_RETRY_COOLDOWN_S
    logger.warning("redis_unavailable_falling_back err=%s", exc)


class _FailoverLimiter(Limiter):
    """slowapi's switch to memory is one flag shared by the worker's threads,
    and only a request that finds it unset is retried in memory. Requests
    already in flight on Redis when it dies find it set by whichever failed
    first, and slowapi re-raises theirs. Retry those in memory as well.

    (slowapi's swallow_errors is no help: on a decorated route it skips setting
    request.state.view_rate_limit, which the decorator then reads -- a 500.)
    """

    def _check_request_limit(self, request, endpoint_func, in_middleware=True):
        try:
            super()._check_request_limit(request, endpoint_func, in_middleware)
        except RateLimitExceeded:
            raise
        except Exception:
            if not self._storage_dead:
                raise
            super()._check_request_limit(request, endpoint_func, in_middleware)


def build_limiter(enabled: bool) -> Limiter:
    """The slowapi Limiter behind the @limiter.limit routes (login, signup,
    pairing).

    slowapi re-raises storage errors by default, so a Redis that died after
    startup turned every one of those routes into a 500. With
    in_memory_fallback_enabled it instead switches to per-process memory with
    the same limits, and probes Redis with backoff (1s, doubling to 32s) to
    switch back once Redis recovers. Redis is given as the storage even when
    it is down at boot, so a worker started during an outage still returns to
    the shared budget.

    The key is client_ip, NOT get_remote_address: behind the tunnel every
    request shares one TCP peer, so get_remote_address made these limits a
    single global bucket instead of per-caller.
    """
    return _FailoverLimiter(
        key_func=client_ip,
        enabled=enabled,
        storage_uri=REDIS_URL if enabled else None,
        storage_options=dict(_REDIS_TIMEOUTS) if enabled else {},
        in_memory_fallback_enabled=True,
    )


def client_ip(request) -> str:
    """Identity a rate limit is keyed on.

    Order matters. CF-Connecting-IP is set by Cloudflare and cannot be forged
    by the caller. X-Forwarded-For is only consulted when Cloudflare's header
    is absent, and then the RIGHTMOST entry is taken -- that is the one our own
    trusted proxy appended, whereas the leftmost is whatever the client claimed.
    """
    if request is None:
        return "unknown"
    headers = getattr(request, "headers", {}) or {}
    cf = headers.get("cf-connecting-ip")
    if cf and cf.strip():
        return cf.strip()
    fwd = headers.get("x-forwarded-for")
    if fwd:
        parts = [p.strip() for p in fwd.split(",") if p.strip()]
        if parts:
            return parts[-1]
    client = getattr(request, "client", None)
    return getattr(client, "host", None) or "unknown"


# ── Fixed-window counter used for per-plan API quotas ──────────────────
# Fixed windows (rather than the previous sliding list of timestamps) because
# a counter is one INCR instead of transferring and re-filtering a timestamp
# list on every request. The tradeoff is a burst straddling a window boundary
# can reach up to 2x the limit; for plan quotas that is acceptable and is how
# most published API quotas behave.

_local_counters: dict = defaultdict(lambda: {"count": 0, "reset": 0.0})
_local_counters_lock = threading.Lock()


def _local_incr(key: str, window_s: int) -> int:
    now = time.time()
    with _local_counters_lock:
        # Opportunistically drop expired buckets so this dict cannot grow
        # without bound the way the previous per-key timestamp lists did.
        if len(_local_counters) > 10000:
            for k, v in list(_local_counters.items()):
                if v["reset"] <= now:
                    del _local_counters[k]
        slot = _local_counters[key]
        if slot["reset"] <= now:
            slot["count"] = 0
            slot["reset"] = now + window_s
        slot["count"] += 1
        return slot["count"]


def incr_window(key: str, window_s: int) -> int:
    """Increment `key`'s counter for the current window; return the new count.

    Falls back to a per-process counter when Redis is unavailable.
    """
    client = get_redis()
    if client is not None:
        try:
            bucket = int(time.time() // window_s)
            redis_key = f"khanrl:{key}:{window_s}:{bucket}"
            pipe = client.pipeline()
            pipe.incr(redis_key)
            # Expire slightly past the window so the final requests in a window
            # still see an accurate count.
            pipe.expire(redis_key, window_s + 5)
            count, _ = pipe.execute()
            return int(count)
        except Exception as exc:
            _mark_unhealthy(exc)
    return _local_incr(key, window_s)


def window_reset_in(window_s: int) -> int:
    """Seconds until the current fixed window rolls over (>=1)."""
    now = time.time()
    return max(1, int(window_s - (now % window_s)))


def reset_for_tests() -> None:
    """Clear in-process fallback state between tests."""
    global _redis_client, _redis_unhealthy_until
    with _local_counters_lock:
        _local_counters.clear()
    _redis_client = None
    _redis_unhealthy_until = 0.0
