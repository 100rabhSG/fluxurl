# Fluxurl — High-Level Design

> Living document. Updated as each phase changes the architecture. See `Docs/decisions/` for the *why* behind each choice.
>
> **Status:** v1 design — Phases 1–2 complete. Phases 3–5 not yet built.

---

## 1. System context

Fluxurl is a public URL shortener. Anyone on the internet can submit a long URL and receive a short code; anyone with the short code can be redirected to the original URL.

**Functional requirements (v1):**

- **Shorten:** `POST /shorten` accepts a long URL, returns a generated short code (and full short URL).
- **Redirect:** `GET /{short_code}` returns a 302 redirect to the original URL.
- **No authentication** for either operation. Anonymous users can shorten and redirect. Codes are unguessable random strings, so the lack of auth doesn't make existing URLs publicly enumerable.
- **No analytics** in v1 (deferred to Phase 6).

**Scale assumptions (v1 target):**

| Metric | Value |
|---|---|
| Registered users | 1M |
| DAU | 50k (5% of registered) |
| URLs created / day | 5k |
| Redirects / day | 100k |
| Read : Write ratio | **20 : 1** (read-heavy) |
| Storage growth | ~1 GB / year (5k × 365 × ~500 B) |

**Non-functional targets (v1):**

- **Latency:**
  - `POST /shorten` — p99 < 500ms (user is actively waiting; "save" tier latency is acceptable)
  - `GET /{short_code}` — p99 < 100ms (redirect is invisible to the user but adds to the destination page's load time; must not be a tax on someone else's experience)
- **Availability:** target ~99% (single EC2 instance, no redundancy). Higher targets would require load balancing, health checks, and multi-AZ deployment — out of scope for v1.
- **Durability:** no formal application-level target. Data lives on a single EBS volume with no backups. EBS itself provides ~99.8–99.9% annual durability. A volume failure or accidental deletion is unrecoverable in v1. Phase 7 (RDS migration) introduces automated backups.
- **Consistency:** v1 uses a single Postgres instance, so distributed consistency models (CAP theorem, eventual vs. strong consistency) don't meaningfully apply — there's only one node, so there's nothing to be inconsistent with. Postgres provides ACID transaction semantics. This becomes a real design question in Phase 7+ if read replicas are introduced and in Phase 10 when a cache layer is added.


## 2. Components

The pieces that make up the system and what each one is responsible for.

**v1:**

- **API service** — Python 3.12 + FastAPI app, async, single process. Handles `POST /shorten` and `GET /{short_code}`. Runs as a Docker container.
- **Database** — single Postgres 16 instance. Stores URL mappings. Single source of truth. Runs as a Docker container alongside the API.

**Deferred to later phases:**

- **Container registry** — AWS ECR (Phase 4)
- **CI/CD pipeline** — GitHub Actions (Phase 5)
- **Managed database** — AWS RDS (Phase 7)

---

## 3. Data model

Logical sketch — exact column types and constraints decided in Phase 1.

**Table: `urls`**

| Column | Type | Notes |
|---|---|---|
| `short_code` | VARCHAR(7), **PK** | Natural primary key — see [ADR 0005](decisions/0005-primary-key-for-urls-table.md) |
| `long_url` | TEXT | NOT NULL. Pydantic enforces max 2083 chars at the API boundary |
| `created_at` | TIMESTAMPTZ | `server_default=now()`, NOT NULL |

No surrogate `id` column. No foreign keys, no other tables in v1. (`clicks` table arrives with Phase 6 analytics.)

---

## 4. Request flows

Happy paths only. Error and edge-case handling decided in Phase 1.

**`POST /shorten`:**

1. Receive long URL in request body.
2. Validate it's a well-formed URL.
3. Generate 7-char random base62 code using `secrets` (cryptographically secure RNG).
4. Insert `(short_code, long_url)` into `urls` table.
5. On unique-constraint collision: regenerate code, retry. Max 5 retries before returning 500.
6. Return the full short URL in the response body.

**`GET /{short_code}`:**

1. Look up `short_code` in `urls` table.
2. If found → return **302** with `Location: <long_url>`.
3. If not found → return 404.

---

## 5. Deployment topology

Where each component runs, how they talk, network boundaries.

*v1 target: single EC2 instance running the API and Postgres as Docker containers, no load balancer, no reverse proxy. Built up incrementally — local dev below, EC2 in Phase 3, ECR in Phase 4.*

### 5.1 Local-dev topology (Phase 2)

`docker compose up` launches three containers on a single user-defined bridge network. Two of them stay running; one is one-shot.

```
┌──────────────────── host machine ────────────────────┐
│                                                       │
│  browser ──► localhost:8000 ──┐                       │
│                                │                      │
│   ┌─────────── compose network (bridge) ──────────┐   │
│   │                            ▼                  │   │
│   │                   ┌─────────────────┐         │   │
│   │   migrate ──►     │      app        │         │   │
│   │  (one-shot,       │  (FastAPI,      │         │   │
│   │   exits 0)        │   uvicorn)      │         │   │
│   │       │           └────────┬────────┘         │   │
│   │       │                    │                  │   │
│   │       └──── via "db" ──────┤                  │   │
│   │                            ▼                  │   │
│   │                   ┌─────────────────┐         │   │
│   │                   │      db         │         │   │
│   │                   │  (postgres:16)  │         │   │
│   │                   └─────────────────┘         │   │
│   └────────────────────────────────────────────────┘  │
│                                                       │
│  localhost:5432 ◄── (port-mapped for dev convenience) │
└───────────────────────────────────────────────────────┘
```

