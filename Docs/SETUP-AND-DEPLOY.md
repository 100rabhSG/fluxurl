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

Or, faster (no Postgres needed — tests use in-memory SQLite via `conftest.py`):

```bash
pytest
```

Tests work without `.env` present because `conftest.py` sets dummy `DATABASE_URL` and `BASE_URL` via `os.environ.setdefault()` before importing app modules. This matches what CI does on GitHub Actions runners.

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

Routine deploys are fully automated as of Phase 5. The flow is:

```
git push origin master  →  CI (lint, test, build, push, deploy)  →  production live
```

That's it — no SSH, no manual commands, no human in the loop. The pipeline runs in ~3-5 minutes from push to production.

This section covers:
1. **One-time infrastructure setup** — done once when bootstrapping the project (already done for this instance)
2. **Routine deploys** — what to do for every code change (`git push`)
3. **Break-glass manual deploys** — the manual fallback if CI is broken and you need to ship urgently

The current production architecture (end of Phase 5):

- **Compute:** single EC2 instance (t3.micro, Ubuntu 22.04) in `ap-south-1`
- **Public address:** Elastic IP `3.109.34.168` (replace with your own if forking)
- **Image source:** ECR private repository `546201496354.dkr.ecr.ap-south-1.amazonaws.com/fluxurl`
- **CI/CD:** GitHub Actions, workflow at `.github/workflows/ci.yml`
- **CI → AWS auth:** OIDC federation; IAM role `fluxurl-github-actions-role` assumed per workflow run
- **CI → EC2 deploy:** SSM Run Command (no SSH from GitHub)
- **EC2 → ECR auth:** IAM instance role `fluxurl-ecr-pull-role` (no AWS keys on the box)
- **Laptop → ECR auth (break-glass only):** IAM user `saurabh-admin` via `~/.aws/credentials`

For the *why* behind these choices, see `HLD.md` §5.2 and `LLD.md` §9–10.

### Prerequisites

- AWS account with MFA on root and an IAM admin user for daily work
- AWS CLI configured locally (`aws configure`) — needed for break-glass and verification commands
- A budget alarm set in AWS Billing (recommended: $1)
- SSH key pair downloaded as `.pem` and stored securely (e.g., `~/.ssh/fluxurl-key.pem`) — needed for emergency access; not used in routine deploys

### Routine deploy: `git push origin master`

The full flow:

```bash
git add <files>
git commit -m "your change"
git push origin master
```

Then watch the workflow run in the GitHub Actions tab. Four jobs:

| Job | Runs on | Duration |
|---|---|---|
| `lint` | every push, every PR | ~15 s |
| `test` | every push, every PR | ~20 s |
| `build-and-push` | master only | ~2-3 min |
| `deploy` | master only | ~30-60 s |

When all four are green, production has been updated. Verify with:

```bash
curl http://3.109.34.168/healthz
```

Expected: `{"db": "ok"}`

To verify that the running container is the latest build:

```bash
ssh -i ~/.ssh/fluxurl-key.pem ubuntu@3.109.34.168 \
  "docker inspect fluxurl-app --format '{{.Image}}'"
```

The output is a `sha256:...` digest. Compare it to the most recent image in ECR:

```bash
aws ecr describe-images --repository-name fluxurl --region ap-south-1 \
  --query "imageDetails[?contains(imageTags, 'latest')].imageDigest"
```

The two digests should match.

For the white-box mechanics of what each job does, see `LLD.md` §10.

### One-time infrastructure setup

This section is the bootstrapping sequence — what to run *once* when setting up the project on a new AWS account. Everything here is already done for the current production instance; you'd only repeat it on a fresh AWS account or for a forked project.

#### EC2 instance

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

**5. Verify**

```bash
docker --version
docker compose version
aws --version
docker run hello-world
sudo systemctl status amazon-ssm-agent
```

The SSM agent should be `active (running)` — required for CI/CD deploys to work. If it shows `inactive` or `not-found`, install it:

```bash
sudo snap install amazon-ssm-agent --classic
sudo systemctl start snap.amazon-ssm-agent.amazon-ssm-agent.service
sudo systemctl enable snap.amazon-ssm-agent.amazon-ssm-agent.service
```

#### ECR + EC2 instance role

**1. Create the ECR repository**

