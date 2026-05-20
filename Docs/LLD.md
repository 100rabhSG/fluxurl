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
| CI (pytest in GitHub Actions) | `conftest.py` calls `os.environ.setdefault("DATABASE_URL", ...)` and `os.environ.setdefault("BASE_URL", ...)` at the top, before any `app.*` import. Dummy values; the actual DB is replaced by the in-memory SQLite engine. See §10.5. |

The app code has no branch for "am I in a container?" — pydantic-settings reads from the environment regardless of how the value got there. The `.env` file is a host-only dev affordance, deliberately excluded from the image so secrets don't get baked in. Refusing to boot without required env vars is intentional: a wrong-but-present value silently connects to the wrong database (or generates wrong short URLs); an absent one is loud.

**Service-level consequence:** because `alembic/env.py` imports `get_settings()`, the `migrate` service needs *every* required env var, not just `DATABASE_URL`. This is why `BASE_URL` is set on the `migrate` service in `docker-compose.prod.yml` even though Alembic itself never uses it. The same dynamic surfaces in tests — `conftest.py` must set `BASE_URL` even though tests never exercise it. A planned cleanup (HLD §9) is to have `alembic/env.py` read `DATABASE_URL` directly from `os.environ` instead of going through the full `Settings` class — that would decouple migrations from the app's full config surface, and the same fix would simplify the conftest.

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
- **SSM agent:** `amazon-ssm-agent` pre-installed and enabled on Ubuntu 22.04. Connects outbound to AWS's SSM endpoints and waits for commands. Operates under the EC2 instance role's `AmazonSSMManagedInstanceCore` permission (Phase 5; see §10).
- **No other application runtime on the host.** No system Python service, no nginx, no systemd unit wrapping containers. The instance's sole job is to run Docker and respond to SSM commands.

### 8.2 Filesystem layout

```
/home/ubuntu/                       ← SSH default working dir, also the SSM working dir for compose commands
├── docker-compose.prod.yml         ← scp'd from laptop; source of truth for what runs on this box
└── (no source code, no Dockerfile) ← image lives in Docker's store, not unpacked here

/var/lib/docker/                    ← Docker's data root (managed; don't poke directly)
├── overlay2/                       ← image layers (incl. ECR-pulled images)
├── containers/                     ← per-container metadata + logs
│   └── <id>/<id>-json.log          ← stdout/stderr captured here per container
└── volumes/
    └── ubuntu_pgdata/_data/        ← the named volume that holds Postgres data
        └── PG_VERSION, base/, pg_wal/, ... (Postgres's actual data directory)

/var/log/amazon/ssm/                ← SSM agent logs (useful when deploy commands misbehave)

/                                   ← root filesystem on the 8 GiB EBS gp3 root volume
                                       (everything above lives on this single disk)
```

The compose file on `/home/ubuntu/docker-compose.prod.yml` is the only project-specific file directly visible on the host. Everything else lives inside Docker's data root or SSM/Docker's log directories.

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

Compose doesn't pick up changes to `environment:` blocks on an already-running container, and won't always re-pull a newer image with the same tag — `--force-recreate` ensures containers are torn down and rebuilt from current config and pulled images. This is also the exact command the CI/CD deploy step runs via SSM (see §10.6).

### 8.5 Persistence

- The `pgdata` named volume is mounted by `docker-compose.prod.yml` and physically lives at `/var/lib/docker/volumes/ubuntu_pgdata/_data/`.
- That path is on the EC2 instance's 8 GiB EBS gp3 root volume. EBS survives instance Stop/Start, so the data survives instance lifecycle events.
- **No backups.** Volume corruption, accidental deletion (`docker volume rm`), or EBS failure means total data loss with no recovery path. This is the most consequential v1 scope cut. Phase 7 (RDS) introduces automated backups.

### 8.6 Network

- Only ports 22 (SSH) and 80 (HTTP) are reachable from the internet, enforced by the EC2 security group.
- Inside the box, the compose bridge network gives each container an internal IP and DNS name. `app` reaches `db` at the hostname `db`, port 5432.
- **Postgres has no host port mapping in production.** This is intentional and different from local-dev — preventing port 5432 from being bound on the host, regardless of what the security group allows.
- The app container's port 8000 is mapped to host port 80, which is then exposed via the security group rule for HTTP.
- **Outbound HTTPS** is unrestricted by default. The instance reaches `*.amazonaws.com` for ECR pulls and SSM agent traffic (the IMDS endpoint is link-local, so not constrained by the security group anyway), and `registry-1.docker.io` for Postgres image pulls.
- **No new inbound ports for CI/CD.** SSM-triggered deploys arrive via the agent's pre-established outbound connection, not via inbound network. The security group's inbound rules are unchanged from Phase 3.

