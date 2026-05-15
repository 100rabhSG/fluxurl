# Fluxurl — Setup & Deployment Guide

How to run Fluxurl locally for development, and how to deploy it to AWS EC2.

> **Last verified:** May 2026 — Docker 29.x, Docker Compose v5.x, Ubuntu 22.04 LTS on EC2

---

## Part 1 — Local Setup

### Prerequisites

- Python 3.12+ (only needed if you want to run scripts outside Docker)
- Docker Desktop (or Docker Engine + Docker Compose)
- Git

### First-time setup

**1. Clone the repo**

```bash
git clone <your-repo-url>
cd fluxurl
```

**2. Create `.env`**

Both `DATABASE_URL` and `BASE_URL` are required — the app will refuse to start if either is missing. Create a `.env` file in the project root:

```
DATABASE_URL=postgresql+asyncpg://fluxurl:fluxurl@db:5432/fluxurl
BASE_URL=http://localhost:8000
```

`.env` is in `.gitignore` and `.dockerignore`; it's a dev-only convenience and never enters the image.

**3. Start the stack**

```bash
docker compose up
```

The dev compose file uses `build: .`, so compose builds the image automatically the first time. This starts three containers:
- `fluxurl-db` — PostgreSQL 16
- `fluxurl-migrate` — runs `alembic upgrade head`, then exits
- `fluxurl-app` — FastAPI app on port 8000

**4. Verify it's running**

In a separate terminal:

```bash
curl http://localhost:8000/healthz
```

Expected response: `{"db": "ok"}`

### Common local tasks

**Stop everything**

```bash
docker compose down
```

**Stop everything and reset the database**

```bash
docker compose down -v
```

The `-v` flag deletes the named volume (`pgdata`), wiping all DB state.

**Run tests**

```bash
docker compose run --rm app pytest
```

**Create a short URL**

```bash
curl -X POST http://localhost:8000/shorten \
  -H "Content-Type: application/json" \
  -d '{"url": "https://example.com"}'
```

PowerShell equivalent:

```powershell
Invoke-RestMethod -Uri http://localhost:8000/shorten `
  -Method Post `
  -ContentType "application/json" `
  -Body '{"url": "https://example.com"}'
```

**Connect to the database**

```bash
docker exec -it fluxurl-db psql -U fluxurl -d fluxurl
```

**View logs**

```bash
docker compose logs -f app     # follow app logs
docker compose logs db         # one-shot db logs
```

**Create a new migration after model changes**

```bash
docker compose run --rm app alembic revision --autogenerate -m "your message"
```

The new migration file appears in `alembic/versions/`. Review it, commit it, and the next `docker compose up` will apply it automatically.

### After code changes

Compose's `build: .` directive means image rebuilds happen automatically — but only if you tell compose to rebuild:

```bash
docker compose up --build
```

Without `--build`, compose will reuse the existing image even if your code changed.

---

## Part 2 — AWS EC2 Deployment

This section covers manual deployment to EC2. CI/CD (GitHub Actions → ECR → EC2) is the eventual goal (Phase 5), but the manual flow comes first.

The current production architecture (end of Phase 4):

- **Compute:** single EC2 instance (t3.micro, Ubuntu 22.04) in `ap-south-1`
- **Public address:** Elastic IP `3.109.34.168` (replace with your own if forking)
- **Image source:** ECR private repository `546201496354.dkr.ecr.ap-south-1.amazonaws.com/fluxurl`
- **EC2 → ECR auth:** IAM instance role `fluxurl-ecr-pull-role` (no AWS keys on the box)
- **Laptop → ECR auth:** IAM user `saurabh-admin` via `~/.aws/credentials`

For the *why* behind these choices, see `HLD.md` §5.2 and `LLD.md` §9.

### Prerequisites

- AWS account with MFA on root and an IAM admin user for daily work
- AWS CLI configured locally (`aws configure`)
- A budget alarm set in AWS Billing (recommended: $1)
- SSH key pair downloaded as `.pem` and stored securely (e.g., `~/.ssh/fluxurl-key.pem`)

### Initial EC2 setup (one-time)

**1. Launch an EC2 instance**

In the AWS console:
- AMI: Ubuntu 22.04 LTS (or current LTS), 64-bit x86, free-tier eligible
- Instance type: `t3.micro` (free-tier)
- Storage: 8 GiB gp3
- Key pair: create new, save the `.pem` file
- Security group rules:
  - SSH (22) — source: **My IP** (not anywhere)
  - HTTP (80) — source: anywhere
- Auto-assign public IP: **Enable**

**2. Set key file permissions**

Linux/macOS:

```bash
chmod 400 ~/.ssh/fluxurl-key.pem
```

Windows (PowerShell):

```powershell
icacls C:\path\to\fluxurl-key.pem /inheritance:r
icacls C:\path\to\fluxurl-key.pem /grant:r "$($env:USERNAME):R"
```

**3. SSH into the instance**

```bash
ssh -i ~/.ssh/fluxurl-key.pem ubuntu@<public-ip>
```

**4. Install Docker and AWS CLI on the instance**

```bash
sudo apt-get update && sudo apt-get upgrade -y
sudo apt-get install -y ca-certificates curl gnupg lsb-release awscli