In the AWS console: ECR → Private registry → Create repository:
- Repository name: `fluxurl`
- Tag immutability: Disabled (mutable, for `:latest` workflow — see [ADR 0011](decisions/0011-image-tagging-strategy.md))
- Scan on push: Enabled

The repository URI will be `<account-id>.dkr.ecr.ap-south-1.amazonaws.com/fluxurl`.

**2. Set the ECR lifecycle policy**

ECR → fluxurl repository → Lifecycle Policy → Create rule. Paste this JSON:

```json
{
  "rules": [
    {
      "rulePriority": 1,
      "description": "Keep last 5 tagged images",
      "selection": {
        "tagStatus": "tagged",
        "tagPatternList": ["*"],
        "countType": "imageCountMoreThan",
        "countNumber": 5
      },
      "action": { "type": "expire" }
    },
    {
      "rulePriority": 2,
      "description": "Delete untagged images after 7 days",
      "selection": {
        "tagStatus": "untagged",
        "countType": "sinceImagePushed",
        "countUnit": "days",
        "countNumber": 7
      },
      "action": { "type": "expire" }
    }
  ]
}
```

Keeps storage well under free-tier limits and preserves a 5-version rollback window.

**3. Create the IAM role for EC2**

IAM → Roles → Create role:
- Trusted entity: AWS service → EC2
- Permissions policies: attach both
  - `AmazonEC2ContainerRegistryReadOnly`
  - `AmazonSSMManagedInstanceCore`
- Name: `fluxurl-ecr-pull-role`

The first policy lets EC2 pull from ECR. The second lets the SSM agent on EC2 communicate with the SSM service (required for CI/CD deploys).

**4. Attach the role to the EC2 instance**

EC2 → Instances → select instance → Actions → Security → Modify IAM role → select `fluxurl-ecr-pull-role` → Update.

**5. Verify**

On EC2:

```bash
aws sts get-caller-identity
```

Expected output: an ARN containing `assumed-role/fluxurl-ecr-pull-role/<instance-id>`. If this works, the instance role is correctly attached and serving credentials via IMDS.

```bash
aws ecr describe-repositories --region ap-south-1
```

Should list the `fluxurl` repository.

From your laptop:

```bash
aws ssm describe-instance-information --region ap-south-1 \
  --query "InstanceInformationList[*].[InstanceId,PingStatus]"
```

Should show your instance with `PingStatus: Online`. If not, the SSM agent isn't talking to AWS — restart it on EC2 with `sudo systemctl restart amazon-ssm-agent` and wait a minute.

#### GitHub Actions IAM setup (OIDC)

**1. Register GitHub's OIDC provider in IAM**

IAM → Identity providers → Add provider:
- Provider type: OpenID Connect
- Provider URL: `https://token.actions.githubusercontent.com`
- Audience: `sts.amazonaws.com`

Done once per AWS account.

**2. Create the GitHub Actions role**

IAM → Roles → Create role:
- Trusted entity type: Web identity
- Identity provider: `token.actions.githubusercontent.com`
- Audience: `sts.amazonaws.com`
- GitHub organization: your GitHub username (or org)
- GitHub repository: `fluxurl`
- Don't attach permissions in the wizard; add them after creation
- Name: `fluxurl-github-actions-role`

**3. Tighten the trust policy**

After creation, find the role → Trust relationships tab → Edit trust policy. Replace the `Condition` block to scope to `master` only:

```json
{
  "Version": "2012-10-17",
  "Statement": [
    {
      "Effect": "Allow",
      "Principal": {
        "Federated": "arn:aws:iam::546201496354:oidc-provider/token.actions.githubusercontent.com"
      },
      "Action": "sts:AssumeRoleWithWebIdentity",
      "Condition": {
        "StringEquals": {
          "token.actions.githubusercontent.com:aud": "sts.amazonaws.com"
        },
        "StringLike": {
          "token.actions.githubusercontent.com:sub": "repo:<your-github-username>/fluxurl:ref:refs/heads/master"
        }
      }
    }
  ]
}
```

The `sub` claim is the security boundary — it scopes role assumption to workflows on `master` of this specific repo. Forks and other branches cannot assume the role.

**4. Attach inline policies**

Two inline policies, both minimal least-privilege.

**`ecr-push-fluxurl`** (lets CI push to the fluxurl ECR repo):

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

**`ssm-deploy-fluxurl`** (lets CI send SSM commands to the specific EC2 instance):

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