### 8.7 Logs

- Each container's stdout/stderr is captured by Docker's default `json-file` log driver and written to `/var/lib/docker/containers/<id>/<id>-json.log`.
- Read logs via `docker compose -f docker-compose.prod.yml logs -f app` (or any specific service). The `-f` follows in real time.
- **SSM command output** is captured automatically by the SSM agent and returned to the CI workflow that issued the command. It's also surfaced in CloudTrail and visible in the SSM "Run Command" history in the AWS console. The deploy step's stdout/stderr (docker login output, pull progress, container recreation messages) lives there.
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
| SSM deploy command runs | `app` and `migrate` containers recreated with the new image. `db` container is untouched (compose recreates only services whose image changed). | Unchanged | Survives |

The auto-restart behavior depends on the Docker daemon starting at boot (it does by default on Ubuntu via systemd). Without that, the restart policy on containers is moot — they only auto-restart if the daemon they're attached to comes back up.

### 8.9 Operator access

- SSH from `<my IP>` only — security group enforces this at the network layer.
- The `ubuntu` user has `sudo` without password (Ubuntu AMI default) and is a member of the `docker` group, so `docker` commands run without `sudo`.
- The SSH key (`fluxurl-key.pem`) is stored in the operator's password manager and is the only credential for accessing the instance. Losing it means rebuilding the instance.
- **SSH is for humans, SSM is for systems.** Routine deploys run via SSM (no human in the loop). SSH is retained for break-glass debugging — looking at logs, inspecting state, restarting the SSM agent if it ever wedges. The two channels are independent: if SSH access is lost (IP change, key lost), SSM still works; if SSM breaks, SSH still works for manual recovery.

---

## 9. ECR auth flow

White-box view of how `docker pull` from a private ECR repository authenticates. The black-box description is in `HLD.md` §5.2 ("Image distribution").

### 9.1 The three principals involved

Three distinct identities interact with ECR. Mixing them up is a common source of confusion.

| Direction | Principal | Mechanism |
|---|---|---|
| Laptop → ECR (push, break-glass only) | IAM user `saurabh-admin` | Long-lived access key in `~/.aws/credentials` |
| CI → ECR (push, routine) | IAM role `fluxurl-github-actions-role` (assumed via OIDC federation) | Temporary credentials issued by AWS STS to the GitHub Actions runner per job |
| EC2 → ECR (pull) | IAM role `fluxurl-ecr-pull-role` (assumed by the EC2 instance) | Temporary credentials served via the instance metadata service |

The laptop uses long-lived credentials because there's no AWS-managed compute to attach a role to — it's a developer machine. After Phase 5, the laptop is no longer in the routine push path; CI handles it via OIDC. The laptop credentials are retained for break-glass scenarios (CI broken, urgent fix needed). The EC2 instance uses an instance role because it *is* AWS-managed compute, and roles are the right pattern for compute principals. The CI runner uses OIDC federation because it's compute that runs *outside* AWS but can prove its identity via a signed token — see §10 for the full flow.

The general principle: **push and pull use different principals.** A compromise of one cannot poison the registry from the other side.

### 9.2 The role and policy on EC2

**Role:** `fluxurl-ecr-pull-role`
- **ARN:** `arn:aws:iam::546201496354:role/fluxurl-ecr-pull-role`
- **Trust policy:** allows the EC2 service principal (`ec2.amazonaws.com`) to assume the role. Without this, AWS would not be allowed to hand role credentials to an EC2 instance.
- **Permissions policies (two attached as of Phase 5):**
  - `AmazonEC2ContainerRegistryReadOnly` (Phase 4) — grants `ecr:GetAuthorizationToken`, `ecr:BatchCheckLayerAvailability`, `ecr:GetDownloadUrlForLayer`, `ecr:BatchGetImage`, plus a handful of describe/list calls. Explicitly does *not* grant push (`ecr:PutImage`, `ecr:UploadLayerPart`).
  - `AmazonSSMManagedInstanceCore` (Phase 5) — lets the SSM agent on the instance communicate with the SSM service. The agent uses these permissions to phone home and receive commands. It does *not* grant the instance any permission to *send* SSM commands; it only authorizes the agent to *receive* them.

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

