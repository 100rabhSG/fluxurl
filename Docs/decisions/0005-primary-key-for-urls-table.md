# ADR 0005: Primary key for `urls` table — `short_code`

- **Status:** Accepted
- **Date:** 2026-05-04
- **Phase:** 1 (app + schema)

## Context

`urls` is the core v1 table — a pure key-value mapping (`short_code` → `long_url` + `created_at`). The access pattern is 100% lookup by `short_code`: both endpoints (`POST /shorten` insert, `GET /{code}` lookup) hit the table by `short_code`. Short codes are system-generated and immutable by design — changing one would break every URL in the wild that points to it. Phase 6 will add a `clicks` table that needs a foreign key reference back to `urls`. The decision is which column should be the primary key.

## Options considered

### Option A: `BIGINT` auto-increment surrogate key

A separate `id BIGINT GENERATED ALWAYS AS IDENTITY` column, with a unique index on `short_code`.

- **Pros:**
  - Universal default — what most ORMs and tutorials reach for first.
  - Sequential inserts give good index locality (less B-tree fragmentation).
  - Small (8 bytes) and cheap to reference from FKs.
- **Cons:**
  - Creates an index on a column nothing actually queries by. Pure overhead at our access pattern.
  - Adds an "internal id" concept the system doesn't otherwise need — extra column to track, two identifiers to reason about.

### Option B: `UUID` surrogate key

A `UUID` column as PK, with a unique index on `short_code`.

- **Pros:**
  - Generatable client-side / before insert.
  - Avoids leaking row insertion order externally.
- **Cons:**
  - Solves problems we don't have — we never expose internal id, and we have no distributed-generation requirement.
  - 16 bytes per row vs 8.
  - Random UUIDs cause index fragmentation (random insert positions in the B-tree).
  - No reason to pick it over BIGINT here, let alone over `short_code`.

### Option C: `short_code` as PK (natural key)

`short_code VARCHAR(7) PRIMARY KEY`. No separate `id` column.

- **Pros:**
  - Single index, and it's the one actually used by every query.
  - Smallest schema — one identifier, not two.
  - FK references in `clicks` are smaller (~7 bytes vs 8/16) and queries like "show me clicks for code X" hit `clicks` directly without a JOIN through `urls`.
- **Cons:**
  - Goes against the "always use a surrogate key" reflex, so it needs justifying (see Decision).
  - Any future schema-level change to `short_code` (length, alphabet) becomes a wider migration.

## Decision

**`short_code` as the primary key (Option C). No separate `id` column.**

The standard objection to natural keys is that they eventually need to change, and changing a natural PK is a database-wide migration nightmare. That objection doesn't apply here: `short_code` is **immutable by product design** — changing one would break the URL in the wild, which is the entire thing Fluxurl promises not to do. So the usual fragility argument is neutralised.

Given that, the access pattern decides it. Every query is a single-key lookup by `short_code`. A surrogate PK would create an index nothing reads from — pure overhead. The conventional decoupling argument ("internal id stays stable, external id can change") doesn't apply either: we never expose an internal id, and we never need to change `short_code` internally.

## Consequences

- **Schema is genuinely simpler.** One identifier per row, not two. Less to reason about when reading the model.
- **One fewer index per row** — saves storage and slightly speeds up inserts. Negligible at v1 scale; free win.
- **`clicks.short_code` will be `VARCHAR(7) REFERENCES urls(short_code)`**, not `BIGINT REFERENCES urls(id)`. Slightly less conventional but valid Postgres, and it means click queries by short code don't need a JOIN.
- **No internal vs external identifier separation.** If we ever need to expose internal row identity (admin tools, audit logs that survive a code change), there's nothing other than `short_code` to use. Acceptable for v1; flag if it ever becomes a constraint.
- **Schema-level change to `short_code` (e.g., 7 → 8 chars, different alphabet) is a wider migration** touching every row and every dependent FK. Acceptable, because such a change is *also* a product-level breaking change — existing short URLs in the wild would still need to resolve — so the schema work is the smaller concern.
- **To revisit:** if we ever introduce a feature that needs a stable internal identity independent of `short_code` (e.g., letting users "rotate" a short code while keeping analytics history). At that point, add a surrogate column; don't retrofit it pre-emptively.

## Notes

- Why not UUID specifically? UUIDs are the right answer when you need distributed ID generation or want to hide insertion order. Neither applies — `short_code` is already random (per [ADR 0004](0004-short-code-generation-algorithm.md)) and already opaque externally.
- Why not just have both (surrogate PK + unique `short_code`)? That's the "safe default" choice. It costs an extra index and an extra concept on every row, in exchange for flexibility we have a concrete reason not to need. Adding a surrogate later is a one-migration job; carrying one we don't use is a permanent tax.