For the explanation of each statement, see `LLD.md` §10.4.

#### Elastic IP

Without an Elastic IP, the instance's public IP changes every Stop/Start. With an Elastic IP, the address is permanent.

**1. Allocate**

AWS Console → EC2 → Network & Security → Elastic IPs → Allocate Elastic IP address. Use the default settings (Amazon's IPv4 pool, same region as your instance).

**2. Associate with the instance**

Select the new Elastic IP → Actions → Associate Elastic IP address. Pick the instance from the dropdown. Save.

**3. Update `BASE_URL`**

In `docker-compose.prod.yml`, on both the `app` and `migrate` services:

```yaml
app:
  environment:
    BASE_URL: http://<elastic-ip>
migrate:
  environment:
    BASE_URL: http://<elastic-ip>
```

Both services need `BASE_URL` set, even though `migrate` doesn't use it — see `LLD.md` §7.4 for the reason.

Commit, scp to EC2, recreate the containers (see the manual deploy section below, or just push to master to trigger CI/CD).

> **Important:** Once you start handing out short URLs with this Elastic IP, you've committed to keeping it. Releasing the Elastic IP (or replacing it with another) silently breaks every short URL ever generated up to that point.

#### Initial deploy to populate the compose file on EC2

The CI/CD pipeline assumes `docker-compose.prod.yml` already exists at `/home/ubuntu/` on the EC2 instance. The deploy step runs commands relative to that file but does not copy it. For the very first deploy:

```bash
scp -i ~/.ssh/fluxurl-key.pem docker-compose.prod.yml ubuntu@<elastic-ip>:~/
```

After this, CI handles deploys. The compose file only needs to be re-`scp`'d if it changes (rare — env vars and service definitions; the image URI is stable).

### Break-glass: manual deploy

When CI is broken (workflow misconfigured, GitHub Actions outage, urgent fix needed before pipeline is fixed), the manual deploy path remains available.

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
- This path is for emergencies. Routine deploys should use `git push origin master`.
- The ECR auth token (steps 3 and 5) lasts ~12 hours. If `docker pull` returns 401, just re-run the `docker login` step.
- The manual path uses the laptop's IAM user credentials, which have broader permissions than CI's role. Use sparingly.
- After a manual deploy, the image in ECR is *not* tagged with a git SHA (unlike CI). To restore traceability, run a `git push` once the fix is committed — CI will re-build and re-push with the SHA tag.

### When your home IP changes (security group)

Your SSH rule restricts access to "My IP." If you change networks (home → coffee shop → travel), your IP changes and SSH stops working.

To fix:

1. Find your current IP: `curl ifconfig.me` (or visit `whatismyip.com`)
2. AWS Console → EC2 → Security Groups → select `fluxurl-sg`
3. Inbound rules → Edit
4. Change the SSH rule's source to your new IP
5. Save

**Note:** CI/CD deploys are unaffected by this. SSM uses the agent's outbound connection; only your SSH access depends on the security group rule.

### Stopping the instance to save free-tier hours

Free tier covers 750 hours/month — about one instance running 24/7. To stretch this further, stop the instance when not actively developing.

**Stop:** AWS Console → EC2 → Instance state → Stop instance.

**Start again:** Instance state → Start instance.

After Start:
- Public IP stays the same (Elastic IP attached)
- Containers auto-restart (`restart: unless-stopped` configured for `app` and `db`)
- The SSM agent reconnects automatically — CI/CD deploys work without intervention

---

## Troubleshooting

### Local

**"Address already in use" on port 80 or 8000**

Something else on your machine is bound to that port. On Windows, this is often IIS (Internet Information Services). Stop the conflicting service or use a different port mapping in `docker-compose.yml`.

**App container exits immediately with Pydantic ValidationError**

`DATABASE_URL` or `BASE_URL` is missing from your environment. Check `.env` exists in the project root and contains both variables.

**Tests fail with database connection errors**

If running inside Docker, the DB container needs to be healthy first. Either wait a few seconds and retry, or use `docker compose up -d db && sleep 5 && docker compose run --rm app pytest`.

If running tests directly (`pytest` on the host), this shouldn't happen — `conftest.py` uses in-memory SQLite and doesn't need a database container.

### CI / GitHub Actions

**Workflow doesn't trigger on push**

- Check the file is at `.github/workflows/ci.yml` (not `.yaml`, not in a subdirectory)
- Check the `on:` block matches the event (e.g., `push` to the branch you pushed)

**`build-and-push` fails at "Configure AWS credentials" with `Not authorized to perform sts:AssumeRoleWithWebIdentity`**

Three likely causes, in order:
1. `permissions: id-token: write` missing from the job YAML. This is required for the runner to mint an OIDC token.
2. The trust policy's `sub` claim doesn't match. Verify it's `repo:<your-username>/fluxurl:ref:refs/heads/master` exactly. Typos here are silent failures.
3. The OIDC provider isn't registered in IAM. Check IAM → Identity providers for `token.actions.githubusercontent.com`.

**`build-and-push` fails at "Login to ECR"**

The GitHub Actions role doesn't have `ecr:GetAuthorizationToken`. Check the `ecr-push-fluxurl` inline policy is attached.

**`build-and-push` fails at `docker push`**

Either the role lacks the specific push actions, or the resource ARN in the inline policy doesn't match the repo URI. Verify `ecr:PutImage` and friends are present and scoped to the right repo ARN.

**`deploy` fails at "Send deploy command via SSM" with `InvalidInstanceId`**

The SSM agent on EC2 isn't running or hasn't registered with SSM.

1. SSH in: `sudo systemctl status amazon-ssm-agent` — should be active
2. From laptop: `aws ssm describe-instance-information --region ap-south-1` — instance should be `PingStatus: Online`
3. If offline: restart the agent (`sudo systemctl restart amazon-ssm-agent`), wait a minute, retry

**`deploy` waits a long time then fails**

The agent received the command but is taking longer than expected. SSH in and check `/var/log/amazon/ssm/amazon-ssm-agent.log` for what's happening. Most often: a slow `docker pull` over a constrained network.

**`deploy` reports completed but production didn't update**

Look at the "Show command output" step in the workflow logs. Even with `set -e`, a subtle issue (wrong compose file path, missing env var) can cause unexpected behavior. The output of every deploy command is captured there.

**Production keeps serving the old image after deploy succeeds**

Verify the image actually got recreated:

```bash
ssh -i ~/.ssh/fluxurl-key.pem ubuntu@3.109.34.168 \
  "docker inspect fluxurl-app --format '{{.Image}}'"
```

Compare against the most recent image digest in ECR. If they differ, `--force-recreate` didn't trigger — usually means the compose file on EC2 is out of date (didn't `scp` after editing).

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