**Token lifetime:** ~12 hours. After that, any new `docker pull` returns 401 Unauthorized; re-running the login command refreshes. The CI/CD deploy step (§10.6) re-authenticates on every deploy, so token expiry is invisible there. In the break-glass manual flow, it's brief friction the operator handles.

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

A small but important design point, now visible across three principals:

- **EC2 instance role** cannot push to ECR. The `AmazonEC2ContainerRegistryReadOnly` policy explicitly excludes write actions. A compromised EC2 instance cannot poison the registry.
- **GitHub Actions role** can push to ECR but only to the `fluxurl` repository, and only when assumed by a workflow on `master` of the specific repo. Branch and repo scoping is enforced in the trust policy's `sub` claim (see §10.3).
- **Laptop IAM user** can push to ECR (broader permissions, since it's a human admin), but is intentionally not used in the routine path.

The three principals are independent. Compromise of one does not grant the powers of another. This separation is the architectural payoff of the role-per-context model.

### 9.7 What to verify when ECR auth misbehaves

Diagnostic order, from cheapest to most expensive check. The ladder is the same on EC2 (for pull) and in CI (for push); only the principal differs.

1. **`aws sts get-caller-identity`** — confirms credentials are present and identify the expected role. On EC2: should show `assumed-role/fluxurl-ecr-pull-role/<instance-id>`. In CI: should show `assumed-role/fluxurl-github-actions-role/GitHubActions`. If this fails, the credential chain is broken — instance role detached (EC2) or OIDC misconfigured (CI).
2. **`aws ecr describe-repositories --region ap-south-1`** — confirms the role has ECR permissions and ECR is reachable. If this fails with `AccessDenied`, the permissions policy is missing or wrong.
3. **`aws ecr get-login-password --region ap-south-1`** — confirms the auth-token API works specifically. If steps 1 and 2 pass but this fails, IAM propagation delay; retry after 30 seconds.
4. **`docker login`** — confirms Docker accepts the token. Very rarely fails if step 3 worked.
5. **`docker pull <ecr-uri>/fluxurl:latest`** or **`docker push ...`** — the actual operation. If steps 1–4 worked but pull returns 401, the token has expired (>12 h since login) — re-run step 3 and step 4.

The diagnostic ladder mirrors the auth flow's layers: identity → permissions → token issuance → token acceptance → token use.

---

## 10. CI/CD workflow structure

White-box view of how `git push origin master` becomes a production deploy. The black-box description is in `HLD.md` §5.2 ("CI/CD pipeline" and "Deployment process"). The structural decision to use SSM rather than SSH is in [ADR 0012](decisions/0012-deploy-mechanism.md).

### 10.1 Workflow shape

One file: `.github/workflows/ci.yml`. Triggers on every push (any branch) and every pull request. Four jobs:

| Job | Depends on | Branch gate | Purpose |
|---|---|---|---|
| `lint` | — | (none, always runs) | `ruff check .` |
| `test` | — | (none, always runs) | `pytest` with in-memory SQLite |
| `build-and-push` | `lint`, `test` | `if: github.ref == 'refs/heads/master'` | `docker build` + tag `:latest` and `:<sha>` + `docker push` to ECR |
| `deploy` | `build-and-push` | `if: github.ref == 'refs/heads/master'` | `aws ssm send-command` to recreate containers on EC2 with the new image |

Two distinct phases stitched together:
- **CI (lint + test)** runs on every branch and PR. It's the always-on signal of code correctness; not gated to master.
- **CD (build-and-push + deploy)** runs only when CI passes on master. The `needs:` keyword enforces the ordering; the `if:` gate enforces the branch scope. Both are required — `needs:` without `if:` would build-and-push on every branch, polluting ECR; `if:` without `needs:` would let a deploy run even if tests failed.

Each job runs on a fresh `ubuntu-latest` runner. There is no shared state between jobs except what passes through ECR (the built image) and IAM (the temporary AWS credentials, re-acquired per job that needs them).

### 10.2 The two principals (CI side)

Two roles are involved when CI runs on master:

| Principal | Used by | What it can do |
|---|---|---|
| `fluxurl-github-actions-role` | `build-and-push` job and `deploy` job, assumed via OIDC | Push to the `fluxurl` ECR repo; send `AWS-RunShellScript` SSM commands to the specific EC2 instance |
| `fluxurl-ecr-pull-role` | EC2 instance receiving the SSM command | Pull from ECR; let SSM agent communicate with the SSM service |

The GitHub Actions role is *new in Phase 5*. The EC2 instance role pre-existed in Phase 4; Phase 5 extended it with `AmazonSSMManagedInstanceCore` so the agent can be reached.

These two roles never share credentials. The CI job assumes its role to send a command; that command runs on EC2 under the *instance* role; the instance role pulls from ECR. Three credential boundaries, end to end. A compromise of CI's credentials cannot impersonate EC2; a compromise of EC2's credentials cannot push to ECR.

### 10.3 OIDC federation (runtime sequence)

The CI side of the auth flow. The conceptual map is in `HLD.md` §5.2 ("CI/CD pipeline"); this section is the white-box mechanics.

**Setup (one-time, already done):**

- An OIDC identity provider is registered in IAM at the URL `https://token.actions.githubusercontent.com` with audience `sts.amazonaws.com`. This tells AWS "trust tokens signed by GitHub's OIDC provider for this audience."
- `fluxurl-github-actions-role` exists with a trust policy that allows the `sts:AssumeRoleWithWebIdentity` action *only* when the token's `sub` claim is `repo:100rabhSG/fluxurl:ref:refs/heads/master`. The branch scope is the security boundary — a workflow from any other branch, or from a fork, cannot assume this role.

**Runtime, every CI job that needs AWS:**

```
GitHub Actions runner
       │
       │ (1) workflow declares `permissions: id-token: write`
       │     so the runner is authorized to mint an OIDC token
       │
       │ (2) action `aws-actions/configure-aws-credentials@v4` asks
       │     GitHub's OIDC provider for a signed JWT
       │
       ▼
GitHub OIDC provider:
       │   Mints a JWT with claims:
       │     sub: repo:100rabhSG/fluxurl:ref:refs/heads/master
       │     aud: sts.amazonaws.com
       │     iss: https://token.actions.githubusercontent.com
       │     exp: 5 minutes from now
       │     plus run_id, run_number, actor, sha, etc.
       │   Signs with GitHub's rotating private key.
       │
       ▼
Runner: receives the JWT, sends to AWS STS:
       │   POST https://sts.amazonaws.com/
       │     Action: AssumeRoleWithWebIdentity
       │     RoleArn: arn:aws:iam::546201496354:role/fluxurl-github-actions-role
       │     WebIdentityToken: <JWT>
       │
       ▼
AWS STS:
       │   Verifies JWT signature against GitHub's published public key
       │   Reads claims, checks against role's trust policy
       │   sub claim matches the StringLike condition → allow
       │   Issues temporary credentials (~1 hour lifetime)
       │
       ▼
Runner: AWS CLI/SDK now has temp credentials in env vars
       │ (AWS_ACCESS_KEY_ID, AWS_SECRET_ACCESS_KEY, AWS_SESSION_TOKEN, AWS_REGION)
       │
       ▼
Subsequent steps in the job (docker push, aws ssm send-command, etc.)
       │ use these credentials transparently via the standard credential chain.
       │
       ▼ Job ends, runner is destroyed, credentials are gone forever.
```

A few key properties:

- **The JWT lives ~5 minutes.** It's a one-time exchange currency; once swapped for AWS credentials, it's discarded. Even if intercepted, the attacker has minutes to use it before expiry.
- **AWS credentials live ~1 hour.** Scoped to the role's permissions. When they expire, the job is usually long over.
- **No long-lived AWS credentials exist on the GitHub side**, ever. The only secret GitHub needs is its own OIDC signing key, which lives on GitHub's servers, never sent anywhere.
- **The trust policy is the security boundary.** Branch protection on the GitHub side is helpful but not load-bearing; the AWS trust policy is what actually decides whether the role can be assumed. Even if a workflow ran on a branch other than master (by mistake or attack), STS would refuse to issue credentials.

The first time you watch this flow execute and see `aws sts get-caller-identity` return the assumed-role ARN from a workflow run, it stops being abstract.

### 10.4 IAM policies on `fluxurl-github-actions-role`

Two inline policies attached, each minimal:

**`ecr-push-fluxurl`** — lets CI push to the specific ECR repository:

```json
{
  "Version": "2012-10-17",
  "Statement": [
    {
      "Sid": "GetAuthorizationToken",
      "Effect": "Allow",
      "Action": "ecr:GetAuthorizationToken",
      "Resource": "*"
    },
    {
      "Sid": "PushToFluxurlRepo",
      "Effect": "Allow",
      "Action": [
        "ecr:BatchCheckLayerAvailability",
        "ecr:InitiateLayerUpload",
        "ecr:UploadLayerPart",
        "ecr:CompleteLayerUpload",
        "ecr:PutImage",
        "ecr:BatchGetImage"
      ],
      "Resource": "arn:aws:ecr:ap-south-1:546201496354:repository/fluxurl"
    }
  ]
}
```

Two statements because `ecr:GetAuthorizationToken` is a registry-level action that requires `Resource: "*"` (an AWS quirk — it doesn't apply to a specific repo, so it can't be scoped to one ARN). The push actions are scoped tightly to just the `fluxurl` repo ARN — even with role compromise, CI can't push to *other* ECR repos in the account.

**`ssm-deploy-fluxurl`** — lets CI send `AWS-RunShellScript` commands to the specific EC2 instance:

```json
{
  "Version": "2012-10-17",
  "Statement": [
    {
      "Sid": "SendCommandToFluxurlInstance",
      "Effect": "Allow",
      "Action": "ssm:SendCommand",
      "Resource": [
        "arn:aws:ec2:ap-south-1:546201496354:instance/i-0ba70062d8f85852b",
        "arn:aws:ssm:ap-south-1::document/AWS-RunShellScript"
      ]
    },
    {
      "Sid": "GetCommandInvocation",
      "Effect": "Allow",
      "Action": "ssm:GetCommandInvocation",
      "Resource": "*"
    }
  ]
}
```

The `SendCommand` action requires *both* the target resource and the document being executed in the `Resource` array — SSM's permission model. Both are scoped: specific instance, specific document. The `GetCommandInvocation` action (used to poll for command completion and fetch output) requires `Resource: "*"` because command invocations are addressed by transient command IDs, not by static ARNs.

### 10.5 The CI workflow file, mechanics

The workflow file (`.github/workflows/ci.yml`) is conceptually four jobs as described in §10.1. Mechanics worth highlighting:

**`lint` and `test` jobs:**

- Both check out the repo, set up Python 3.12, install `requirements.txt` and `requirements-dev.txt`, then run their respective command (`ruff check .` and `pytest`).
- `actions/setup-python@v5` is configured with `cache: pip` and `cache-dependency-path` pointing at both requirements files. This caches the pip download layer between runs — first run installs from scratch (~30 s), subsequent runs reuse the cache (~5 s).
- The `test` job uses in-memory SQLite via `aiosqlite`, set up by `conftest.py`. **No Postgres service container is needed** because `conftest.py` overrides FastAPI's `get_session` dependency to yield SQLite-backed sessions instead. The tradeoff is that CI doesn't catch Postgres-specific bugs (concurrent transactions, JSON operators, timezone-aware columns). At v1 scale this is acceptable; HLD §9 tracks this as a Phase-12-or-later revisit.
- **`conftest.py` sets `DATABASE_URL` and `BASE_URL` via `os.environ.setdefault()` before any `from app.*` import.** This is required because `app/config.py`'s pydantic-settings construction fails fast on missing values. Without the setdefault calls, the `from app.main import app` line in conftest would raise a `ValidationError` and tests would never run. Values are dummies; the SQLite override means the DB URL is never actually used. This is the same fail-fast Settings behavior that bit the migrate service in Phase 3 (see §7.4 "service-level consequence") — it surfaces in every entry point that imports the app modules.

**`build-and-push` job:**

```yaml
build-and-push:
  needs: [lint, test]
  if: github.ref == 'refs/heads/master'
  permissions:
    id-token: write
    contents: read
  steps:
    - uses: actions/checkout@v4
    - uses: aws-actions/configure-aws-credentials@v4
      with:
        role-to-assume: arn:aws:iam::546201496354:role/fluxurl-github-actions-role
        aws-region: ap-south-1
    - uses: aws-actions/amazon-ecr-login@v2
    - run: |
        docker build -t $ECR_REGISTRY/$ECR_REPOSITORY:$IMAGE_TAG \
                     -t $ECR_REGISTRY/$ECR_REPOSITORY:latest .
        docker push $ECR_REGISTRY/$ECR_REPOSITORY:$IMAGE_TAG
        docker push $ECR_REGISTRY/$ECR_REPOSITORY:latest
      env:
        ECR_REGISTRY: 546201496354.dkr.ecr.ap-south-1.amazonaws.com
        ECR_REPOSITORY: fluxurl
        IMAGE_TAG: ${{ github.sha }}
```

Key points:

- `permissions: id-token: write` is what allows the runner to request an OIDC token. **Without this line, OIDC auth fails with a misleading "Not authorized to perform sts:AssumeRoleWithWebIdentity" error** — sounds like an AWS permissions issue but is actually a GitHub-side authorization missing. Common gotcha.
- The image is tagged twice — `:latest` (mutable convenience pointer) and `:<git-sha>` (immutable, traceable to source commit). Both push commands are run; the second push is essentially free because the layers were uploaded by the first.
- `aws-actions/amazon-ecr-login@v2` runs the `aws ecr get-login-password | docker login` chain (see §9.4) using the temp credentials from the previous step.

**`deploy` job:** see §10.6 below.

### 10.6 SSM deploy mechanism

The `deploy` job is the bridge between "image is in ECR" and "production runs the new image." It uses AWS Systems Manager Run Command rather than SSH — the architectural rationale is in [ADR 0012](decisions/0012-deploy-mechanism.md).

**The mechanism in three layers:**

**Layer 1: GitHub Actions runner sends a command via the SSM API.**

```yaml
- name: Configure AWS credentials
  uses: aws-actions/configure-aws-credentials@v4
  with:
    role-to-assume: arn:aws:iam::546201496354:role/fluxurl-github-actions-role
    aws-region: ap-south-1

- name: Send deploy command via SSM
  id: send
  run: |
    COMMAND_ID=$(aws ssm send-command \
      --instance-ids i-0ba70062d8f85852b \
      --document-name "AWS-RunShellScript" \
      --comment "Deploy commit ${{ github.sha }}" \
      --parameters 'commands=[
        "set -e",
        "cd /home/ubuntu",
        "aws ecr get-login-password --region ap-south-1 | docker login --username AWS --password-stdin 546201496354.dkr.ecr.ap-south-1.amazonaws.com",
        "docker compose -f docker-compose.prod.yml pull",
        "docker compose -f docker-compose.prod.yml up -d --force-recreate"
      ]' \
      --query "Command.CommandId" --output text)
    echo "command_id=$COMMAND_ID" >> $GITHUB_OUTPUT
```

The runner authenticates to AWS via OIDC (same as `build-and-push`), then calls the `ssm:SendCommand` API. The `comment` field includes the commit SHA, which becomes the breadcrumb in CloudTrail tying the deploy back to source. The command itself is a list of shell commands the SSM agent will execute on the target instance.

**Layer 2: SSM routes the command to the instance via the agent's outbound connection.**

The SSM agent on EC2 maintains an outbound HTTPS connection to AWS's SSM service. When the API receives a `SendCommand` with this instance as a target, the command is dispatched through that pre-existing channel. **No inbound network is involved on the EC2 side.** The agent on EC2 receives the command and executes it under `root` (the default for `AWS-RunShellScript` on Linux), so `docker` and other privileged commands work without needing extra group membership or `sudo`.

Three things make this work:
- The agent is running on EC2 (`systemctl status amazon-ssm-agent`).
- The instance role has `AmazonSSMManagedInstanceCore` (§9.2), which lets the agent authenticate to SSM.
- The instance has outbound HTTPS to AWS endpoints (default security group behavior).

**Layer 3: The deploy command sequence.**

The five-step shell sequence is:

1. `set -e` — abort on first error. Without this, `docker compose pull` failing wouldn't prevent the `up -d` step, and the workflow could "succeed" while leaving production in a broken state.
2. `cd /home/ubuntu` — the directory where `docker-compose.prod.yml` lives. SSM commands run in `/usr/bin` by default, so this is necessary for compose to find the file.
3. `aws ecr get-login-password ... | docker login ...` — auths docker to ECR. The SSM session inherits the EC2 instance role's credentials via IMDS (same chain as in §9.4). This is the same login that operators would run manually.
4. `docker compose pull` — fetches the new `:latest` image from ECR. Idempotent: if the image is already up to date, this is a no-op.
5. `docker compose up -d --force-recreate` — recreates containers from the new image. The `--force-recreate` is necessary because compose doesn't always restart a container when only the image-it-points-to has been updated (the tag string is unchanged). `-d` keeps the SSM session from being held open by container processes.

The deploy step does not currently shut containers down gracefully before recreating — compose's default recreate behavior kills the old container and starts the new one. In-flight requests during the swap fail. At v1 traffic (~12 reads/sec peak), this is a brief blip; Phase 12 may revisit if graceful rollover matters.

**Layer 4: The workflow waits for completion.**

```yaml
- name: Wait for deploy to complete
  run: |
    aws ssm wait command-executed \
      --instance-id i-0ba70062d8f85852b \
      --command-id ${{ steps.send.outputs.command_id }}

- name: Show command output
  if: always()
  run: |
    aws ssm get-command-invocation \
      --instance-id i-0ba70062d8f85852b \
      --command-id ${{ steps.send.outputs.command_id }} \
      --query "{Status:Status,StandardOutput:StandardOutputContent,StandardError:StandardErrorContent}" \
      --output table
```

The `aws ssm wait command-executed` call polls the SSM API until the command has finished (success or failure). Without this, the workflow would "succeed" immediately after dispatching the command, never knowing whether the deploy actually worked.

The `Show command output` step has `if: always()` so it runs even when the wait failed — that's exactly the case where you want to see what went wrong on EC2. The output (stdout and stderr from the deploy commands) gets surfaced in the workflow logs, so failures are visible in one place.

### 10.7 End-to-end timing and behavior

A typical push-to-deploy timeline:

| Stage | Duration | Notes |
|---|---|---|
| `git push origin master` | 1-3 s | Local git → GitHub |
| Workflow trigger | <5 s | GitHub queues the run |
| `lint` job | ~15 s | Mostly pip install (cached); ruff is sub-second |
| `test` job | ~20 s | Pip install + pytest; SQLite tests are instant |
| `build-and-push` job | 2-3 min | Mostly docker build (layers cached server-side after first run); push is fast |
| `deploy` job | 30-60 s | OIDC auth + send command + wait + image pull on EC2 + recreate |
| **Total push → live** | **3-5 min** | |

The `lint` and `test` jobs run in parallel, so total wall time is dominated by the slowest of the two. `build-and-push` and `deploy` are sequential because `deploy` depends on the image being in ECR.

### 10.8 What to verify when CI/CD misbehaves

Diagnostic ladder for the CI/CD pipeline, in order of where failures typically surface:

1. **Workflow doesn't trigger** — check the file is at `.github/workflows/ci.yml` (not `.yaml`, not in a subdirectory), and the `on:` block matches the event (`push` to the branch you pushed).
2. **`lint` or `test` fails** — read the workflow logs; this is normal CI failure, not infrastructure.
3. **`build-and-push` fails at "Configure AWS credentials"** with `Not authorized to perform sts:AssumeRoleWithWebIdentity` — almost always one of: `permissions: id-token: write` missing from the workflow YAML; the trust policy's `sub` claim doesn't match (branch typo, repo typo); or the OIDC provider isn't registered in IAM.
4. **`build-and-push` fails at "Login to ECR"** — the GitHub Actions role doesn't have `ecr:GetAuthorizationToken`. Check the inline policy in §10.4.
5. **`build-and-push` fails at `docker push`** — the role has GetAuthorizationToken but not the push actions, or the resource ARN in the inline policy doesn't match the image tag. Verify the `ecr:PutImage` and friends are present and scoped to the right repo ARN.
6. **`deploy` fails at "Send deploy command via SSM" with `InvalidInstanceId`** — the SSM agent on EC2 is not running or has not registered with SSM. SSH in and check `systemctl status amazon-ssm-agent` and `aws ssm describe-instance-information --region ap-south-1` from a laptop to confirm the instance is "Online."
7. **`deploy` waits forever** — the agent received the command but is taking longer than expected. SSH in and check `/var/log/amazon/ssm/amazon-ssm-agent.log` for what's actually happening. Most often: a slow `docker pull` over a constrained network.
8. **`deploy` reports the command completed but production didn't update** — check the "Show command output" step's logs. The deploy commands run with `set -e`, so they should fail loudly, but a subtle compose issue (e.g., the wrong compose file path) might appear as a success with no actual recreation.

The diagnostic order generally mirrors the pipeline's data flow: trigger → CI gates → AWS auth → ECR push → SSM dispatch → command execution → result verification. Failures at later stages depend on all earlier stages working.