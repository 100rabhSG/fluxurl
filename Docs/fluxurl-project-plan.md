# Fluxurl — Project Plan

A URL shortener with analytics. Breadth-first, manual-before-automated learning project to deeply understand Python, FastAPI, Docker, AWS (EC2 / ECR / IAM), and CI/CD with GitHub Actions.

---

## Project Overview

**Name:** Fluxurl

**End state of v1:** Working URL shortener deployed on EC2 via GitHub Actions. Every push to `main` lints, tests, builds an image, pushes to ECR, and redeploys. Postgres runs as a container alongside the app.

**Stack (v1):**

```
App:       Python 3.12, FastAPI, Pydantic, SQLAlchemy (async), Alembic, Uvicorn
Testing:   pytest, ruff
Database:  PostgreSQL (in a Docker container on EC2)
Packaging: Docker (multi-stage Dockerfile), docker-compose for local dev
AWS:       EC2, ECR, IAM (instance role), VPC + Security Groups, CloudWatch (basic)
CI/CD:     GitHub Actions
```

---

## Design Docs (running across all phases)

The goal of this project is interview-grade design thinking, not just working code. Three artifacts capture that, alongside the existing `NOTES.md` concepts log:

- **`Docs/HLD.md`** — high-level design. Black-box view: system context, components, data flow, deployment topology, scaling story, failure modes. First sketched before Phase 1 (see Phase 0.5), then refined phase by phase as reality forces revisions.
- **`Docs/LLD.md`** — low-level design. White-box view: module boundaries inside `app/`, function-level contracts, DB session/transaction lifecycle, error taxonomy, concurrency model. Written *as you build*, not upfront — LLD before code is fiction.
- **`Docs/decisions/`** — one Architecture Decision Record (ADR) per non-obvious decision. Each ADR is short and follows: **Context → Options considered → Decision → Consequences**. The point of an ADR is to force you to name the alternatives you rejected and why.

**ADR template** lives at `Docs/decisions/0000-template.md`. Numbered sequentially: `0001-short-code-generation.md`, `0002-async-sqlalchemy.md`, etc.

**Rule:** every phase checkpoint requires writing any ADRs triggered by that phase's decisions, and updating `HLD.md` and `LLD.md` if the architecture or internals changed. No phase is "done" until its design docs are written.

**Doc map:**

| Doc | Purpose | Audience |
|---|---|---|
| `NOTES.md` | Concept recall in your own words ("what is async SQLAlchemy?") | You, later |
| `HLD.md` | What the system looks like from outside (boxes, arrows, boundaries) | Anyone reviewing your architecture |
| `LLD.md` | How the inside is wired (modules, contracts, sessions, errors) | A reviewer reading your code |
| `Docs/decisions/*.md` | Why you chose X over Y at a point in time | An interviewer asking "why didn't you do Z?" |

**HLD vs LLD vs ADR — quick rule:**

- If it has rejected alternatives → ADR
- If it describes the system from outside → HLD
- If it describes how the code is organised inside → LLD

---

## Phase 0 — Setup (½ day)

Get accounts and tools ready before you write code.

