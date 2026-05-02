# ADR 0001: Short code length - 7 characters

- **Status:** Accepted
- **Date:** 2026-05-01
- **Phase:** 0.5 (v1 HLD sketch)

## Context

Fluxurl generates a short code per submitted URL. The code length determines two things:

1. **Keyspace size** (how many distinct codes can ever exist).
2. **Collision rate at insert time** as the keyspace fills (random generation with uniqueness check).

The alphabet is base62 (`[A-Za-z0-9]`, 62 symbols), chosen separately for URL-safety. The question here is purely *length*.

V1 scale target is 5,000 new URLs/day (~1.8M/year).

## Options considered

### Option A: 5 characters (62^5 ≈ 916M)

- **Pros:**
  - Shortest practical code; nicest for users.
  - 916M total codes covers ~458 years at our growth rate (~2M/year).
- **Cons:**
  - Collision rate at insert grows fast as the keyspace fills. By the time ~200M codes exist, each random generation has a ~22% chance of hitting an existing code, forcing many retries.
  - A length migration later is genuinely painful (existing short URLs are public, can't be changed; you end up with two coexisting code lengths forever).

### Option B: 7 characters (62^7 ≈ 3.5T)

- **Pros:**
  - Collision rate stays vanishingly small even at 1000x our growth.
  - Two extra characters cost the user almost nothing visually.
  - No realistic scenario where we run out of codes.
- **Cons:**
  - Slightly longer URL than option A. Negligible at this scale.

### Option C: 8+ characters

- **Pros:** Even more headroom.
- **Cons:** No additional benefit over 7 chars at any plausible scale; just longer URLs.

## Decision

**7 characters.**

The cost of two extra characters (vs option A) is essentially zero. The cost of being wrong about length is a painful migration of public URLs that can never be undone cleanly. When the cost of a safety margin is negligible, take it.

## Consequences

- **Easier:** No collision storm to worry about even as the table grows.
- **Harder:** Nothing material.
- **To revisit:** Only if scale assumptions are wildly wrong (e.g., we suddenly need to generate billions of URLs/day).

## Notes

- The lesson encoded here: **when the cost of a safety margin is negligible, take it.**
