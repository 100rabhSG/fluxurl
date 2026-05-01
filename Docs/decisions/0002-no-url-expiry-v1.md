# ADR 0002: No URL expiry in v1

- **Status:** Accepted
- **Date:** 2026-05-01
- **Phase:** 0.5 (v1 HLD sketch)

## Context

URL shorteners commonly support expiry: a short URL stops resolving after some date. This protects against indefinite storage growth and reduces the surface area for stale or abusive links.

We have to decide whether v1 supports expiry at all. The relevant constraints:

- v1 is a learning project on the AWS free tier with one EC2 box and one Postgres container.
- Storage growth at v1 scale is ~1 GB/year (5k writes/day * 365 * ~500 B).
- Adding expiry pulls in a background job, a soft-delete vs hard-delete decision, and a UX distinction between "expired" and "never existed."

## Options considered

### Option A: No expiry. URLs are permanent.

- **Pros:**
  - Zero operational complexity. No background job, no scheduler, no cron-on-EC2 question.
  - No UX ambiguity - a short code either resolves or doesn't (404).
  - Storage cost is negligible at v1 scale (~1 GB/year).
- **Cons:**
  - Storage grows monotonically forever.
  - Abuse / takedown can only be handled by manual deletion.

### Option B: Fixed expiry for everyone (e.g., 30 days).

- **Pros:**
  - Bounds storage growth.
- **Cons:**
  - Requires a background deletion job (whole new component for v1).
  - Forces a new error case: "expired" vs "not found" - do we tell the user, or just return 404?
  - At v1 scale the storage we'd save is not worth the complexity.

### Option C: Per-URL TTL (user picks expiry at create time).

- **Pros:** Most flexible.
- **Cons:** Adds an API parameter, adds a `expires_at` column, *and* still needs the deletion job. All the cost of Option B plus a feature decision.

## Decision

**No expiry in v1 (Option A).**

The operational and UX cost of any expiry option is real; the storage saved is trivial at v1 scale. We're not losing anything we can't add later, and adding it later is straightforward (new column, new job).

## Consequences

- **Easier:** No background job needed in v1. No "expired vs 404" UX question. Insert and lookup paths stay maximally simple.
- **Harder:** If storage or compliance ever becomes a real concern (DMCA takedowns, GDPR, abuse), we'll need to add a deletion mechanism. That's a known future task, not a hidden one.
- **To revisit:** When storage growth, abuse load, or compliance pressure forces it. Likely Phase 6+ when analytics is added (since an analytics retention policy might want to align with URL retention).

## Notes

- This is a *scope cut*, but it earns an ADR because it has a real rejected alternative (a fixed-expiry model is genuinely common in the wild). Scope cuts that *don't* earn an ADR (e.g., "no auth in v1," "no rate limiting in v1") are tracked in `HLD.md` section 8.
- Filter rule for future decisions: ADR if you genuinely weighed and rejected an alternative; just a scope cut if you decided not to build it yet.
