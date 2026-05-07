# ADR 0008: Base image — `python:3.12-slim` over full Debian or Alpine

- **Status:** Accepted
- **Date:** 2026-05-07
- **Phase:** 2 (Dockerize)

## Context

Phase 2 packages the FastAPI app into a container image that will eventually be pushed to ECR and run on EC2. The first decision when writing the Dockerfile is which base image to start `FROM`. The Python project provides several official tags on Docker Hub, and the choice has knock-on effects on image size, build time, runtime libc, and which Python wheels actually install cleanly.

Constraints that shape the choice:

- App is Python 3.12 (pinned in `requirements.txt` toolchain).
- Runtime dependencies include `asyncpg` (C-extension Postgres driver) and `psycopg[binary]` (used by Alembic). Both ship precompiled wheels built against **glibc**.
- Free-tier deployment — image will be pulled to a small EC2 instance, so smaller is better for pull time and disk.
- Learning project — a "smaller is always better" race-to-the-bottom isn't the goal; the choice should be defensible, not extreme.

## Options considered

### Option A: `python:3.12` (full Debian)

The default tag. A complete Debian Bookworm image with the full apt toolchain, build tools, and a wide set of system libraries already installed.

- **Pros:**
  - Everything builds out of the box — no missing headers, no compiler hunts.
  - Closest to a "normal" Linux dev environment, fewest surprises.
- **Cons:**
  - ~1 GB. Most of it is build tooling and system packages this app never touches at runtime.
  - Larger attack surface — more binaries present means more potential CVE exposure.
  - Slower pulls on a cold EC2 instance.

### Option B: `python:3.12-slim` (Debian, trimmed)

Same Debian Bookworm base, but stripped of build tools, docs, and packages not required for running Python. Still **glibc**, so precompiled Python wheels work without modification.

- **Pros:**
  - ~150 MB base — roughly an order of magnitude smaller than full Debian.
  - Wheels for `asyncpg` and `psycopg[binary]` install in seconds because they match glibc.
  - Familiar Debian tooling is still available via `apt-get` if a system package is genuinely needed.
- **Cons:**
  - No compiler in the image, so any pure-source Python package would fail to install without adding `build-essential` (none of our current deps need this).

### Option C: `python:3.12-alpine`

Alpine Linux base. Smallest of the three on paper (~50 MB), uses **musl libc** instead of glibc, and uses `apk` instead of `apt`.

- **Pros:**
  - Smallest base by raw byte count.
  - Popular in size-obsessed minimal-image guides.
- **Cons:**
  - **musl libc breaks the wheel ecosystem.** PyPI wheels for `asyncpg` and `psycopg[binary]` are built against glibc (`manylinux`). On Alpine they don't match, pip falls back to building from source, which requires pulling in `gcc`, `musl-dev`, `postgresql-dev`, etc.
  - Net result: the "small" Alpine image ends up *larger* than slim once build deps are added, and build time goes from seconds to minutes.
  - musl has subtle behavioural differences from glibc (DNS resolution, threading) — extra debugging surface for no real benefit.

## Decision

**Option B: `python:3.12-slim`.**

The whole point of Alpine is binary size, but for a stack that depends on glibc Python wheels, Alpine inverts its own value proposition — you trade seconds of pull time for minutes of source compilation and a bigger final image. Full Debian solves the "everything just works" problem but ships ~850 MB of tooling we never use at runtime. Slim is the middle answer that actually fits this app: glibc-compatible (so wheels install cleanly), small enough that pulls are quick on a free-tier EC2, and trimmed of obvious bloat without going so minimal that ordinary Python packaging breaks.

## Consequences

- Wheels for `asyncpg` and `psycopg[binary]` install in their precompiled form — no compiler needed in the image at runtime.
- If a future dependency is pure-source-only (no manylinux wheel), the Dockerfile will need a build stage that adds `build-essential` and then discards it — this is the trigger for moving to a multi-stage build (planned in the next iteration of Phase 2, ADR 0007).
- Image size baseline for the single-stage build is ~298 MB. That's the number future optimisations (multi-stage, non-root user, removed `pip` cache) will be measured against.
- Sticking with Debian-family means `apt-get` is available if a system library is ever needed — no need to learn `apk` quirks.
- Revisit if: (a) a critical dependency drops glibc wheel support, or (b) image size becomes a real bottleneck on the deployment path (unlikely on EC2, more likely if this ever moves to Lambda).

## Notes

The Alpine question is the one an interviewer is most likely to push on, because "use Alpine, it's smaller" is folk wisdom in the Docker community. The honest answer is that Alpine is smaller *only* for stacks whose dependencies have musl wheels or are pure Python. For anything with C extensions targeting glibc — which is most of the scientific/database Python ecosystem — Alpine is a worse choice on every axis that matters: build time, final size, and runtime predictability.
