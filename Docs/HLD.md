# Fluxurl — High-Level Design

> Living document. Updated as each phase changes the architecture. See `Docs/decisions/` for the *why* behind each choice.
>
> **Status:** Phase 4 complete. App deployed manually to AWS EC2 with Elastic IP. Runtime image sourced from ECR; EC2 authenticates via an IAM instance role. CI/CD (Phase 5) not yet wired up.

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

---

## 2. Components

The pieces that make up the system and what each one is responsible for.

**v1 (current state, end of Phase 4):**

- **API service** — Python 3.12 + FastAPI app, async, single process. Handles `POST /shorten` and `GET /{short_code}`. Runs as a Docker container.
- **Database** — single Postgres 16 instance. Stores URL mappings. Single source of truth. Runs as a Docker container alongside the API.
- **Compute host** — AWS EC2 t3.micro running Ubuntu 22.04 LTS in `ap-south-1`. Hosts both containers.
- **Public address** — AWS Elastic IP (`3.109.34.168`), pinned to the EC2 instance. Survives instance Stop/Start; the contract Fluxurl makes with short-URL holders is that this address doesn't change.
- **Container registry** — AWS ECR private repository (`546201496354.dkr.ecr.ap-south-1.amazonaws.com/fluxurl`). Single source of truth for the runtime image. Built and pushed from laptop; pulled by EC2 during deploy.
- **EC2 instance role** — IAM role `fluxurl-ecr-pull-role` attached to the EC2 instance. Grants ECR-read permissions via temporary credentials issued by AWS through the instance metadata service. No AWS credentials are stored on the EC2 instance.

**Deferred to later phases:**

- **CI/CD pipeline** — GitHub Actions (Phase 5)
- **Managed database** — AWS RDS (Phase 7)
- **Reverse proxy + TLS** — Nginx + Let's Encrypt (Phase 9)
- **Custom domain** — Phase 9
- **Cache layer** — Redis (Phase 10)
- **Infrastructure as code** — Terraform (Phase 11)

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
6. Return the full short URL in the response body, constructed from `BASE_URL` env var + `short_code`.

**`GET /{short_code}`:**

1. Look up `short_code` in `urls` table.
2. If found → return **302** with `Location: <long_url>`.
3. If not found → return 404.

---

## 5. Deployment topology

Where each component runs, how they talk, network boundaries.

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

The **image**, not the source code or the compose file. `app` and `migrate` use the *same locally-built image*, differentiated only by their `command`. `db` uses the upstream `postgres:16` image from Docker Hub. The compose file is environment-specific glue.

### 5.2 Production topology (Phase 3 + Phase 4)

The runtime image is built on laptop locally, pushed to ECR, and pulled by a EC2 instance running the same compose layout as local-dev (with deliberate differences for network exposure and image source).

```
   Developer laptop
       │ docker build
       │ docker push
       ▼
┌────────────────────── AWS ap-south-1 ──────────────────────┐
│                                                            │
│   ┌──── ECR ────────────┐       ┌──── IAM ────────────┐    │
│   │ fluxurl repo        │       │ Role:               │    │
│   │ (private)           │◄──────┤ fluxurl-ecr-        │    │
│   │   :latest           │ pull  │ pull-role           │    │
│   └─────────────────────┘       │  • trust: ec2       │    │
│                                 │  • perms: ECR read  │    │
│                                 └──────────┬──────────┘    │
│                                            │ attached      │
│                                            ▼               │
│   Internet ──► 3.109.34.168 (Elastic IP)                   │
│                       │                                    │
│   Security group "fluxurl-sg"                              │
│     22/tcp from <my IP>     (SSH)                          │
│     80/tcp from 0.0.0.0/0   (HTTP)                         │
│                       │                                    │
│                       ▼                                    │
│   ┌──── EC2 t3.micro (Ubuntu 22.04) ────────┐              │
│   │                                         │              │
│   │   port 80 ──► app (container)           │              │
│   │              :8000 inside               │              │
│   │                                         │              │
│   │   ┌─── compose bridge network ──┐       │              │
│   │   │   migrate ──► app ──► db    │       │              │
│   │   │   (one-shot)         :5432  │       │              │
│   │   │                       │     │       │              │
│   │   │            pgdata (named volume)    │              │
│   │   │                       │     │       │              │
│   │   └───────────────────────┼─────┘       │              │
│   │                           ▼             │              │
│   │              EBS gp3 root volume        │              │
│   │              (8 GiB, persists           │              │
│   │               across Stop/Start)        │              │
│   └─────────────────────────────────────────┘              │
│                                                            │
└────────────────────────────────────────────────────────────┘
```

