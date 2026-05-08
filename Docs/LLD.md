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

White-box view of the image that runs `app` and `migrate`. The black-box topology is in `HLD.md` §5.1.

### 7.1 Build shape

Two-stage Dockerfile (see [ADR 0008](decisions/0008-multistage-dockerfile.md)).

- **Stage 1 (builder)** — `python:3.12` (full image, has gcc/headers in case a dependency needs to compile). Creates an isolated venv at `/opt/venv` and `pip install`s `requirements.txt` into it.
- **Stage 2 (runtime)** — `python:3.12-slim` (see [ADR 0007](decisions/0007-base-image-python-slim.md)). Copies *only* the populated venv from the builder, then the application code.

What stays vs what is discarded:

| Kept in runtime | Discarded between stages |
|---|---|
| `/opt/venv` (installed packages) | gcc, build-essential, Python headers |
| `/app/app/`, `/app/alembic/`, `/app/alembic.ini` | pip cache, source tarballs |
| | Anything `.dockerignore` filtered out of the build context |

The `.dockerignore` file enforces what never enters the build context in the first place: `.git/`, `.venv/`, `.env`, `tests/`, `Docs/`, `__pycache__/`, IDE config.

### 7.2 Filesystem layout (runtime image)

```
/app/                  ← WORKDIR
├── app/               ← application code
├── alembic/           ← migration scripts
└── alembic.ini

/opt/venv/             ← isolated Python environment
├── bin/uvicorn
├── bin/alembic
└── lib/python3.12/site-packages/
```

`ENV PATH="/opt/venv/bin:$PATH"` puts the venv's `bin/` first on `PATH`, so `uvicorn` and `alembic` resolve to the venv versions without needing to `activate` it.

### 7.3 Identity

- Image build runs as `root` (Docker default). The runtime stage stays root through `COPY` so it can write into `/app`.
- A `useradd -u 1000 -m appuser` creates a non-root user with a fixed UID; `USER appuser` switches identity for the running process.
- Files COPY'd while root are owned by root but have default mode 644 / 755 — appuser can read code and execute binaries without owning them. The app does not write to disk at runtime, so write access is unneeded.
- Rationale and threat model in [ADR 0009](decisions/0009-non-root-container-user.md).

### 7.4 Env-var contract

The image declares a contract — the variables its process needs in its environment to run. The contract is *what* the app expects, not *how* it gets there.

| Variable | Required? | Read by | Behavior if missing |
|---|---|---|---|
| `DATABASE_URL` | **Yes** | `app/config.py` (pydantic-settings) | Fail-fast on startup with a Pydantic validation error (no default) |

**How the contract is satisfied per environment:**

| Environment | Mechanism |
|---|---|
| Host uvicorn (Phase 1, dev) | `.env` file in the working dir — pydantic-settings auto-loads it via `SettingsConfigDict(env_file=".env")` |
| Container via compose (Phase 2) | `.env` is excluded by `.dockerignore` and never enters the image. Compose's `environment:` block injects `DATABASE_URL` as an OS-level env var into the container; pydantic-settings reads `os.environ` |
| EC2 / ECR (Phase 3+) | Same image; supplied by whatever runs the container on the box (compose-on-EC2, systemd, `docker run -e`) |

The app code has no branch for "am I in a container?" — pydantic-settings reads from the environment regardless of how the value got there. The `.env` file is a host-only dev affordance, deliberately excluded from the image so secrets don't get baked in. Refusing to boot without `DATABASE_URL` is intentional: a wrong-but-present value silently connects to the wrong database; an absent one is loud.

### 7.5 Network surface

- `EXPOSE 8000` is **documentation only**. It does not publish the port to the host — it is a hint to readers and tools about which port the process listens on.
- The actual host-to-container mapping (`8000:8000`) is set in `docker-compose.yml`, not in the image. The image is unaware of how it will be reached.

### 7.6 Process model — one image, two commands

The image declares `CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]` as its default. Compose uses this directly for the `app` service. The `migrate` service overrides `command` with `["alembic", "upgrade", "head"]` — same image, different process. PID 1 is whichever binary `CMD` resolves to (no entrypoint script wraps it).

This is what makes the HLD's "deployable unit = image" claim concrete: the image is a self-contained tool that can run *either* the web server or the migration runner. Compose decides which.

## 8. On-host layout (EC2)

*To be filled in Phase 3.*

## 9. ECR auth flow

*To be filled in Phase 4.*

## 10. CI/CD workflow structure

*To be filled in Phase 5.*
