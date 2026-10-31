"""The backend's Postgres connection budget.

db.connect() keeps one connection per thread, so how many threads a worker
runs is how many connections it can open. docker-compose.yml sizes Postgres's
max_connections from THREADPOOL_SIZE; these check the backend holds to it.
"""
import anyio
import anyio.to_thread

import db


def test_startup_caps_the_threadpool_at_threadpool_size(monkeypatch):
    monkeypatch.setattr(db, "THREADPOOL_SIZE", 7)

    async def tokens_after_startup():
        await db.bound_threadpool()
        return anyio.to_thread.current_default_thread_limiter().total_tokens

    assert anyio.run(tokens_after_startup) == 7


def test_the_app_runs_the_cap_at_startup():
    import main
    assert db.bound_threadpool in main.app.router.on_startup