- [x] AWS account created, MFA on root, IAM admin user with access keys
- [x] AWS CLI installed and configured locally
- [x] AWS Budgets alert set at $1 (you're free tier, but paranoia is healthy)
- [x] GitHub repo created, README skeleton, `.gitignore` for Python
- [x] Concepts log started (a single `NOTES.md` or Notion page — whatever you'll actually use)
- [x] Python 3.12 installed locally; Docker Desktop installed and running
- [x] `Docs/HLD.md` skeleton created (just headings — filled in during Phase 0.5)
- [x] `Docs/LLD.md` skeleton created (just headings — filled in starting Phase 1)
- [x] `Docs/decisions/0000-template.md` ADR template in place

**Concepts log entry:** What's the difference between AWS root user and IAM user? Why never use root for daily work?

**Design docs:** No ADRs yet — nothing architectural decided. HLD and LLD are skeletons.

---

## Phase 0.5 — v1 HLD sketch (½ day)

Before writing a line of app code, design v1 as if it were an interview question. The point is to lock in the *shape* of the system and force the obvious decisions out into the open. This is intentionally short — you don't yet know enough to over-design, and that's a feature.

**Build:**

- [x] Fill in `Docs/HLD.md` sections 1–4 (system context, components, data model, request flows) at a black-box level
- [x] Fill in section 8 (out of scope for v1) — explicitly
- [x] List open questions in section 9 — anything you genuinely don't know yet
- [x] Write ADRs for decisions you've already implicitly made (see `Docs/decisions/0001`–`0004`)

**Checkpoint:** You can hand `HLD.md` to someone who has never seen the project and they understand what v1 is, what's in scope, what's out, and which questions remain. The ADRs name at least one rejected alternative each.

**Constraint to respect:** Don't fill in deployment topology, container internals, or CI/CD details yet. Those belong to later phases — trying to design them now is guessing.

**Concepts log:**

- What does "design a URL shortener" actually mean as an interview question — what's the interviewer testing for?
- Why pick relational over key-value for a write-heavy short-code service (and what would flip the answer)?

---

## Phase 1 — Local app, no Docker, no AWS (2–3 days)

Just build the app on your machine. Get FastAPI fundamentals down before anything else.

**Build:**

- [x] FastAPI project skeleton (`app/main.py`, `app/api/`, `app/models/`, `app/db/`)
- [x] Postgres running locally (Docker container is fine, but only as a dev dependency — no app containerisation yet)
- [x] SQLAlchemy async setup, single `urls` table: `short_code` (PK), `long_url`, `created_at` (no surrogate `id`)
- [x] Alembic initialised, first migration creates the table
- [x] `POST /shorten` — takes a long URL, generates 7-char base62 short code, returns the short URL
- [x] `GET /{short_code}` — looks up code, returns 302 redirect (or 404)
- [x] Pydantic schemas for request/response
- [x] Basic error handling (invalid URL, code not found, code collision on insert)
- [x] 5–10 pytest tests covering happy path and edge cases

**Checkpoint:** You can `curl -X POST localhost:8000/shorten -d '{"url":"..."}'`, get a short code, then `curl -L localhost:8000/{code}` and get redirected. Tests pass.

**Concepts log:**

- Why async SQLAlchemy needs `AsyncSession`, not `Session`
- What FastAPI's dependency injection actually does (you know DI from your stack — what's different here?)
- Why base62 and not base64 for short codes (URL-safety)
- Collision strategy: retry-on-conflict vs check-before-insert (and why one is racy)

**Design docs:**

- ADR: short-code generation (length, alphabet, random vs sequential, collision handling)
- ADR: sync vs async SQLAlchemy
- ADR: schema for `urls` table (PK choice, indexing on `short_code`, why not store URL hash)
- Update `HLD.md`: refine the v1 sketch with anything that turned out wrong once you actually built it
- Update `LLD.md`: module layout under `app/`, who owns the DB session, request → session → transaction lifecycle, error taxonomy (which exceptions → which HTTP codes), Pydantic schemas as the API boundary

---

## Phase 2 — Dockerize (1–2 days)

Now make the app run in a container. Locally only — still no AWS.

**Build:**

- [ ] Multi-stage Dockerfile (builder stage installs deps, runtime stage copies only what's needed)
- [ ] Non-root user in the image
- [ ] `.dockerignore` (don't ship `.git`, `__pycache__`, `.env`, etc.)
- [ ] `docker-compose.yml` for local dev: app container + postgres container, networked together, env vars wired
- [ ] App reads DB connection from env vars, not hardcoded
- [ ] `docker compose up` starts everything; tests still pass when run inside the container

**Checkpoint:** `docker compose up` from a clean clone gets the whole thing running. You can hit the API. Image size is under 300MB.

**Break it intentionally:**

- Build the image as single-stage and compare size — feel why multi-stage matters
- Run as root and read the security warning — understand why non-root
- Forget `.dockerignore` and watch the image bloat

**Concepts log:**

- Multi-stage builds: what gets discarded, what gets kept, why
- Docker layer caching: why is `COPY requirements.txt` separate from `COPY . .`
- Bridge network in docker-compose: how does the app container reach `postgres` by hostname
- Image vs container vs volume — your three-line explanation

**Design docs:**

- ADR: multi-stage build vs single-stage (with concrete size numbers from your "break it intentionally" run)
- ADR: base image choice (e.g., `python:3.12-slim` vs `-alpine` vs distroless)
- Update `HLD.md`: add the local-dev container topology
- Update `LLD.md`: container internals (entrypoint, working dir, non-root UID, env-var contract, healthcheck)

---

## Phase 3 — Manual EC2 deployment (2–3 days)

Get your container running on AWS, by hand. No automation yet — that's the point.

**Build:**

- [ ] Launch EC2 t2.micro / t3.micro (Ubuntu 22.04 LTS), free tier
- [ ] Key pair created, `.pem` saved securely
- [ ] Security group: SSH (22) from your IP only, HTTP (80) from anywhere
- [ ] SSH in, install Docker on the box
- [ ] Manually copy your image to EC2 (using `docker save` + `scp` + `docker load`, or build directly on the box)
- [ ] Run app container + postgres container on EC2 (a small `docker-compose.yml` on the server is fine)
- [ ] App listens on port 80 (or map 80 → 8000)
- [ ] Hit your EC2 public IP from your browser — works

**Checkpoint:** Your URL shortener is live on the internet at `http://<ec2-public-ip>/`. You can shorten a URL and follow the redirect.

**Break it intentionally:**

- Close port 80 in the security group, watch what happens
- Stop the postgres container, hit the API, read the error
- Reboot EC2 — does your app come back? (Spoiler: probably not. That's the lesson.)

**Concepts log:**

- What's actually in a security group — stateful firewall, ingress rules, why "from your IP only" for SSH
- EC2 public IP vs Elastic IP (and why your IP changes on reboot if you didn't allocate one)
- Why running `docker compose up` interactively is bad — what happens when you close SSH
- systemd vs `docker run --restart=always` for keeping containers alive

**Design docs:**

- ADR: Postgres in a container on EC2 vs RDS for v1 (cost, learning value, deferred to Phase 7)
- ADR: t2.micro vs t3.micro, AMI choice
- ADR: security group ingress rules (why SSH from your IP only, why 80 from anywhere)
- Update `HLD.md`: add the deployment topology (EC2, SG, public IP)
- Update `LLD.md`: on-host layout (where the compose file lives, how containers restart, where logs go, how env vars are supplied to the box)

---

## Phase 4 — ECR + IAM instance role (1–2 days)

Stop copying images around. Pull from a real registry, with proper auth.

**Build:**

- [ ] Create ECR private repo
- [ ] From your laptop: tag image, `aws ecr get-login-password | docker login`, push
- [ ] Create IAM role with `AmazonEC2ContainerRegistryReadOnly` policy
- [ ] Attach role to EC2 instance
- [ ] On EC2: `aws ecr get-login-password` works **without any credentials configured** — this is the IAM moment
- [ ] Pull image from ECR on EC2, run it
- [ ] Stop using the locally-copied image entirely

**Checkpoint:** Your EC2 box authenticates to ECR using its instance role. Zero AWS keys on the server. Image pulls successfully.

**Break it intentionally:**

- Detach the IAM role from EC2, try to pull, read the error
- Use the wrong policy (e.g., write-only), see what fails

**Concepts log:**

- IAM role vs IAM user — when to use which
- Instance metadata service (IMDS): how does code on EC2 know what role it has
- The AWS credential chain: env vars → shared credentials file → instance role
- Why hardcoded keys on a server is a fireable offense

**Design docs:**

- ADR: ECR private vs public repo
- ADR: image tagging strategy (`latest` vs git SHA vs semver) and why
- ADR: IAM instance role vs static keys on EC2 (least-privilege rationale)
- Update `HLD.md`: add ECR to the deployment topology
- Update `LLD.md`: how the EC2 box authenticates to ECR at pull time (credential chain, IMDS hop)

---

## Phase 5 — GitHub Actions CI/CD (2–3 days)

The whole loop, automated.

**Build:**

- [ ] `.github/workflows/ci.yml`: on PR — checkout, install deps, ruff lint, pytest (with Postgres service container)
- [ ] `.github/workflows/deploy.yml`: on push to `main` — run CI, then build image, push to ECR, SSH to EC2, pull and restart
- [ ] GitHub Secrets configured: AWS credentials (a *deploy-only* IAM user with minimal perms — ECR push, no more), EC2 SSH key, EC2 host
- [ ] On EC2, deployment is a simple script: `docker compose pull && docker compose up -d`
- [ ] First push to main triggers full pipeline — green build, app updates

**Checkpoint:** You change a string in the app, push to main, walk away, come back to find the new version live. Pipeline completes in under 5 minutes.

**Break it intentionally:**

- Push code that fails lint — pipeline blocks deploy
- Push code that breaks a test — pipeline blocks deploy
- Push code that builds fine but crashes at runtime — read CloudWatch / Docker logs to debug

**Concepts log:**

- Difference between CI (test on PR) and CD (deploy on merge), and why they're often separate workflows
- Why your GitHub Actions IAM user needs different (smaller) permissions than your EC2 instance role
- The principle of least privilege, with concrete examples from your own setup
- Why deploying via SSH is fine for v1 but not how big teams do it (foreshadowing for ECS/K8s discussions)

**Design docs:**

- ADR: deploy via SSH vs pull-based agent vs ECS/Fargate (why SSH is right for v1, what would force a change)
- ADR: split CI and CD workflows vs one combined workflow
- ADR: deploy-only IAM user permissions (exact policy and why each statement is there)
- ADR: zero-downtime strategy for v1 (or explicit acknowledgement that v1 has a brief gap)
- Update `HLD.md`: add the CI/CD pipeline diagram
- Update `LLD.md`: workflow structure (jobs, steps, secrets, where each step runs), the deploy script's exact contract

---

## v1 Done — Take Stock

By here you've got a real, deployed, automatically-updating service on AWS. Take a day to:

- Write a proper README (architecture diagram, setup steps, design decisions)
- Update your concepts log
- Push your resume bullet — and be ready to defend every word
- Sit with what you built before adding more

**Resume line:**

> "Built and deployed **Fluxurl**, a URL shortener service on AWS (EC2, ECR, IAM) using FastAPI and async SQLAlchemy, packaged with multi-stage Docker builds, with a GitHub Actions CI/CD pipeline performing lint, test, build, and zero-downtime deploys."

---

## Phases 6+ — Deepening (pick what interests you)

These are independent — do them in any order, or skip ones you don't care about.

### Phase 6 — Click analytics

Add a `clicks` table. Every redirect logs `short_code`, `timestamp`, `ip`, `user_agent`, `referer`. Add `GET /{code}/stats` endpoint.
**Teaches:** high-write workloads, async logging, basic aggregation queries.

### Phase 7 — Migrate Postgres to RDS

Spin up RDS db.t3.micro. Migrate data with `pg_dump` / `pg_restore`. Update security group so EC2 can reach RDS on 5432.
**Teaches:** managed services, parameter groups, SG chaining, real production database setup.

### Phase 8 — Gunicorn + structured logging

Run Uvicorn under Gunicorn with multiple workers. Switch app logs to JSON format flowing to CloudWatch.
**Teaches:** process management, observability fundamentals.

### Phase 9 — Nginx reverse proxy + Let's Encrypt TLS

Put Nginx in front of FastAPI. Get a domain (Namecheap, ~$10/yr — your one allowed spend). Set up Let's Encrypt via certbot. Now you have HTTPS like a real site.

### Phase 10 — Redis caching + rate limiting

Add Redis container. Cache hot short codes (read-through). Rate-limit `POST /shorten` per IP.
**Teaches:** caching strategies, cache invalidation, distributed counters.

### Phase 11 — Terraform

Rewrite all your manually-created infra as Terraform. EC2, ECR, IAM, security groups, RDS. Apply against a fresh region to prove it works.
**Teaches:** IaC properly, because you already know what each resource does.

### Phase 12 — CloudWatch alarms + a status dashboard

Set up CloudWatch alarms (5xx rate, EC2 CPU). Build a small `/health` and `/metrics` endpoint.
**Teaches:** real observability and incident detection.

---

## Ground Rules Across All Phases

1. **Manual first, automated second.** Never automate something you haven't done by hand.
2. **Every phase needs a concepts log entry.** If you can't explain it in plain English, you don't understand it yet.
3. **Break things on purpose.** The fastest learning happens when you watch something fail and trace why.
4. **Don't move on until the checkpoint passes.** "Mostly working" doesn't count.
5. **Commit often, with real messages.** Your git history is part of the resume signal.
6. **AWS Budgets alarm at $1.** Free tier is generous but mistakes happen.