The instance role is attached but doesn't have ECR permissions. Verify both `AmazonEC2ContainerRegistryReadOnly` and `AmazonSSMManagedInstanceCore` are attached (IAM console → Roles → `fluxurl-ecr-pull-role` → Permissions).

**`docker pull` from ECR is slow or hangs**

Outbound HTTPS may be restricted somewhere upstream. The instance needs to reach `*.amazonaws.com` (ECR, SSM) and `registry-1.docker.io` (Postgres image). Check the security group's outbound rules — by default these are unrestricted.

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
- `.github/workflows/ci.yml` — CI/CD workflow (lint, test, build-and-push, deploy)
- `alembic/` — database migrations
- `Docs/HLD.md` — high-level design
- `Docs/LLD.md` — low-level design (CI/CD mechanics in §10)
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

In CI, tests set `DATABASE_URL` and `BASE_URL` to dummy values via `conftest.py` — see `LLD.md` §7.4.

### Compose file selection

- Local: `docker compose up` (uses `docker-compose.yml` by default)
- Production: `docker compose -f docker-compose.prod.yml <command>` (must specify explicitly)

### AWS resources (production)

| Resource | Identifier | Region |
|---|---|---|
| EC2 instance | `i-0ba70062d8f85852b` | ap-south-1 |
| Elastic IP | `3.109.34.168` | ap-south-1 |
| ECR repository | `546201496354.dkr.ecr.ap-south-1.amazonaws.com/fluxurl` | ap-south-1 |
| EC2 instance role | `fluxurl-ecr-pull-role` (ECR read + SSM agent) | global |
| GitHub Actions role | `fluxurl-github-actions-role` (ECR push + SSM SendCommand) | global |
| OIDC provider | `token.actions.githubusercontent.com` | global |
| Security group | `fluxurl-sg` (SSH from My IP, HTTP from anywhere) | ap-south-1 |