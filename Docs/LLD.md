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
    short_url: str    # "http://3.109.34.168/6qmtOsk" (constructed from BASE_URL + short_code)
    long_url: str     # "https://example.com/..."
```

**`ErrorResponse`:**
```python
class ErrorResponse(BaseModel):
    detail: str       # Human-readable error message
```

**Boundary rule:** Raw ORM `Url` objects never leave the handler. The handler constructs a `ShortenResponse` from the fields it needs. This decouples the DB schema from the API contract — the model can change (add columns, rename internally) without breaking clients.

The `short_url` is **constructed at response time**, not stored. The handler reads `BASE_URL` from settings and concatenates with `short_code`. This means the response shape adapts to wherever the service is deployed without any DB rewrite — at the cost of needing a correct `BASE_URL` in every environment (see §7.4).

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
| `BASE_URL` | **Yes** | `app/config.py` (pydantic-settings) | Fail-fast on startup with a Pydantic validation error (no default) |

**How the contract is satisfied per environment:**

| Environment | Mechanism |
|---|---|
| Host uvicorn (Phase 1, dev) | `.env` file in the working dir — pydantic-settings auto-loads it via `SettingsConfigDict(env_file=".env")` |
| Container via compose (Phase 2) | `.env` is excluded by `.dockerignore` and never enters the image. Compose's `environment:` block injects vars as OS-level env vars into the container; pydantic-settings reads `os.environ` |
| EC2 / ECR (Phase 3+) | Same image; supplied by whatever runs the container on the box (compose-on-EC2, systemd, `docker run -e`) |

The app code has no branch for "am I in a container?" — pydantic-settings reads from the environment regardless of how the value got there. The `.env` file is a host-only dev affordance, deliberately excluded from the image so secrets don't get baked in. Refusing to boot without required env vars is intentional: a wrong-but-present value silently connects to the wrong database (or generates wrong short URLs); an absent one is loud.

**Service-level consequence:** because `alembic/env.py` imports `get_settings()`, the `migrate` service needs *every* required env var, not just `DATABASE_URL`. This is why `BASE_URL` is set on the `migrate` service in `docker-compose.prod.yml` even though Alembic itself never uses it. A planned cleanup (HLD §9) is to have `alembic/env.py` read `DATABASE_URL` directly from `os.environ` instead of going through the full `Settings` class — that would decouple migrations from the app's full config surface.

### 7.5 Network surface

- `EXPOSE 8000` is **documentation only**. It does not publish the port to the host — it is a hint to readers and tools about which port the process listens on.
- The actual host-to-container mapping (`8000:8000` in dev, `80:8000` on EC2) is set in the compose file, not in the image. The image is unaware of how it will be reached.

### 7.6 Process model — one image, two commands

The image declares `CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]` as its default. Compose uses this directly for the `app` service. The `migrate` service overrides `command` with `["alembic", "upgrade", "head"]` — same image, different process. PID 1 is whichever binary `CMD` resolves to (no entrypoint script wraps it).

This is what makes the HLD's "deployable unit = image" claim concrete: the image is a self-contained tool that can run *either* the web server or the migration runner. Compose decides which.

---

## 8. On-host layout (EC2)

White-box view of what's on the EC2 instance and where. The black-box topology is in `HLD.md` §5.2.

### 8.1 OS and runtime

- **OS:** Ubuntu 22.04 LTS, default `ubuntu` user (UID 1000, member of `docker` group).
- **Docker:** installed from Docker's official APT repository (not Ubuntu's stale `docker.io`). Currently Docker 29.x with Compose v5.x bundled as a plugin (`docker compose`, not the legacy `docker-compose` binary).
- **AWS CLI:** installed from Ubuntu's APT repository (`awscli` package). Required for `aws ecr get-login-password` during deploys. No `~/.aws/credentials` file — the CLI uses the instance role's credentials automatically via the metadata service.
- **No other application runtime on the host.** No system Python service, no nginx, no systemd unit wrapping containers. The instance's sole job is to run Docker.

### 8.2 Filesystem layout

```
/home/ubuntu/                       ← SSH default working dir
├── docker-compose.prod.yml         ← scp'd from laptop; source of truth for what runs on this box
└── (no source code, no Dockerfile) ← image lives in Docker's store, not unpacked here

