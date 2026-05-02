# ADR 0004: Short code generation algorithm — random

- **Status:** Accepted
- **Date:** 2026-05-02
- **Phase:** 0.5 (v1 HLD sketch)

## Context

`POST /shorten` needs to produce a 7-character base62 short code for each new URL (length decided in [ADR 0001](0001-short-code-length.md)). Three families of algorithms exist for generating that code; we have to pick one.

## Options considered

### Option A: Random generation

Generate a random 7-char base62 string per request; insert; on unique-constraint conflict, retry.

- **Pros:**
  - Predictability is rare (codes can't be guessed from neighbours).
  - Concurrency-safe: two requests at the same time generate independently, no coordination needed.
  - Collision rate is vanishingly low at our chosen length (62^7 ≈ 3.5T).
- **Cons:**
  - Non-deterministic — same long URL submitted twice produces two different short codes. Not a feature we want anyway (see Option B).

### Option B: Hash-based

Hash the long URL (e.g., truncate a SHA-256) to produce the short code.

- **Pros:**
  - Deterministic — same long URL always maps to the same short code.
  - Collision rate similar to random at the same length.
- **Cons:**
  - **Determinism is not a feature here, it's a bug.** Two different users shortening the same long URL would get the same short code. That breaks privacy (one user can discover that another shortened the same URL) and breaks per-user analytics (clicks aren't separable).

### Option C: Counter-based (sequential)

Maintain a global counter; encode the current value as base62 for each new code.

- **Cons:**
  - **Highly predictable** — anyone can enumerate every short URL ever created by incrementing.
  - **Concurrency issues** — requires a single coordination point (DB sequence, Redis INCR, etc.) to hand out unique counter values. Adds a synchronisation bottleneck and a failure mode we don't otherwise have.
  - Not safe.

## Decision

**Random generation (Option A).**

It's the only option that's simultaneously concurrency-safe, hard to enumerate, and free of the determinism trap that Option B falls into. The collision concern that's normally cited against random generation is neutralised by our 7-char length choice (ADR 0001).

## Consequences

- **Easier:** No coordination point needed for code generation. Handlers are independent.
- **Harder:** Same long URL submitted twice produces two distinct short codes. We've decided that's correct behaviour, not a downside.
- **To revisit:** Only if scale grows enough that collision retries become a real cost — at which point the fix is longer codes, not a different algorithm.

## Notes

- Companion decision (collision-handling strategy: retry-on-conflict vs check-before-insert) is deferred to Phase 1, when the actual insert code is being written.

## Implementation notes (for Phase 1)

These don't change the algorithm choice, but they're decisions the implementation has to honour. Captured here so they're not re-litigated later.

### Use a cryptographically secure RNG

In Python, use `secrets.choice()` or `secrets.token_urlsafe()` — **not** `random.random()`.

`random` is a pseudo-random generator seeded predictably; an attacker who observes a few outputs can predict future codes. `secrets` is designed to resist that. This is a small change with no cost, and it eliminates a class of attack we'd otherwise have to argue about.

### Collision handling: optimistic, not pessimistic

Two reasonable shapes for the insert path:

- **Optimistic (chosen):** generate code → attempt insert → on unique-constraint violation, retry up to N times. The DB serialises the inserts, so this is race-condition-safe.
- **Pessimistic (rejected):** generate code → check if it exists → if not, insert. **Has a race condition:** two concurrent requests can both check, both find nothing, both insert. Don't do this.

Optimistic is the only correct approach. The retry cap (`N`) is a small Phase 1 detail — log and 500 if it's ever hit, since at our keyspace it would indicate a genuine bug, not normal operation.