sudo install -m 0755 -d /etc/apt/keyrings
sudo curl -fsSL https://download.docker.com/linux/ubuntu/gpg \
  -o /etc/apt/keyrings/docker.asc
sudo chmod a+r /etc/apt/keyrings/docker.asc

echo \
  "deb [arch=$(dpkg --print-architecture) signed-by=/etc/apt/keyrings/docker.asc] \
  https://download.docker.com/linux/ubuntu \
  $(. /etc/os-release && echo "$VERSION_CODENAME") stable" | \
  sudo tee /etc/apt/sources.list.d/docker.list > /dev/null

sudo apt-get update
sudo apt-get install -y docker-ce docker-ce-cli containerd.io \
  docker-buildx-plugin docker-compose-plugin

sudo usermod -aG docker ubuntu
```

After the last command, log out and back in for the group change to take effect.

Verify:

```bash
docker --version
docker compose version
aws --version
docker run hello-world
```

### ECR + IAM instance role setup (one-time)

**1. Create the ECR repository**

In the AWS console: ECR → Private registry → Create repository:
- Repository name: `fluxurl`
- Tag immutability: Disabled (mutable, for `:latest` workflow — see [ADR 0011](decisions/0011-image-tagging-strategy.md))
- Scan on push: Enabled

The repository URI will be `<account-id>.dkr.ecr.ap-south-1.amazonaws.com/fluxurl`.

**2. Create the IAM role for EC2**

IAM → Roles → Create role:
- Trusted entity: AWS service → EC2
- Permissions policy: `AmazonEC2ContainerRegistryReadOnly`
- Name: `fluxurl-ecr-pull-role`

**3. Attach the role to the EC2 instance**

EC2 → Instances → select instance → Actions → Security → Modify IAM role → select `fluxurl-ecr-pull-role` → Update.

Verify on EC2:

```bash
aws sts get-caller-identity
```

Expected output: an ARN containing `assumed-role/fluxurl-ecr-pull-role/<instance-id>`. If this works, the instance role is correctly attached and serving credentials via IMDS.

```bash
aws ecr describe-repositories --region ap-south-1
```

Should list the `fluxurl` repository. If this works, ECR is reachable with the role's permissions.

### Elastic IP (one-time setup)

Without an Elastic IP, the instance's public IP changes every Stop/Start. With an Elastic IP, the address is permanent.

**1. Allocate**

AWS Console → EC2 → Network & Security → Elastic IPs → Allocate Elastic IP address. Use the default settings (Amazon's IPv4 pool, same region as your instance).

**2. Associate with the instance**

Select the new Elastic IP → Actions → Associate Elastic IP address. Pick the instance from the dropdown. Save.

**3. Update `BASE_URL`**

In `docker-compose.prod.yml`, on the `app` and `migrate` services:

```yaml
app:
  environment:
    BASE_URL: http://<elastic-ip>
