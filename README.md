# Fluxurl

A URL shortener with click analytics. Shorten long URLs into 7-character codes and track how many times each link is visited.

**Live:** http://3.109.34.168/healthz

## Why

Learning project to build depth in Python/FastAPI, Docker, AWS, and CI/CD. The focus is on production-shaped infrastructure — no long-lived AWS credentials anywhere, push-to-deploy via GitHub Actions, least-privilege IAM with separate roles per principal, full audit trail. The app is the vehicle.

## Stack

- **App:** Python 3.12, FastAPI, Pydantic, SQLAlchemy 2.0 (async), Alembic, Uvicorn
- **Database:** PostgreSQL 16
- **Packaging:** Docker (multi-stage), docker-compose for local dev
- **Infrastructure:** AWS EC2, ECR, IAM (instance role + OIDC-federated GitHub role), VPC + Security Groups, SSM
- **CI/CD:** GitHub Actions — lint and test on every push; build, push to ECR, and deploy to EC2 on push to `master`

## How it works

1. `POST /shorten` with a long URL → returns a short code
2. `GET /{short_code}` → 302 redirect to the original URL

## Deploying

```
git push origin master
```

Production updates in 3–5 minutes via the CI/CD pipeline. See [SETUP-AND-DEPLOY](Docs/SETUP-AND-DEPLOY.md) for the manual fallback.

## Docs

- [Project plan](Docs/fluxurl-project-plan.md) — phased build plan
- [HLD](Docs/HLD.md) — high-level (black-box) design
- [LLD](Docs/LLD.md) — low-level (white-box) design
- [SETUP-AND-DEPLOY](Docs/SETUP-AND-DEPLOY.md) — operations guide
- [Decisions](Docs/decisions) — ADRs