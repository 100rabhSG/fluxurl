# Fluxurl — Low-Level Design

> Living document. White-box view of how the code is wired. Updated phase-by-phase as you build. Companion to `HLD.md` (black-box) and `Docs/decisions/` (why).

---

## 1. Module layout

```
app/
├── __init__.py
├── main.py          # FastAPI app instance, static routes (/, /healthz), router mounting
├── config.py        # pydantic-settings Settings class, @lru_cache get_settings()
├── api/
│   ├── __init__.py
│   └── urls.py      # POST /shorten, GET /{short_code} handlers
├── db/
│   ├── __init__.py
│   └── session.py   # async engine, session factory, get_session() dependency
├── models/
│   ├── __init__.py  # re-exports Base + Url (so Alembic discovers them)
│   ├── base.py      # DeclarativeBase
│   └── url.py       # Url ORM model
├── schemas/
│   ├── __init__.py  # re-exports all schemas
│   └── url.py       # ShortenRequest, ShortenResponse, ErrorResponse
└── services/
    ├── __init__.py
    └── shortener.py # generate_short_code(), ALPHABET, DEFAULT_LENGTH
```

**Ownership rules:**
- `api/` knows about schemas and services — never imports ORM internals beyond the model class.
- `services/` is pure logic — no DB, no HTTP, no FastAPI imports.
- `db/` owns the engine and session lifecycle — nothing else creates sessions.
- `schemas/` defines the API boundary — every request/response body crosses through a Pydantic model.

---

## 2. Request lifecycle

A single `POST /shorten` request, step by step:

```
Client → Uvicorn → FastAPI router → shorten() handler
                                         │
                                         ├─ Depends(get_session) → yields AsyncSession
                                         ├─ Pydantic validates body → ShortenRequest
                                         ├─ generate_short_code() → 7-char string
                                         ├─ session.add(Url(...))
                                         ├─ await session.commit()
                                         │       └─ IntegrityError? → rollback, retry
                                         ├─ return ShortenResponse(...)
                                         │
                                         └─ session.__aexit__() closes session (via `async with`)
```

Key points:
- **Dependency injection** happens before the handler body runs. `get_session()` is an async generator — FastAPI `await`s it, gets the session, runs the handler, then resumes the generator (which closes the session).
- **Pydantic validation** also happens before the handler. If the body fails validation, FastAPI returns 422 automatically — the handler never executes.
- **One session per request.** The session is created at request start and closed at request end, regardless of success or failure.

---

## 3. DB session and transaction model

**Creation:** `app/db/session.py` creates a module-level `engine` (connection pool) and `async_session_factory`. The `get_session()` async generator yields one session per call.

**Scope:** Per-request. FastAPI's `Depends(get_session)` creates a new session for each incoming request. No session sharing across requests.

**Commit:** Explicit. The handler calls `await session.commit()` after a successful insert. There is no auto-commit — if the handler raises before committing, the session closes without persisting anything.

**Rollback:** On `IntegrityError` (collision), the handler explicitly calls `await session.rollback()` to reset the session state, then retries with a new code. If an unhandled exception propagates, the session closes without commit — effectively a rollback.

**`expire_on_commit=False`:** Required in async mode. Without it, SQLAlchemy would try to lazy-load attributes after commit, which is forbidden in async (no implicit I/O). See [ADR 0006](decisions/0006-async-sqlalchemy.md).

---

## 4. Short-code generation

**Module:** `app/services/shortener.py`

**Contract:**
```python
def generate_short_code(length: int = 7) -> str:
    """Return a cryptographically random base62 string of `length` characters."""
```

**Alphabet:** `0-9 A-Z a-z` (62 characters). URL-safe without encoding. See [ADR 0001](decisions/0001-short-code-length.md).

**RNG:** `secrets.choice()` — cryptographically secure. Codes are unguessable; no sequential patterns to enumerate.

**Collision handling (in the handler, not in this function):**
1. Generate code.
2. Insert row.
3. If `IntegrityError` (PK collision) → rollback, regenerate, retry.
4. Max 5 retries. If all 5 collide → raise `HTTPException(500)`.

See [ADR 0004](decisions/0004-short-code-generation-algorithm.md) for why optimistic insert beats check-then-insert.

---

## 5. Error taxonomy

| Scenario | Where detected | HTTP code | Response body |
|---|---|---|---|
| Request body fails Pydantic validation (bad URL, missing field) | FastAPI/Pydantic (before handler) | 422 | `{"detail": [{type, loc, msg, input}]}` |
| `short_code` wrong length or invalid chars | Handler fast-fail check | 404 | `{"detail": "short code not found"}` |
| `short_code` valid shape but no DB row | Handler, after DB lookup | 404 | `{"detail": "short code not found"}` |
| All collision retries exhausted | Handler, after retry loop | 500 | `{"detail": "Could not generate a unique short code; please retry."}` |
| Database unreachable | SQLAlchemy/asyncpg (unhandled) | 500 | FastAPI default `{"detail": "Internal Server Error"}` |

**Design choice:** the two 404 cases (invalid shape vs valid shape but missing) return the **same** error message deliberately. Different messages would let an attacker probe which code shapes are "real" vs "garbage" — information leakage.

---

## 6. Schemas (API boundary)

**`ShortenRequest`:**
```python
class ShortenRequest(BaseModel):
    url: HttpUrl  # Pydantic validates format + max 2083 chars
```

**`ShortenResponse`:**
```python
class ShortenResponse(BaseModel):
    short_code: str   # "6qmtOsk"
    short_url: str    # "http://localhost:8000/6qmtOsk"
    long_url: str     # "https://example.com/..."
```

**`ErrorResponse`:**
```python
class ErrorResponse(BaseModel):
    detail: str       # Human-readable error message
```

**Boundary rule:** Raw ORM `Url` objects never leave the handler. The handler constructs a `ShortenResponse` from the fields it needs. This decouples the DB schema from the API contract — the model can change (add columns, rename internally) without breaking clients.

---

## 7. Container internals

*To be filled in Phase 2.*

## 8. On-host layout (EC2)

*To be filled in Phase 3.*

## 9. ECR auth flow

*To be filled in Phase 4.*

## 10. CI/CD workflow structure

*To be filled in Phase 5.*
