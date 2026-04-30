# Fluxurl

A URL shortener with click analytics. Shorten long URLs into 7-character codes and track how many times each link is visited.

## Why

Learning project to build depth in Python/FastAPI, Docker, AWS (EC2, ECR, IAM), and CI/CD with GitHub Actions. The infrastructure and deployment pipeline are the focus — the app is the vehicle.

## Stack

- **App:** Python 3.12, FastAPI, Pydantic, SQLAlchemy 2.0 (async), Alembic, Uvicorn
- **Database:** PostgreSQL
- **Packaging:** Docker (multi-stage), docker-compose for local dev
- **Infrastructure:** AWS EC2, ECR, IAM (instance role), VPC + Security Groups
- **CI/CD:** GitHub Actions — lint, test, build, push, deploy on every push to `main`

## How it works

1. `POST /shorten` with a long URL → returns a short code
2. `GET /{short_code}` → 301 redirect to the original URL
3. Click analytics track each redirect

## Docs

- [Project plan](Docs/fluxurl-project-plan.md) — phased build plan
- [HLD](Docs/HLD.md) — high-level (black-box) design
- [LLD](Docs/LLD.md) — low-level (white-box) design
- [Decisions](Docs/decisions) — ADRs
- [Notes](Docs/NOTES.md) — personal concepts log
