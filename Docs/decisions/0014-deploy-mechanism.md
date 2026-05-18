# ADR 0014: Deploy mechanism — SSM Run Command for CI-to-EC2 deploys

**Status:** Accepted
**Date:** 2026-05-15
**Phase:** 5 (CI/CD)

## Context

Phase 5 introduces CI/CD via GitHub Actions. After CI builds and pushes a new image to ECR, something has to tell the EC2 instance to pull and recreate containers with the new image. The mechanism shapes the auth model (whether long-lived secrets are needed), the network surface (whether new inbound ports must be opened), the audit trail (whether deploys are traceable to a workflow run), and the operational complexity of every future deploy.

The project goal continues to emphasize correct AWS identity patterns, least privilege, and consistency with the auth model established in Phases 3 and 4 (compute identity assumed dynamically, no long-lived credentials).

## Options considered

### Option A: SSH from the GitHub Actions runner to EC2

- Pros:
  - Conceptually simple; SSH is universally understood.
  - Works for any host, not just AWS — portable if the deploy target ever changes.
  - Easy to test manually (same SSH the operator already uses).
- Cons:
  - Requires storing a long-lived SSH private key in GitHub Secrets — exactly the credential-storage pattern Phase 4 eliminated for EC2-to-AWS auth.
  - Requires opening port 22 to GitHub's runner IP ranges (large, changing) or to `0.0.0.0/0` (worse).
  - No AWS-side audit trail; CloudTrail records nothing about the deploy.
  - Reverses the architectural discipline of the prior two phases.

### Option B: AWS Systems Manager (SSM) Run Command

- Pros:
  - Zero long-lived credentials in GitHub Secrets. The GitHub Actions role (assumed via OIDC) gets `ssm:SendCommand` scoped to the specific EC2 instance.
  - No inbound network changes. The SSM agent on EC2 connects outbound to AWS; commands route through that pre-established channel.
  - Every deploy is logged in CloudTrail with the assumed-role identity and the commit that triggered it.
  - Consistent with the Phase 3 and 4 auth model: compute proves what it is, AWS issues short-lived credentials.
- Cons:
  - AWS-specific; locks the deploy mechanism into AWS Systems Manager.
  - Requires the SSM agent installed and running on the instance (Ubuntu 22.04 includes it pre-installed and enabled by default).
  - Requires extending the EC2 instance role with `AmazonSSMManagedInstanceCore` so the agent can communicate with the SSM service.
  - Slightly slower than direct SSH (routing through AWS's control plane adds a few seconds per deploy).

### Option C: Pull-based deployment (Watchtower or equivalent)

- Pros:
  - Most decoupled — CI/CD has zero knowledge of the deploy target. Push to ECR and the instance picks it up on its own.
  - No deploy credentials needed in CI at all; ECR push permission is sufficient.
  - Scales naturally to multi-instance fleets.
  - Self-healing — an instance coming back from outage pulls the latest on next poll.
- Cons:
  - Polling delay; deploys take effect at the next poll cycle, not immediately.
  - Less control over deploy timing and less correlation between "image pushed" and "production updated."
  - Extra always-running container consuming resources on the t3.micro.
  - More moving parts to understand and debug (Watchtower's own configuration, failure modes, logs).
  - Overkill for a single-instance deployment.

## Decision

Use SSM Run Command (Option B) for CI-to-EC2 deploys.

The GitHub Actions role, assumed via OIDC federation, gets `ssm:SendCommand` permission scoped to the specific EC2 instance ARN. The instance role (`fluxurl-ecr-pull-role`) is extended with `AmazonSSMManagedInstanceCore` so the agent can communicate with the SSM service. No SSH keys are stored anywhere; no inbound ports beyond the existing SSH (operator-only) and HTTP (public) need to change.

This decision is consistent with the auth model established in Phases 3 and 4, and avoids reintroducing long-lived credentials that the prior phases deliberately eliminated. The principal-separation discipline holds: push from CI identity, pull from runtime host identity, deploy commands routed through AWS's control plane.

## Consequences

- Easier:
  - No long-lived deploy credentials to manage, rotate, or worry about leaking.
  - No new inbound ports on the EC2 security group; SSH stays locked to operator IP.
  - Every deploy is traceable to a specific workflow run and commit via CloudTrail.
  - Tight least-privilege scoping: the deploy role can only invoke commands on the named EC2 instance.
- Harder:
  - AWS-specific mechanism — if the project ever moves off AWS, the deploy step needs rewriting. Acceptable: the rest of the v1 infrastructure (ECR, EC2, RDS in Phase 7) already commits to AWS.
  - SSM agent becomes a critical-path dependency; its health is an operational concern, though it is itself observable via SSM.
  - Slightly more moving parts than SSH (the agent, the SSM service, the role permissions on both sides) — but each piece is a learnable, documented part of AWS.
- Revisit:
  - If Fluxurl ever scales beyond a single instance (Phase 11+ territory) or moves to a Kubernetes-based runtime, pull-based reconciliation becomes more compelling. The single-instance scale is what makes Option C overkill today.
  - If CloudWatch Logs integration is added in Phase 8, SSM command output should be streamed there alongside application logs for unified incident visibility.

## Notes

The diagnostic for this setup is that a deploy command sent from GitHub Actions appears in CloudTrail attributed to the assumed GitHub Actions role, with the originating commit SHA in the command's `--comment` field, and runs on EC2 under the SSM agent — not as the result of any inbound SSH connection.

The general principle this reflects: route system-to-system commands through the cloud control plane, not direct network connections. Auth, audit, and least-privilege become first-class concerns rather than implicit properties of "whoever has the key."