/var/lib/docker/                    ← Docker's data root (managed; don't poke directly)
├── overlay2/                       ← image layers (incl. ECR-pulled images)
├── containers/                     ← per-container metadata + logs
│   └── <id>/<id>-json.log          ← stdout/stderr captured here per container
└── volumes/
    └── ubuntu_pgdata/_data/        ← the named volume that holds Postgres data
        └── PG_VERSION, base/, pg_wal/, ... (Postgres's actual data directory)

/                                   ← root filesystem on the 8 GiB EBS gp3 root volume
                                       (everything above lives on this single disk)
```

The compose file on `/home/ubuntu/docker-compose.prod.yml` is the only project-specific file directly visible on the host. Everything else lives inside Docker's data root.

### 8.3 Image source

- **Phase 4 onward:** the image is pulled from ECR at `546201496354.dkr.ecr.ap-south-1.amazonaws.com/fluxurl:latest`. `docker-compose.prod.yml` references this full URI directly; compose triggers the pull on `up` if the local store doesn't have the requested digest, or if `docker compose pull` is invoked explicitly.
- **Docker's storage model is identical regardless of source.** A pulled-from-ECR image lives in `/var/lib/docker/overlay2/` the same way a `docker build`-built image does. Docker doesn't distinguish source — only content. Two routes can land at the same image (and de-duplicate to the same layers) by manifest digest.
- **The ECR pull is authenticated**, unlike Docker Hub pulls of public images. The host must complete `docker login` to ECR before `docker pull` works. Auth is covered in §9.

### 8.4 What runs and how

The full container set is defined in `docker-compose.prod.yml`:

| Service | Image | Restart | State |
|---|---|---|---|
| `db` | `postgres:16` (Docker Hub) | `unless-stopped` | Up, healthy |
| `migrate` | `<ecr-uri>/fluxurl:latest` | none | Exited (0) after migrations run |
| `app` | `<ecr-uri>/fluxurl:latest` | `unless-stopped` | Up |

The compose project name is `ubuntu` (derived from the directory the compose file is invoked from), which is why the named volume appears as `ubuntu_pgdata` in `/var/lib/docker/volumes/`.

**Restart policy reasoning:**
- `db` and `app` have `restart: unless-stopped` so they auto-recover from crashes and from EC2 reboot/Stop/Start. Docker daemon starts them when the OS comes up; if they crash, Docker restarts them with exponential backoff.
- `migrate` deliberately has *no* restart policy. It's a one-shot service that exits 0 after migrations complete. Attaching a restart policy would cause Docker to re-run it indefinitely.
- `unless-stopped` (not `always`) — the policy respects manual `docker compose down`, so operator-initiated stops aren't fought by Docker.

**Bringing the stack up:**

```bash
docker compose -f docker-compose.prod.yml up -d
```

The `-f` flag is required because the prod file isn't named `docker-compose.yml`. The `-d` (detached) flag is required because anything else ties container lifetimes to the SSH session.

**Recreate after env var or image changes:**

```bash
docker compose -f docker-compose.prod.yml up -d --force-recreate
```

Compose doesn't pick up changes to `environment:` blocks on an already-running container, and won't always re-pull a newer image with the same tag — `--force-recreate` ensures containers are torn down and rebuilt from current config and pulled images.

### 8.5 Persistence

- The `pgdata` named volume is mounted by `docker-compose.prod.yml` and physically lives at `/var/lib/docker/volumes/ubuntu_pgdata/_data/`.
- That path is on the EC2 instance's 8 GiB EBS gp3 root volume. EBS survives instance Stop/Start, so the data survives instance lifecycle events.
- **No backups.** Volume corruption, accidental deletion (`docker volume rm`), or EBS failure means total data loss with no recovery path. This is the most consequential v1 scope cut. Phase 7 (RDS) introduces automated backups.

### 8.6 Network

- Only ports 22 (SSH) and 80 (HTTP) are reachable from the internet, enforced by the EC2 security group.
- Inside the box, the compose bridge network gives each container an internal IP and DNS name. `app` reaches `db` at the hostname `db`, port 5432.
- **Postgres has no host port mapping in production.** This is intentional and different from local-dev — preventing port 5432 from being bound on the host, regardless of what the security group allows.
- The app container's port 8000 is mapped to host port 80, which is then exposed via the security group rule for HTTP.
- **Outbound HTTPS** is unrestricted by default. The instance reaches `*.amazonaws.com` (for ECR pulls and the metadata service is link-local) and `registry-1.docker.io` (for Postgres image pulls).

### 8.7 Logs

- Each container's stdout/stderr is captured by Docker's default `json-file` log driver and written to `/var/lib/docker/containers/<id>/<id>-json.log`.
- Read logs via `docker compose -f docker-compose.prod.yml logs -f app` (or any specific service). The `-f` follows in real time.
- **No log rotation configured.** Logs grow unbounded; on a t3.micro with 8 GiB EBS, this could fill the disk on a busy day. Acceptable at v1 traffic; revisit in Phase 8 or Phase 12.
- Logs do not currently flow to CloudWatch. Phase 8 introduces structured logging and CloudWatch integration.

### 8.8 Lifecycle behavior

| Event | Containers | Public IP | Data |
|---|---|---|---|
| `sudo reboot` from inside | Auto-restart (Docker daemon starts on boot; `restart: unless-stopped` brings up app+db) | Unchanged | Survives |
| Console "Reboot instance" | Auto-restart, same as above | Unchanged | Survives |
| Console "Stop" then "Start" | Auto-restart, same as above | Unchanged (Elastic IP attached) | Survives |
| Console "Terminate" | Gone | Elastic IP detaches (still allocated to account) | Gone (EBS released unless preserved) |
| App container crashes | Auto-restart by Docker with exponential backoff | Unchanged | Survives |
| `docker compose down` | Stop, do **not** auto-restart (operator intent respected) | Unchanged | Survives |

The auto-restart behavior depends on the Docker daemon starting at boot (it does by default on Ubuntu via systemd). Without that, the restart policy on containers is moot — they only auto-restart if the daemon they're attached to comes back up.

### 8.9 Operator access

- SSH from `<my IP>` only — security group enforces this at the network layer.
- The `ubuntu` user has `sudo` without password (Ubuntu AMI default) and is a member of the `docker` group, so `docker` commands run without `sudo`.
- The SSH key (`fluxurl-key.pem`) is stored in the operator's password manager and is the only credential for accessing the instance. Losing it means rebuilding the instance.

---

## 9. ECR auth flow

White-box view of how `docker pull` from a private ECR repository authenticates. The black-box description is in `HLD.md` §5.2 ("Image distribution").

### 9.1 The two principals involved

Two distinct identities push to and pull from ECR. Mixing them up is a common source of confusion.

| Direction | Principal | Mechanism |
|---|---|---|
| Laptop → ECR (push) | IAM user `saurabh-admin` | Long-lived access key in `~/.aws/credentials` |
| EC2 → ECR (pull) | IAM role `fluxurl-ecr-pull-role` (assumed by the EC2 instance) | Temporary credentials served via the instance metadata service |

The laptop uses long-lived credentials because there's no AWS-managed compute to attach a role to — it's a developer machine. The EC2 instance uses an instance role because it *is* AWS-managed compute, and roles are the right pattern for compute principals.

### 9.2 The role and policy on EC2

**Role:** `fluxurl-ecr-pull-role`
- **ARN:** `arn:aws:iam::546201496354:role/fluxurl-ecr-pull-role`
- **Trust policy:** allows the EC2 service principal (`ec2.amazonaws.com`) to assume the role. Without this, AWS would not be allowed to hand role credentials to an EC2 instance.
- **Permissions policy:** the AWS-managed policy `AmazonEC2ContainerRegistryReadOnly`. Grants `ecr:GetAuthorizationToken`, `ecr:BatchCheckLayerAvailability`, `ecr:GetDownloadUrlForLayer`, `ecr:BatchGetImage`, plus a handful of describe/list calls. Explicitly does *not* grant push (`ecr:PutImage`, `ecr:UploadLayerPart`) — least privilege.

**Attachment:** the role is attached to the EC2 instance via an *instance profile* (a thin wrapper around the role specific to EC2). When attached, AWS begins publishing temporary credentials at the instance metadata service.

### 9.3 Instance metadata service (IMDS)

The metadata service is a special endpoint reachable only from inside the EC2 instance — link-local IP `169.254.169.254`, not routable from outside the box.

**What it serves for the role:**

```
http://169.254.169.254/latest/meta-data/iam/security-credentials/fluxurl-ecr-pull-role
```

Returns JSON:
```json
{
  "Code": "Success",
  "Type": "AWS-HMAC",
  "AccessKeyId": "ASIA...",          ← prefix ASIA = session credentials
  "SecretAccessKey": "...",
  "Token": "...",                     ← session token (required for temp creds)
  "Expiration": "..."                 ← when these creds stop working
}
```

- `AccessKeyId` starts with `ASIA` (session credentials). Long-lived credentials start with `AKIA`. The prefix is a quick visual diagnostic.
- `Token` is required for any API call signed with temp credentials.
- `Expiration` is well in the future; AWS refreshes credentials before they expire, transparently.

**IMDSv2:** modern AMIs default to IMDSv2, which requires a session-token handshake before the credential GET works. The AWS SDK handles this automatically; manual `curl` against IMDS needs an explicit `PUT` to obtain a token first.

### 9.4 What `aws ecr get-login-password` actually does

The login command pipes through several layers; the relevant chain on EC2 is:

```
aws ecr get-login-password --region ap-south-1
       │
       ▼
AWS CLI: "I need credentials"
       │
       │   Credential provider chain checks (in order):
       │     1. environment variables (AWS_ACCESS_KEY_ID etc.) → not set
       │     2. ~/.aws/credentials file → does not exist
       │     3. EC2 instance metadata service (IMDS) → found!
       │
       ▼
AWS CLI: calls ECR's GetAuthorizationToken API, signed with temp creds
       │
       ▼
ECR: validates the signature, looks up the principal
     (sees "this is fluxurl-ecr-pull-role"), checks the policy
     (sees ecr:GetAuthorizationToken is allowed), returns a token
       │
       ▼
AWS CLI: prints the token to stdout (which is a base64-encoded
         "user:password" string for Docker login)
```

The token is then piped into `docker login --username AWS --password-stdin <ecr-host>`, which stores Docker credentials in `~/.docker/config.json` (or a credential helper) and tells subsequent `docker pull` calls to use them.

**Token lifetime:** ~12 hours. After that, any new `docker pull` returns 401 Unauthorized; re-running the login command refreshes. Phase 5 (CI/CD) re-authenticates per deploy, so this is invisible there. In Phase 4's manual flow, it's a brief friction the operator handles.

### 9.5 What `docker pull` does on top of that

Once Docker has ECR credentials cached:

```
docker pull 546201496354.dkr.ecr.ap-south-1.amazonaws.com/fluxurl:latest
       │
       ▼
Docker daemon: resolves the tag against ECR
       │   GET /v2/fluxurl/manifests/latest
       │   Authorization: Bearer <token from login>
       │
       ▼
ECR: returns the manifest (a JSON document listing layer digests)
       │
       ▼
Docker daemon: for each layer in the manifest,
   if NOT already in /var/lib/docker/overlay2/, pull it:
     GET /v2/fluxurl/blobs/sha256:<digest>
       │
       ▼
ECR: streams the layer blob (compressed tar)
       │
       ▼
Docker daemon: verifies digest, unpacks layer into overlay2,
   assembles image from all layers, tags it locally
```

The deduplication is automatic — if a layer's digest is already in the local store (from a previous pull, a `docker save`/`load` in Phase 3, or a different image that shared the layer), it's not re-downloaded. This is why subsequent pulls of slightly-changed images are nearly free over the wire.

### 9.6 Credential isolation between push and pull

A small but important design point: the EC2 instance role *cannot push* to ECR. The `AmazonEC2ContainerRegistryReadOnly` policy explicitly excludes write actions. If the EC2 instance were compromised, an attacker could not push a malicious image to ECR using the role's credentials. Push permission lives only with the laptop's IAM user, whose credentials are not on EC2 at all.

In Phase 5, the laptop's role in pushing is replaced by GitHub Actions. The CI workflow will use a *different* IAM principal (likely a dedicated push role assumed via OIDC federation), keeping push and pull permissions on separate principals throughout.

### 9.7 What to verify when ECR auth misbehaves

Diagnostic order, from cheapest to most expensive check:

1. **`aws sts get-caller-identity`** on EC2 — confirms the metadata service is returning credentials and they're for the expected role. If this fails, the role isn't attached or IMDS is unreachable.
2. **`aws ecr describe-repositories --region ap-south-1`** — confirms the role has ECR read permissions and ECR is reachable. If this fails with `AccessDenied`, the permissions policy is missing or wrong.
3. **`aws ecr get-login-password --region ap-south-1`** — confirms the auth-token API works specifically. If steps 1 and 2 pass but this fails, propagation delay; retry after 30 seconds.
4. **`docker login`** — confirms Docker accepts the token. Very rarely fails if step 3 worked.
5. **`docker pull <ecr-uri>/fluxurl:latest`** — the actual pull. If steps 1–4 worked but pull returns 401, the token has expired (>12 h since login) — re-run step 3 and step 4.

The diagnostic ladder mirrors the auth flow's layers: identity → permissions → token issuance → token acceptance → token use.

---

## 10. CI/CD workflow structure

*To be filled in Phase 5.*