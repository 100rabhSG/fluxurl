# ADR 0010: Run migrations as a separate one-shot compose service

- **Status:** Accepted
- **Date:** 2026-05-08
- **Phase:** Phase 2 — Dockerize

## Context

In Phase 1, schema migrations were run by hand from the host (`alembic upgrade head` against a local Postgres). Phase 2 puts the app in a container behind compose, with Postgres as a sibling container — there is no longer a host-side venv that owns migrations. Every `compose up` could now boot the app against a database whose schema is older than the code expects, which would fail at the first query.

A migration step has to run **after** the DB is reachable and **before** the app accepts traffic. The question is *who* runs it.

Constraint: solo developer, single environment, free tier — no orchestrator (k8s/ECS) to defer this to.

## Options considered

### Option A: Run migrations manually (host or `docker exec`)
- Pros: Zero infra. Same model as Phase 1.
- Cons: Requires a human-in-the-loop on every deploy. Falls apart on EC2 / CI — the whole point of Phase 2 is to remove "my laptop" from the deploy path. Easy to forget; failure mode is a silently broken app.

### Option B: Run migrations from the app's entrypoint
- Pros: One service. Alembic is idempotent (checks `alembic_version` table, no-ops when already at head — ~50 ms cost), so running on every start is cheap.
- Cons: Concurrency — if the app ever runs multiple replicas (gunicorn workers, scale-out), they race to run migrations on startup, and Alembic isn't safe under concurrent runs. Also blurs responsibility: a migration failure looks identical to an app crash in logs.

### Option C: Separate one-shot `migrate` service in compose
- Pros: Single migration runner guaranteed (only one container of this service exists). Clear separation: app's job is to serve, migrate's job is to evolve schema. Compose's `depends_on: service_completed_successfully` enforces ordering — migrate must exit 0 before app starts. Migration failures are isolated and obvious.
- Cons: Extra service definition in compose. Image is built/pulled twice (same image, different command). Slightly slower cold start — migrate boots, runs, exits, then app boots.

## Decision

Adopted **Option C** — a dedicated `migrate` service that runs `alembic upgrade head` once and exits, with the `app` service gated on its successful completion via `service_completed_successfully`.

The forcing reason is concurrency safety: even though Phase 2 only runs one app container, picking the entrypoint approach (Option B) would lock me into a pattern that breaks the moment I add a second worker or replica. Option C costs one extra service definition for a property (single migration runner) I'd otherwise have to retrofit. Manual runs (Option A) were ruled out because Phase 2's whole purpose is to make the deploy reproducible without me.

## Consequences

- **Easier:** Reproducible `compose up` from a clean clone — schema is always up-to-date before the app starts. Migration failures surface as a single non-zero exit, not as a confusing app crash.
- **Harder:** Two services to reason about instead of one. Need to remember that the same image is used for both — a code change rebuilds both.
- **Revisit when:**
  - **Phase 5 (CI/CD):** decide whether CI also runs migrations, or whether they only run on the EC2 box at deploy time. The compose-level service handles the on-host case but doesn't address pre-deploy validation.
  - **Phase 7 (RDS):** managed DB introduces real production concerns — backwards-compatible migrations, rollback strategy, expand/contract pattern. Out of scope for v1, but the current setup gives no safety net.

## Notes

- "Why not just run Alembic in the entrypoint, since it's idempotent?" — Idempotency only protects against running it *too many times*. It does not protect against running it *concurrently*. Two replicas calling `alembic upgrade head` at the same instant can both decide a migration is pending and both try to apply it. Splitting it into a one-shot service makes "exactly one runner" a structural property, not a hope.
- Required adding `psycopg[binary]` to `requirements.txt` — Alembic's `env.py` swaps the SQLAlchemy URL from `+asyncpg` to `+psycopg` (sync) because Alembic's runner is sync. This was implicit on the host (the dev venv had it installed at some point); the container, being built strictly from `requirements.txt`, exposed the missing pin.
- Considered but not adopted: a check-then-run script ("only run if there are pending migrations"). Adds complexity for no gain — Alembic already does this internally, faster than any wrapper script could.