migrate:
  environment:
    BASE_URL: http://<elastic-ip>
```

Both services need `BASE_URL` set, even though `migrate` doesn't use it — see `LLD.md` §7.4 for the reason.

Commit, scp to EC2, recreate the containers (see "Deploying a new build to EC2" below).

> **Important:** Once you start handing out short URLs with this Elastic IP, you've committed to keeping it. Releasing the Elastic IP (or replacing it with another) silently breaks every short URL ever generated up to that point.

### Deploying a new build to EC2

This is the manual flow used after Phase 4. Phase 5 will automate it via GitHub Actions; until then, every code change requires this sequence.

**On laptop:**

```bash
# 1. Build the image
docker build -t fluxurl:latest .

# 2. Tag for ECR
docker tag fluxurl:latest 546201496354.dkr.ecr.ap-south-1.amazonaws.com/fluxurl:latest

# 3. Authenticate Docker to ECR (laptop uses your IAM user credentials)
aws ecr get-login-password --region ap-south-1 | \
  docker login --username AWS --password-stdin 546201496354.dkr.ecr.ap-south-1.amazonaws.com

# 4. Push
docker push 546201496354.dkr.ecr.ap-south-1.amazonaws.com/fluxurl:latest
```

**On EC2 (via SSH):**

```bash
# 5. Authenticate Docker to ECR (EC2 uses its instance role; no keys needed)
aws ecr get-login-password --region ap-south-1 | \
  docker login --username AWS --password-stdin 546201496354.dkr.ecr.ap-south-1.amazonaws.com

# 6. Pull the new image
docker compose -f docker-compose.prod.yml pull