**How they talk:**

- The host reaches `app` through the published port `8000:8000`. Postgres is also port-mapped (`5432:5432`) for local inspection from the host — a dev convenience, not something a production topology would expose.
- Inside the network, `app` and `migrate` resolve the database at the hostname `db`. Compose's default network resolves **service names** to container IPs via Docker's embedded DNS — that's why `DATABASE_URL` uses `@db:5432`.

**Lifecycle ordering:**

1. `db` starts and passes its `pg_isready` healthcheck.
2. `migrate` runs `alembic upgrade head`, exits 0, and terminates.
3. `app` starts (gated on `migrate` having completed successfully) and begins serving traffic.

The rationale for the separate `migrate` service is in [ADR 0010](decisions/0010-migration-strategy.md).

**What is the deployable unit?**

The **image**, not the source code or the compose file. `app` and `migrate` use the *same locally-built image*, differentiated only by their `command`. `db` uses the upstream `postgres:16` image from Docker Hub. Phase 4 pushes the locally-built image to ECR; Phase 3 runs that same image on EC2. The compose file is environment-specific glue.

**What is *not* in this topology yet, and where it shows up:**

- No reverse proxy (Nginx) — Phase 9.
- No external image registry — image is built locally; ECR arrives in Phase 4.
- No managed database — `postgres:16` runs as a sibling container; RDS replaces it in Phase 7.
- No TLS / custom domain — Phase 9.
- No process supervisor — `docker compose up` is interactive; on EC2 in Phase 3 we'll need `--restart=always` or systemd to keep containers alive across reboots.

---

## 6. Failure modes

What can break, what happens when it does, what's acceptable for v1. First pass — expanded as new failure modes are discovered during build.

| Failure | What happens | v1 acceptable? |
|---|---|---|
| Postgres container down | All requests return 5xx | Yes — manual recovery, minutes of downtime |
| EC2 reboot | Service down until containers restart | Yes — fits inside the 99% availability budget |
| EBS volume corruption / loss | Permanent data loss, no recovery path | Yes — explicitly accepted, Phase 7 (RDS) fixes |
| Code collision retries exhausted | 500 to client | Yes — vanishingly rare at v1 scale (~6 in 100k inserts at peak projected scale) |
| Deploy mid-request | In-flight request fails | Yes — client retries |

---

## 7. Scaling story

Throughput analysis from the v1 scale targets:

| Rate | Average | Peak (10×) |
|---|---|---|
| Writes (`POST /shorten`) | ~0.06 / sec (1 every ~17 s) | ~0.6 / sec |
| Reads (`GET /{code}`) | ~1.2 / sec | ~12 / sec |

**What this means for v1 design:**

- A single small EC2 instance with a single Postgres container handles peak load with massive headroom.
- **No sharding, no read replicas, no caching** are needed for *performance* at v1 scale.
- Caching may still be added later for *latency* reasons (a different argument from throughput — noted for Phase 10).

**Constraints to revisit when these numbers grow 10×:**

- Read scaling — at ~12 reads/sec average, single Postgres still handles it; at 10× (~120 reads/sec) a read replica or cache becomes the natural next step.
- Write throughput — still fine even at 10×, but if a counter-based ID strategy is ever introduced, the central counter becomes the bottleneck before raw throughput does.
- Storage — at 10× growth (~10 GB/year), still well inside RDS free-tier limits. No urgency.

---

## 8. Out of scope for v1

Deliberately deferred. Each cut is intentional, not forgotten.

**Product features cut:**

- **URL expiry** — see [ADR 0002](decisions/0002-no-url-expiry-v1.md). Requires a background job + deletion strategy; cost outweighs the value at v1 scale.
- **Custom / vanity short codes** — system generates codes; user has no input on length or characters.
- **User accounts / auth / login** — no signup flow in v1.
- **URL editing or deletion** — once created, a short URL is permanent.
- **Click analytics** — deferred to Phase 6 (separate `clicks` table, `/stats` endpoint).
- **Rate limiting** — deferred to Phase 10 (Redis).

**Infrastructure cut:**

- **Backups** — no automated or manual backup strategy in v1. Most consequential cut; called out explicitly so it isn't missed.
- Kubernetes / ECS / Fargate / Lambda
- CloudFront / Route 53 / API Gateway
- Redis, Celery
- Multi-environment (single env only)
- Custom domain / TLS (Phase 9)

---

## 9. Open questions (resolved)

All Phase 1 questions have been resolved:

- ~~**Primary key for `urls`**~~ → `short_code` as natural PK. See [ADR 0005](decisions/0005-primary-key-for-urls-table.md).
- ~~**URL validation strictness**~~ → Pydantic `HttpUrl` validates format (requires scheme + host). Accepts `http` and `https`; rejects relative URLs and malformed schemes.
- ~~**Max length of `long_url`**~~ → Pydantic `HttpUrl` enforces 2083-char limit (browser standard). No additional app-level check.
- ~~**404 behavior**~~ → Plain JSON `{"detail": "short code not found"}`. Same message for invalid shape and valid-but-missing (prevents information leakage).