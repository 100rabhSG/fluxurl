# ADR 0011: Image tagging strategy — `:latest` for v1, hybrid in Phase 5

**Status:** Accepted
**Date:** 2026-05-15
**Phase:** 4 (ECR + IAM instance role)

## Context

With ECR introduced in Phase 4, images are now stored in a registry, addressable by tag. Every push needs at least one tag. The choice of tagging strategy is a real decision — it shapes how rollback works, how deployments are audited, how `docker-compose.prod.yml` references the image, and what CI/CD will look like in Phase 5.

Three candidates:

1. **`:latest` only** — every push overwrites the `latest` tag. Simple. No history at the tag level.
2. **Git commit SHA only** — every push tags with the build's git SHA. Immutable. Each image traceable to an exact commit. No human-friendly "current" pointer.
3. **`:latest` + git SHA (hybrid)** — every push gets two tags. Combines the convenience of `:latest` with the immutability of a SHA-tagged history.

The hybrid is the industry-standard production-grade approach. The question is whether to adopt it *now* (Phase 4, manual deploys) or *later* (Phase 5, when CI/CD is in place).

## Decision

**Phase 4: use `:latest` only.**
**Phase 5: switch to hybrid (`:latest` + git SHA) as part of the CI/CD ADR.**

## Rationale

Manual SHA tagging is error-prone. The workflow becomes:

```
$sha = git rev-parse --short HEAD
docker build -t fluxurl:latest -t fluxurl:$sha .
docker tag fluxurl:$sha <ecr-url>/fluxurl:$sha
docker tag fluxurl:latest <ecr-url>/fluxurl:latest
docker push <ecr-url>/fluxurl:$sha
docker push <ecr-url>/fluxurl:latest
```

Six commands instead of three. One forgotten `$sha` capture and the rollback strategy is silently broken. One forgotten tag and auditability degrades.

In CI/CD, the same thing is trivial — `${{ github.sha }}` is already in scope, the build script tags both atomically, no chance of human error. The discipline becomes free.

The general principle: **don't adopt rigor before you have the tooling to make it cheap.** Manual SHA tagging in Phase 4 would erode itself; CI-driven SHA tagging in Phase 5 will pay off forever.

This is not deferring "the right answer" out of laziness — it's recognizing that the right answer is *the right answer in the right context*. The right context for SHA tagging is automation.

## Consequences

**Positive:**
- Phase 4 deploys stay simple: build, tag once, push.
- `docker-compose.prod.yml` references `<ecr-url>/fluxurl:latest` — one tag to track.
- No risk of broken rollback contract from forgotten manual tags.

**Negative / accepted risks during Phase 4:**
- No tag-level rollback. Rolling back requires identifying the previous manifest digest in ECR (`aws ecr describe-images`), then either re-pulling by digest or re-tagging.
- No audit trail at the tag level. "What was running when X bug appeared?" requires digest archaeology, not tag history.
- Anyone with push access to ECR can silently replace what `:latest` points at. For a solo project, this is acceptable; for a team, it would be a real risk.

The risks are bounded by the short duration of Phase 4 (manual deploys, low push frequency, single operator). Phase 5 mitigates all of them by introducing SHA tagging in CI.

## Alternatives considered

**Hybrid now (Option B during the discussion):** rejected because manual SHA tagging is error-prone. The auditability gained would be unreliable, undermining the point.

**Immutable tags only (no `:latest`):** rejected for v1. Requires `docker-compose.prod.yml` to reference a specific SHA, which would need updating on every deploy. Tolerable with CI; painful without.

**Semantic versioning (`v1.0.0`, etc.):** rejected for v1. Useful when releases have clear human-facing version boundaries; overkill for a single-developer project with no versioned releases. May revisit if/when fluxurl reaches public-facing v1.0.

## Follow-ups

- **Phase 5 ADR (CI/CD):** switch to hybrid tagging. CI workflow tags every build with both `:latest` and `:${git-sha-short}`. `docker-compose.prod.yml` continues to reference `:latest`; the SHA tag becomes the rollback mechanism.
- **Phase 5 + ECR lifecycle policy:** once SHA tags accumulate, configure ECR to auto-delete images older than N days or beyond the most recent K versions, to control storage cost.
- **Future consideration:** switching `docker-compose.prod.yml` to reference SHAs directly (no `:latest`) — the production-grade pattern. Worth revisiting if multi-environment deploys appear.