# 7. Recreate containers with the new image
docker compose -f docker-compose.prod.yml up -d --force-recreate
```

**Verify from your laptop:**

```bash
curl http://<elastic-ip>/healthz
```

Expected: `{"db": "ok"}`

Notes:
- The ECR auth token (steps 3 and 5) lasts ~12 hours. If `docker pull` returns 401, just re-run the `docker login` step.
- The first push uploads all layers (~75 MB). Subsequent pushes only upload changed layers — usually a few MB.
- `--force-recreate` (step 7) is required to pick up env-var changes and to recreate containers with a newer image even when the tag (`:latest`) hasn't changed.

### When your home IP changes (security group)

Your SSH rule restricts access to "My IP." If you change networks (home → coffee shop → travel), your IP changes and SSH stops working.

To fix:

1. Find your current IP: `curl ifconfig.me` (or visit `whatismyip.com`)
2. AWS Console → EC2 → Security Groups → select `fluxurl-sg`
3. Inbound rules → Edit
4. Change the SSH rule's source to your new IP
5. Save

### Stopping the instance to save free-tier hours

Free tier covers 750 hours/month — about one instance running 24/7. To stretch this further, stop the instance when not actively developing.

**Stop:** AWS Console → EC2 → Instance state → Stop instance.

**Start again:** Instance state → Start instance.

After Start:
- Public IP stays the same (Elastic IP attached)
- Containers auto-restart (`restart: unless-stopped` configured for `app` and `db`)
- No manual `docker compose up` needed — the stack comes back on its own

---

## Troubleshooting

### Local

**"Address already in use" on port 80 or 8000**

Something else on your machine is bound to that port. On Windows, this is often IIS (Internet Information Services). Stop the conflicting service or use a different port mapping in `docker-compose.yml`.

**App container exits immediately with Pydantic ValidationError**

`DATABASE_URL` or `BASE_URL` is missing from your environment. Check `.env` exists in the project root and contains both variables.

**Tests fail with database connection errors**

The DB container needs to be healthy first. Either wait a few seconds and retry, or use `docker compose up -d db && sleep 5 && docker compose run --rm app pytest`.

### EC2

**SSH "Permission denied (publickey)"**

In order of likelihood:
1. Wrong username — Ubuntu AMIs use `ubuntu`, not `root` or your laptop username
2. Wrong path to the `.pem` file
3. Permissions on the `.pem` file too open (Linux: `chmod 400`, Windows: see prerequisites)
4. Wrong IP — has it changed? Did you stop/start without an Elastic IP?

**SSH "Connection timed out"**

Almost always the security group. Your home IP has probably changed since you set the SSH rule. See "When your home IP changes" above.

**ECR auth fails: "no basic auth credentials" or 401**

The ECR auth token has expired (~12-hour lifetime). Re-run the `aws ecr get-login-password | docker login` step.

**`aws sts get-caller-identity` works but `aws ecr describe-repositories` fails with AccessDenied**

The instance role is attached but doesn't have ECR permissions. Verify the role has `AmazonEC2ContainerRegistryReadOnly` attached (IAM console → Roles → `fluxurl-ecr-pull-role` → Permissions).

**`docker pull` from ECR is slow or hangs**

Outbound HTTPS may be restricted somewhere upstream. The instance needs to reach `*.amazonaws.com` and `registry-1.docker.io` (the latter for the Postgres image). Check the security group's outbound rules — by default these are unrestricted.

**Short URLs show `localhost:8000` instead of the EC2 IP**

Either `BASE_URL` isn't set in the compose file on EC2, or the container wasn't recreated after the change. Run `docker compose -f docker-compose.prod.yml up -d --force-recreate` and verify with `docker exec fluxurl-app printenv BASE_URL`.

**Public IP changed after I stopped/started the instance**

You don't have an Elastic IP attached. See "Elastic IP (one-time setup)" above.

**Containers aren't running after a reboot**

The restart policy should bring them back automatically — `restart: unless-stopped` is set on `app` and `db`. If they're not running:
1. Check `docker compose -f docker-compose.prod.yml ps` for status
2. Check `docker compose -f docker-compose.prod.yml logs app` for crash details
3. If the policy is missing, verify `docker inspect fluxurl-app | grep -A 1 RestartPolicy` shows `unless-stopped`

**`migrate` exits with Pydantic ValidationError**

`migrate` runs the same image as `app` and imports `get_settings()` via `alembic/env.py`. If any required env var (including `BASE_URL`) is missing from migrate's `environment:` block in `docker-compose.prod.yml`, migrate fails to start. Verify both `DATABASE_URL` and `BASE_URL` are set on the `migrate` service.

---

## Reference

### Key files

- `Dockerfile` — multi-stage image build (builder: full Python, runtime: slim)
- `docker-compose.yml` — local dev (uses `build: .`, exposes 8000 and 5432)
- `docker-compose.prod.yml` — EC2 prod (uses ECR image URI, exposes 80 only, restart policies set)
- `alembic/` — database migrations
- `Docs/HLD.md` — high-level design
- `Docs/LLD.md` — low-level design
- `Docs/decisions/` — Architecture Decision Records (ADRs)
- `NOTES.md` — concepts learned during the project

### Environment variables

| Variable | Used by | Required? | Notes |
|---|---|---|---|
| `DATABASE_URL` | app, migrate | **Yes** | No default. App fails to start if missing. |
| `BASE_URL` | app, migrate | **Yes** | No default. Required on migrate too because alembic/env.py imports get_settings(). |
| `POSTGRES_USER` | db | Yes | `fluxurl` |
| `POSTGRES_PASSWORD` | db | Yes | `fluxurl` |
| `POSTGRES_DB` | db | Yes | `fluxurl` |

### Compose file selection

- Local: `docker compose up` (uses `docker-compose.yml` by default)
- Production: `docker compose -f docker-compose.prod.yml <command>` (must specify explicitly)

### AWS resources (production)

| Resource | Identifier | Region |
|---|---|---|
| EC2 instance | `i-0ba70062d8f85852b` | ap-south-1 |
| Elastic IP | `3.109.34.168` | ap-south-1 |
| ECR repository | `546201496354.dkr.ecr.ap-south-1.amazonaws.com/fluxurl` | ap-south-1 |
| IAM instance role | `fluxurl-ecr-pull-role` | global |
| Security group | `fluxurl-sg` (SSH from My IP, HTTP from anywhere) | ap-south-1 |