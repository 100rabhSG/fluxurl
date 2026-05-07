# ─── Stage 1: builder ────────────────
# Full Python image — has gcc and headers in case a future dep needs to compile.
FROM python:3.12 AS builder

# Create an isolated venv. Everything pip installs lands inside this directory,
# so the whole dependency set hands off to the runtime stage as one COPY.
RUN python -m venv /opt/venv

# Use the venv's pip directly. No "activate" needed — calling /opt/venv/bin/pip
# installs into /opt/venv/lib/python3.12/site-packages.
COPY requirements.txt .
RUN /opt/venv/bin/pip install --no-cache-dir -r requirements.txt

# ─── Stage 2: runtime ────────────────
# Slim image — no compiler, no build tools. Just enough to run Python.
FROM python:3.12-slim

WORKDIR /app

# Copy the populated venv from the builder. The runtime image now has every
# runtime dependency, but none of pip's build-time tooling or caches.
COPY --from=builder /opt/venv /opt/venv

# Put the venv's bin/ first on PATH so `uvicorn`, `python`, etc. resolve to
# the venv versions. This is the Dockerfile equivalent of `source activate`.
ENV PATH="/opt/venv/bin:$PATH"

# Explicit copies — safer than `COPY . .`, even with .dockerignore.
COPY app/ ./app/
COPY alembic/ ./alembic/
COPY alembic.ini ./

EXPOSE 8000

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