**Key differences from local-dev:**

- **App is published on host port 80**, not 8000. Users hit `http://3.109.34.168/<code>` without specifying a port. Inside the container the app still listens on 8000; only the host-side mapping changes.
- **Postgres is NOT port-mapped on the host.** Inter-container communication via the compose network is sufficient; nothing exposes 5432 to the internet. Defense in depth — even if the security group is misconfigured, port 5432 isn't bound on the host.
- **Image source is ECR**, not a locally-loaded tar or a `build: .` directive. `docker-compose.prod.yml` references `546201496354.dkr.ecr.ap-south-1.amazonaws.com/fluxurl:latest`. EC2 pulls from ECR using credentials issued to the attached instance role.
- **`BASE_URL` is set explicitly** to `http://3.109.34.168` so generated short URLs reference the public Elastic IP, not the container's internal hostname. `BASE_URL` is a required environment variable — the app fails to start if it is missing (no default).
- **Containers have `restart: unless-stopped`** on `app` and `db`. They auto-recover from crashes and from EC2 reboot/Stop/Start. `migrate` deliberately has no restart policy (one-shot service; exiting cleanly is success).

**Persistence:**

- The `pgdata` named volume lives on the EC2 instance's 8 GiB EBS gp3 root volume. EBS survives instance Stop/Start.
- No backups. EBS volume loss is unrecoverable. Phase 7 (RDS) introduces automated backups.

**Public address:**

- The Elastic IP (`3.109.34.168`) is allocated from AWS's pool and associated with the instance. It does *not* change on Stop/Start, which is the property that makes short URLs durable.

**Image distribution (Phase 4):**

ECR is the canonical source of the runtime image. The laptop and the EC2 instance both interact with ECR; neither talks directly to the other.

- **Laptop → ECR (push):** authenticates to ECR using IAM user credentials (`saurabh-admin`), build the image locally, tag it with the ECR repository URI, and run `docker push`. Only changed layers transfer over the wire (deduplication is automatic via the OCI registry protocol).
- **EC2 ← ECR (pull):** the EC2 instance authenticates to ECR using *temporary credentials* issued by AWS via the instance metadata service. These credentials originate from the `fluxurl-ecr-pull-role` IAM role attached to the instance. The role's permissions policy (`AmazonEC2ContainerRegistryReadOnly`) limits the instance to pull-only operations — it cannot push, delete repositories, or access other AWS services. Credentials are rotated automatically by AWS every few hours; no AWS keys are stored anywhere on the EC2 disk.

This decoupling is the architectural payoff of Phase 4. The laptop has no awareness of where the image is consumed; EC2 has no awareness of where the image was built. Both ends communicate through the registry. Phase 5 will replace the laptop with a CI/CD runner and the manual SSH deploy with a CI-triggered pull — the registry seam stays untouched.

**Deployment process (manual, end of Phase 4):**

1. **Laptop:** `docker build -t fluxurl:latest .`
2. **Laptop:** `docker tag fluxurl:latest <ecr-uri>/fluxurl:latest`
3. **Laptop:** `docker push <ecr-uri>/fluxurl:latest`
4. **EC2 (over SSH):** `aws ecr get-login-password --region ap-south-1 | docker login --username AWS --password-stdin <ecr-uri>`
5. **EC2:** `docker compose -f docker-compose.prod.yml pull`
6. **EC2:** `docker compose -f docker-compose.prod.yml up -d --force-recreate`

Phase 5 (GitHub Actions) automates steps 1-3 on every push to `main`, and replaces steps 4-6 with an SSH-triggered remote deploy.

**What is *not* in this topology yet, and where it shows up:**

- **No reverse proxy (Nginx)** — Phase 9
- **No managed database** — `postgres:16` runs as a sibling container; RDS replaces it in Phase 7
- **No TLS / custom domain** — Phase 9
- **No automated deploys** — every code change requires the manual flow above; Phase 5 (GitHub Actions) automates it
- **No ECR lifecycle policy** — old images accumulate in ECR over time; lifecycle rules (e.g., "keep last 10 images") arrive when storage cost matters

---

## 6. Failure modes

What can break, what happens when it does, what's acceptable for v1. Updated as new failure modes are discovered during build.

