# ADR 0003: Redirect status code — 302 (or 307)

- **Status:** Accepted
- **Date:** 2026-05-02
- **Phase:** 0.5 (v1 HLD sketch)

## Context

`GET /{short_code}` returns an HTTP redirect to the long URL. We have to pick a status code. The user-visible behaviour is identical at click time — the browser follows the redirect either way. The difference is what the browser (and intermediate caches) does on the **next** request for the same short URL.

| Code | Meaning | Browser behaviour next time |
|---|---|---|
| **301 Moved Permanently** | "Resource permanently moved. Future requests should go directly to the new location." | Browser caches aggressively. Next click on the same short URL may skip our server entirely. |
| **302 Found** / **307 Temporary Redirect** | "Resource is temporarily at this location. Keep asking the original URL." | Browser does not cache. Every click goes through our server. |

The non-obvious consequence: this is really about *who pays for the lookups* — the browser cache, or our server.

**Why this matters for Fluxurl specifically:** Phase 6 introduces click analytics. Click analytics requires every click to hit our server. A 301 in v1 silently breaks analytics in Phase 6 — browsers that cached the 301 will keep bypassing us, and the count of "clicks" will be a massive undercount with no retroactive fix.

## Options considered

### Option A: 302 (or 307) from day one

- **Pros:**
  - Every click hits our server. Phase 6 analytics work correctly without a status-code switch.
  - No long tail of cached 301s to deal with later.
  - Load cost is trivial at v1 scale (~1.2 reads/sec).
- **Cons:**
  - Slightly more server load than 301. Negligible at our scale.

### Option B: 301 in v1, switch to 302 in Phase 6

- **Pros:**
  - Lower server load in v1 (browsers cache after the first click).
- **Cons:**
  - The switch is messy: browsers that already cached the 301 will keep bypassing us until their cache expires (weeks, sometimes indefinite).
  - The "load reduction" benefit is meaningless at v1 throughput.
  - Future-you pays for past-you's optimisation.

### Option C: 307 specifically (vs 302)

- **Pros:** Stricter HTTP method preservation than 302. The "modern correct" choice.
- **Cons:** For a GET-only redirect (which is all we do), 302 and 307 are functionally equivalent.

## Decision

**302 from day one.** (307 would be equally fine; not worth agonising.)

The supposed "load reduction" benefit of 301 is meaningless at our scale, and the future cost of switching is real. Pick the consistent answer now so analytics in Phase 6 just works.

## Consequences

- **Easier:** Phase 6 click analytics counts every click correctly. No coordination needed between the redirect change and the analytics rollout.
- **Harder:** Every click costs us a DB lookup. At v1 scale (~1.2 reads/sec peak ~12/sec) this is trivially affordable.
- **To revisit:** If read load ever grows enough that bypassing the server for repeat clicks would meaningfully reduce cost. At that point we'd add a cache (Phase 10) rather than switch back to 301 — caching is the right knob, not the status code.

## Notes

- bit.ly, t.co, and similar services historically used 301 with tight cache-control headers, but many have moved toward 302 precisely because they want every click for analytics. There's no universal right answer — it depends on what the service prioritises.
- Lesson encoded: **a decision in v1 can silently break a feature in a later phase.** Always ask "what future feature relies on this behaviour?" before locking in.
