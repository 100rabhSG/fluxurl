# ADR 0006: Async SQLAlchemy (asyncpg) over sync (psycopg2)

- **Status:** Accepted
- **Date:** 2026-05-05
- **Phase:** 1 (app + schema)

## Context

FastAPI is an async framework — request handlers are `async def`. The database is the only I/O dependency in v1. The question is whether DB queries should also be async (non-blocking) or run synchronously inside the async event loop.

## Options considered

### Option A: Sync SQLAlchemy (psycopg2) + `run_in_executor`

Use traditional `Session` (synchronous). FastAPI can still call sync code from an `async def` handler by offloading it to a thread pool (`run_in_executor`), or by defining the handler as a plain `def` (FastAPI auto-threads plain `def` handlers).

- **Pros:**
  - Simpler API — no `await`, no `AsyncSession`, more tutorial examples available.
  - Mature ecosystem — every SQLAlchemy extension works out of the box.
- **Cons:**
  - Each blocked DB call occupies a thread from the pool. Under load, thread pool saturation = increased latency.
  - Mixing sync DB code inside an otherwise-async app is conceptually inconsistent — two concurrency models to reason about.

### Option B: Async SQLAlchemy 2.0 (asyncpg)

Use `AsyncSession`, `create_async_engine`, `await session.execute(...)`. The driver (`asyncpg`) is fully non-blocking — it uses the event loop directly, no threads.

- **Pros:**
  - Consistent mental model — everything is async, one concurrency primitive (`await`).
  - No thread pool overhead. A single event loop thread handles many concurrent requests while waiting on Postgres.
  - Matches the idiom the FastAPI docs themselves recommend.
- **Cons:**
  - Slightly different API surface from sync SQLAlchemy (e.g., `session.get()` needs `await`, lazy loading is forbidden by default, `expire_on_commit=False` is required).
  - Alembic doesn't support async natively — needs a sync driver (`psycopg`) for migrations (one extra dependency + a URL-swap trick in `env.py`).
  - Fewer StackOverflow examples compared to sync.

## Decision

**Option B: async SQLAlchemy with asyncpg.**

The learning goal of this project includes understanding async I/O end-to-end. Using sync SQLAlchemy would create a gap — "everything is async except the most important I/O call." Option B keeps the stack consistent and forces engagement with how async sessions, transactions, and connection pooling actually work.

The Alembic sync-driver workaround is a one-time setup cost (already done: `env.py` swaps `+asyncpg` → `+psycopg` for the migration runner).

## Consequences

- **All DB calls use `await`** — `await session.execute(...)`, `await session.commit()`.
- **`expire_on_commit=False`** set on the session factory. Without it, accessing an attribute after commit would trigger a lazy load, which raises in async mode (no implicit I/O allowed).
- **Two Postgres drivers in `requirements`:** `asyncpg` (runtime) and `psycopg[binary]` (Alembic migrations only). Slightly odd but harmless.
- **Lazy relationship loading is off-limits.** If future phases add relationships (e.g., `Url.clicks`), they must use `selectinload()` or `joinedload()` — explicit eager loading.
- **Testing uses `aiosqlite`** — the async equivalent of SQLite, so tests mirror the async session pattern without needing Postgres.