| Failure | What happens | v1 acceptable? |
|---|---|---|
| Postgres container down | All requests return 5xx. `restart: unless-stopped` brings it back automatically within seconds if it crashed. | Yes — automatic recovery for crashes; manual intervention only for data-level issues |
| EC2 reboot | Brief outage (~30 s during reboot). `restart: unless-stopped` auto-starts `app` and `db` when Docker daemon comes back up. Public IP unchanged. | Yes |
| EC2 Stop/Start | Same as reboot for containers. Public IP stays (Elastic IP attached). | Yes |
| Elastic IP released or detached | Every existing short URL silently breaks. No internal recovery path. | Operational discipline — release only on permanent shutdown |
| EBS volume corruption / loss | Permanent data loss, no recovery path | Yes — explicitly accepted, Phase 7 (RDS) fixes |
| Home IP changes (developer) | SSH stops working until security group rule updated to new IP | Yes — 30-second console fix |
| ECR unreachable during deploy | Deploy fails at the `docker pull` step; existing running containers unaffected | Yes — fall back to running version, retry deploy |
| ECR auth token expired (12 h) | `docker pull` returns 401. Re-run `aws ecr get-login-password \| docker login` to refresh. | Yes — well-understood manual fix; Phase 5 CI handles auth per-deploy |
| IAM role detached from instance | EC2 loses ECR pull permission; existing running containers unaffected | Operational discipline — role attachment is configuration, not runtime |
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
- **Custom domain / TLS** — service is reached by raw Elastic IP. Phase 9 introduces a domain + Let's Encrypt.
- **Automated deployment** — manual SSH-and-compose flow. Phase 5 introduces GitHub Actions CI/CD.
- **Immutable image tags** — Phase 4 uses `:latest` only; tag-level rollback isn't possible. See [ADR 0011](decisions/0011-image-tagging-strategy.md). Phase 5 adds git-SHA-tagged builds.
- **ECR lifecycle policies** — old images accumulate; no auto-cleanup. Phase 5 follow-up once accumulation matters.
- Kubernetes / ECS / Fargate / Lambda
- CloudFront / Route 53 / API Gateway
- Redis, Celery
- Multi-environment (single env only)

---

## 9. Open questions and known limitations

**Resolved during Phases 1–4:**

- ~~**Primary key for `urls`**~~ → `short_code` as natural PK. See [ADR 0005](decisions/0005-primary-key-for-urls-table.md).
- ~~**URL validation strictness**~~ → Pydantic `HttpUrl` validates format (requires scheme + host). Accepts `http` and `https`; rejects relative URLs and malformed schemes.
- ~~**Max length of `long_url`**~~ → Pydantic `HttpUrl` enforces 2083-char limit (browser standard). No additional app-level check.
- ~~**404 behavior**~~ → Plain JSON `{"detail": "short code not found"}`. Same message for invalid shape and valid-but-missing (prevents information leakage).
- ~~**Container restart policy**~~ → `restart: unless-stopped` applied to `app` and `db` in `docker-compose.prod.yml`. `migrate` deliberately excluded (one-shot service). Verified by EC2 reboot test.
- ~~**`BASE_URL` default behavior**~~ → Now required at startup; no default. Missing `BASE_URL` raises a Pydantic `ValidationError` at startup. Migrate service must also receive `BASE_URL` because it imports the same `Settings` class (alembic/env.py calls `get_settings()`).
- ~~**Image distribution mechanism**~~ → ECR (Phase 4). EC2 authenticates via instance role; no AWS keys on the box. See [ADR 0011](decisions/0011-image-tagging-strategy.md) for tagging strategy.

**Outstanding:**

- **Reserved short codes** — generator does not exclude codes that collide with static routes (`shorten`, `docs`, `redoc`, `openapi.json`, `healthz`). Probability is vanishingly small (1 in 62^7), but the principle is unprotected. Phase 5 follow-up before automation amplifies the risk.
- **Image tag rollback path** — Phase 4 uses `:latest` only, so rollback requires identifying a previous manifest digest in ECR and re-pulling by digest. Phase 5 introduces git-SHA-tagged builds for tag-level rollback.
- **Alembic config coupling** — `alembic/env.py` calls `get_settings()`, which forces every required env var (including `BASE_URL`) into the migrate service's environment, even though migrate doesn't use them. Should be decoupled so migrate only reads `DATABASE_URL` directly from `os.environ`. Phase 6 cleanup candidate.