# Fluxurl — High-Level Design

> Living document. Updated as each phase changes the architecture. See `Docs/decisions/` for the *why* behind each choice.

---

## 1. System context

What Fluxurl is, who uses it, what's outside the system boundary.

*To be filled in Phase 1.*

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

What works today, what breaks at scale, what we'd change first.

*To be filled at v1 Done — Take Stock.*

## 8. Out of scope for v1

Tracked here so reviewers know what was *deliberately* deferred:

- Kubernetes / ECS / Fargate / Lambda
- CloudFront / Route 53 / API Gateway
- Redis, Celery
- Multi-environment (single env only)
- Custom domain / TLS (Phase 9)

## 9. Open questions

Things not yet decided. Each should turn into an ADR when resolved.
