# ADR 0008: Multi-stage Dockerfile (full builder + slim runtime) over single-stage

- **Status:** Accepted
- **Date:** 2026-05-08
- **Phase:** 2 (Dockerize)

## Context

Phase 2 packages the FastAPI app into a container image for ECR/EC2. ADR 0007 already settled the *runtime* base image (`python:3.12-slim`). This ADR addresses a separate question: should the Dockerfile be a single stage that builds and runs out of the same image, or split into a dedicated **builder** stage and a **runtime** stage that copy artefacts between them?

The answer matters because the builder and runtime have fundamentally different jobs:

- The **builder** runs once per image build. It needs to be *robust* — every package pip might install, including ones with C extensions that need a compiler, has to succeed. Build time and builder size are paid in CI minutes and local disk, never in production.
- The **runtime** runs continuously in production. It's pulled to EC2, pushed to ECR, and accounts for every byte of cold-start pull time. It needs to be *small and minimal*, with no tooling beyond what's needed to serve requests.

A single-stage Dockerfile is forced to compromise between these two roles in one image. A multi-stage Dockerfile lets each stage optimise for its own job and then hands a small, clean artefact across the stage boundary.

## Options considered

### Option A: Single-stage on `python:3.12` (full Debian)

Use the full image throughout. Build and runtime are the same.

- **Pros:**
  - Simplest possible Dockerfile — one `FROM`, no stage boundaries.
  - Any future C-extension dependency installs without ceremony — gcc and headers are already there.
- **Cons:**
  - Final image is ~1 GB. Most of that is build tooling that never executes at runtime.
  - Larger attack surface — every binary in the image is something an attacker could potentially leverage.
  - Slower pulls on cold EC2 instances; eats into free-tier disk.

### Option B: Single-stage on `python:3.12-slim`

Use the slim image throughout. Build and runtime are the same, but stripped of compilers and apt build tools.

- **Pros:**
  - Final image ~298 MB — fits the runtime perfectly.
  - **For our current dependency set, this is functionally identical in size to multi-stage.** Every dep we use ships a glibc wheel (`asyncpg`, `psycopg[binary]`, FastAPI, SQLAlchemy, Pydantic), so slim has everything pip needs to succeed today. There is no build-time bloat to leave behind because none ever gets installed.
- **Cons:**
  - The day a future dependency lacks a manylinux wheel and needs to compile, the build will fail. Fixing it means either adding `apt-get install build-essential` (which permanently inflates the production image) or restructuring the Dockerfile mid-incident.
  - Couples the runtime image to whatever the build process happens to need today. Any build-time decision (a compiler, a header library, a temporary tool) ends up shipping to production.

### Option C: Multi-stage — `python:3.12` builder + `python:3.12-slim` runtime

Builder stage uses the full image (gcc, headers, complete apt toolchain). Builder installs dependencies into a venv at `/opt/venv`. Runtime stage uses slim and `COPY --from=builder /opt/venv /opt/venv`, then sets `PATH` so the venv's binaries resolve.

- **Pros:**
  - Production image stays at ~302 MB regardless of what the builder needs to do. A future dep that needs `gcc` doesn't change a single byte of what gets pushed to ECR.
  - The two stages can be optimised for different things — robustness in the builder, size in the runtime — because they have different lives.
  - The builder's overhead lives only in CI / local Docker layer cache, not in the deployed image.
- **Cons:**
  - More complex Dockerfile — two `FROM`s, a `COPY --from=`, an explicit `ENV PATH`.
  - First build pulls ~1 GB for the builder base; subsequent builds reuse the cached layer.
  - For *today's* deps (all glibc-wheel-friendly), the production image is ~4 MB *larger* than single-stage slim, because of small venv overhead (`bin/` wrapper scripts, `pyvenv.cfg`, slightly more aggressive bytecode compilation by the full Python interpreter). The size win arrives only when the builder actually has bloat worth discarding.

## Decision

**Option C: multi-stage with `python:3.12` builder and `python:3.12-slim` runtime.**

The principle is: **optimise the production image, not the build image.** The builder is throwaway — it doesn't ride to production. Making the builder slightly bigger and more capable is a free trade: zero cost in production size, real reliability gained against future dependency changes. That is the entire point of multi-stage — the two stages can be optimised for different things because they have different lives.

For our *current* set of dependencies, Option B (single-stage slim) would produce a near-identical-sized image. Option C is not chosen for today's byte count — it's chosen because the day a real C-extension dependency lands without a manylinux wheel, the production image stays exactly the same size while the builder absorbs the change. With single-stage slim, that same day forces a Dockerfile rewrite under pressure; with single-stage full, the pre-paid bloat is already in production whether you ever needed it or not.

The ~4 MB increase over single-stage slim is the cost of having that infrastructure ready, and it is paid once.

## Consequences

- **Production image is decoupled from build-time concerns.** New deps that need to compile only affect the builder — `apt-get install build-essential` in the builder stage adds nothing to the runtime image.
- **First builds are slower.** Pulling `python:3.12` takes longer than pulling slim alone. Subsequent builds hit the layer cache and are fast.
- **The venv at `/opt/venv` is the contract between stages.** Builder writes there, runtime copies it whole. Keeping the path neutral (not `/root/.local`) means a future non-root user works without re-pathing.
- **`ENV PATH="/opt/venv/bin:$PATH"`** is required in the runtime stage so that `uvicorn`, `alembic`, and `python` resolve to the venv versions. This is the Dockerfile equivalent of `source activate`.
- **Image size baseline for multi-stage:** ~302 MB. Single-stage slim was 298 MB; full single-stage would be ~1 GB. That ~302 MB is the new number future optimisations (non-root user, healthcheck, pip cache cleanup) will be measured against.
- **Revisit if:** (a) we add a dep that meaningfully grows the venv (e.g., NumPy, pandas) — the runtime size will jump and may justify a closer look at what's actually in `site-packages`; (b) we ever consider distroless or `scratch` for the runtime — multi-stage is the prerequisite, so this ADR makes that move possible later.

## Notes

The most common interview pushback on multi-stage is "but your image got slightly *bigger*, what's the point?" The honest answer is: multi-stage's payoff is conditional on the builder producing discardable bloat. Today our builder doesn't, so we pay a small overhead. The reason we adopt it anyway is exactly the same reason teams write tests before they have bugs — the structure earns its keep at the moment a problem appears, not before. Reverting to single-stage slim *today* and "adding multi-stage when needed" is also defensible, but it makes the same future change a Dockerfile rewrite under time pressure rather than a one-line edit.
