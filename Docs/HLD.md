# Fluxurl — High-Level Design

> Living document. Updated as each phase changes the architecture. See `Docs/decisions/` for the *why* behind each choice.

---

## 1. System context

Fluxurl is a public URL shortener. Anyone on the internet can submit a long URL and receive a short code; anyone with the short code can be redirected to the original URL.

**Functional requirements (v1):**

- **Shorten:** `POST /shorten` accepts a long URL, returns a generated short code (and full short URL).
- **Redirect:** `GET /{short_code}` returns a 302 redirect to the original URL. Codes are unguessable, so lack of auth doesn't make them publicly enumerable.
- **No authentication** for either operation. Anonymous users can shorten and redirect.
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

**Availability and durability targets (v1):**

- **Availability:** target ~99% (single EC2 instance, no redundancy). Higher targets would require load balancing, health checks, and multi-AZ deployment — out of scope for v1.
- **Durability:** no formal application-level target. Data lives on a single EBS volume with no backups. EBS itself provides ~99.8–99.9% annual durability. A volume failure or accidental deletion is unrecoverable in v1. Phase 7 (RDS migration) introduces automated backups.

## 2. Components

The pieces that make up the system and what each one is responsible for.

*To be filled in Phase 1, expanded in later phases.*

- **API service** — TBD
- **Database** — TBD
- **Container registry** — TBD (Phase 4)
- **CI/CD pipeline** — TBD (Phase 5)

## 3. Data model

Tables, key columns, relationships, indexes.

*To be filled in Phase 1.*

## 4. Request flows

Step-by-step for the main paths:

- `POST /shorten` — TBD
- `GET /{short_code}` — TBD

*To be filled in Phase 1.*

## 5. Deployment topology

Where each component runs, how they talk, network boundaries.

*Local dev added in Phase 2. EC2 added in Phase 3. ECR added in Phase 4.*

## 6. Failure modes

What can break, what happens when it does, what's acceptable for v1.

*To be filled progressively. Examples: DB down, EC2 reboot, ECR auth failure, deploy mid-request.*

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

- Kubernetes / ECS / Fargate / Lambda
- CloudFront / Route 53 / API Gateway
- Redis, Celery
- Multi-environment (single env only)
- Custom domain / TLS (Phase 9)

## 9. Open questions

Things not yet decided. Each should turn into an ADR when resolved.