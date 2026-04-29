# Fluxurl — Low-Level Design

> Living document. White-box view of how the code is wired. Updated phase-by-phase as you build. Companion to `HLD.md` (black-box) and `Docs/decisions/` (why).

---

## 1. Module layout

What lives under `app/` and what each module owns. One concern per module.

*To be filled in Phase 1.*

```
app/
├── api/        # ?
├── db/         # ?
├── models/     # ?
├── schemas/    # ?
└── main.py     # ?
```

## 2. Request lifecycle

How a single HTTP request flows through the code: routing → dependency injection → DB session → transaction → response serialization.

*To be filled in Phase 1.*

## 3. DB session and transaction model

- Where the `AsyncSession` is created
- Scope (per-request) and how it's injected
- Where commits happen
- Rollback behavior on exceptions

*To be filled in Phase 1.*

## 4. Short-code generation

Function contract, alphabet, length, collision handling. The actual algorithm — pseudocode is fine.

*To be filled in Phase 1.*

## 5. Error taxonomy

Which exceptions get raised where, which HTTP status codes they map to, and how validation errors are surfaced.

| Error | Where raised | HTTP status | Response shape |
|---|---|---|---|
| TBD | TBD | TBD | TBD |

*To be filled in Phase 1.*

## 6. Schemas (API boundary)

Pydantic request and response models. The contract between the outside world and the app.

*To be filled in Phase 1.*

## 7. Container internals

Entrypoint, working dir, non-root UID, env-var contract, healthcheck command.

*To be filled in Phase 2.*

## 8. On-host layout (EC2)

Where the compose file lives, how containers restart, where logs go, how env vars are supplied.

*To be filled in Phase 3.*

## 9. ECR auth flow

How the EC2 box authenticates to ECR at pull time \u2014 credential chain, IMDS hop.

*To be filled in Phase 4.*

## 10. CI/CD workflow structure

Jobs, steps, where each step runs (GitHub-hosted runner vs EC2), secrets used, the deploy script's exact contract.

*To be filled in Phase 5.*
