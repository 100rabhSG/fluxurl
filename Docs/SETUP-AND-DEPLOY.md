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

**2. Build the image**

```bash
docker build -t fluxurl:latest .
```

**3. Start the stack**

```bash
docker compose up
```

This starts three containers:
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
docker compose logs db          # one-shot db logs
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

This section covers manual deployment to EC2. CI/CD (GitHub Actions → ECR → EC2) is the eventual goal, but the manual flow comes first.

### Prerequisites

- AWS account with MFA on root and an IAM admin user for daily work
- AWS CLI configured locally (`aws configure`)
- A budget alarm set in AWS Billing (recommended: $1)
- SSH key pair downloaded as `.pem` and stored securely (e.g., `~/.ssh/fluxurl-key.pem`)

### Initial EC2 setup

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

**4. Install Docker on the instance**

```bash
sudo apt-get update && sudo apt-get upgrade -y
sudo apt-get install -y ca-certificates curl gnupg lsb-release

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
docker run hello-world
```

### Deploying a new build to EC2

**1. Build the image locally**

```bash
docker build -t fluxurl:latest .
```

**2. Save the image as a tar file**

```bash
docker save fluxurl:latest -o fluxurl.tar
```

**3. Copy the tar to EC2**

```bash
scp -i ~/.ssh/fluxurl-key.pem fluxurl.tar ubuntu@<elastic-ip>:~/
```

**4. Copy the production compose file**

```bash
scp -i ~/.ssh/fluxurl-key.pem docker-compose.prod.yml ubuntu@<elastic-ip>:~/
```

**5. SSH in and load the image**

```bash
ssh -i ~/.ssh/fluxurl-key.pem ubuntu@<elastic-ip>
```

On EC2:

```bash
docker load -i fluxurl.tar
rm fluxurl.tar
```

**6. Start (or restart) the stack**

```bash
docker compose -f docker-compose.prod.yml up -d --force-recreate
```

The `--force-recreate` flag ensures containers pick up any env var changes from the compose file.

**7. Verify**

From your laptop:

```bash
curl http://<elastic-ip>/healthz
```

Expected: `{"db": "ok"}`

### Elastic IP (one-time setup)

Without an Elastic IP, the instance's public IP changes every Stop/Start. With an Elastic IP, the address is permanent.

**1. Allocate**

AWS Console → EC2 → Network & Security → Elastic IPs → Allocate Elastic IP address. Use the default settings (Amazon's IPv4 pool, same region as your instance).

**2. Associate with the instance**

Select the new Elastic IP → Actions → Associate Elastic IP address. Pick the instance from the dropdown. Save.

**3. Update `BASE_URL`**

In `docker-compose.prod.yml`:

```yaml
app:
  environment:
    BASE_URL: http://<elastic-ip>
```

Commit, scp to EC2, recreate the container (see "Deploying a new build to EC2" above).

> **Important:** Once you start handing out short URLs with this Elastic IP, you've committed to keeping it. Releasing the Elastic IP (or replacing it with another) silently breaks every short URL ever generated up to that point.

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
- Public IP stays the same **only if** you have an Elastic IP attached
- Containers do **not** auto-restart — you'll need to SSH in and run `docker compose -f docker-compose.prod.yml up -d`

---

## Troubleshooting

### Local

**"Address already in use" on port 80 or 8000**

Something else on your machine is bound to that port. On Windows, this is often IIS (Internet Information Services). Stop the conflicting service or use a different port mapping in `docker-compose.yml`.

**"Image not found" after pulling the repo**

You haven't built the image yet. Run `docker build -t fluxurl:latest .` first.

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

**Short URLs show `localhost:8000` instead of the EC2 IP**

The `BASE_URL` env var isn't set in the compose file on EC2, or the container wasn't recreated after the change. Run `docker compose -f docker-compose.prod.yml up -d --force-recreate` and try again.

**Public IP changed after I stopped/started the instance**

You don't have an Elastic IP attached. See "Elastic IP (one-time setup)" above.

**Containers aren't running after a reboot**

Currently expected behavior — no restart policy is configured. SSH in and run `docker compose -f docker-compose.prod.yml up -d`. Adding `restart: unless-stopped` to each service in `docker-compose.prod.yml` would fix this; that's a deliberate Phase 3 follow-up.

---

## Reference

### Key files

- `Dockerfile` — multi-stage image build (builder: full Python, runtime: slim)
- `docker-compose.yml` — local dev (uses `build: .`, exposes 8000 and 5432)
- `docker-compose.prod.yml` — EC2 prod (uses `image: fluxurl:latest`, exposes 80 only)
- `alembic/` — database migrations
- `Docs/HLD.md` — high-level design
- `Docs/LLD.md` — low-level design
- `Docs/decisions/` — Architecture Decision Records (ADRs)
- `NOTES.md` — concepts learned during the project

### Environment variables

| Variable | Used by | Default | Required? |
|---|---|---|---|
| `DATABASE_URL` | app, migrate | — | yes |
| `BASE_URL` | app | `http://localhost:8000` | should be required in prod |
| `POSTGRES_USER` | db | `fluxurl` | yes |
| `POSTGRES_PASSWORD` | db | `fluxurl` | yes |
| `POSTGRES_DB` | db | `fluxurl` | yes |

### Compose file selection

- Local: `docker compose up` (uses `docker-compose.yml` by default)
- Production: `docker compose -f docker-compose.prod.yml up -d` (must specify explicitly)
