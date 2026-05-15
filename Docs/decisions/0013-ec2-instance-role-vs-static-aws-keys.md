# ADR 0013: EC2 authentication to ECR via instance role, not static AWS keys

**Status:** Accepted  
**Date:** 2026-05-15  
**Phase:** 4 (ECR + IAM instance role)

## Context

In Phase 4, the EC2 host must authenticate to ECR to pull runtime images. The design choice is whether EC2 should use static AWS access keys stored on disk or obtain temporary credentials through an attached IAM role. The project goal explicitly emphasizes learning correct AWS identity patterns and least privilege.

## Options considered

### Option A: IAM instance role on EC2 (recommended AWS pattern)

- Pros:
  - No long-lived AWS keys stored on the server.
  - Temporary credentials are rotated automatically by AWS.
  - Permissions can be tightly scoped to pull-only ECR actions.
  - Better compromise blast-radius profile than static keys.
- Cons:
  - Requires role creation, trust policy, and attachment steps.
  - Adds conceptual overhead (IMDS and credential chain).

### Option B: Static IAM user keys on EC2

- Pros:
  - Familiar setup for beginners.
  - Works in any environment with only access key + secret key.
- Cons:
  - Long-lived credentials on disk are a high-risk secret management pattern.
  - Key rotation is manual and easy to neglect.
  - Harder to prove least privilege and role separation cleanly.

### Option C: Build and run only local images on EC2 (no registry auth)

- Pros:
  - Avoids AWS auth on the host entirely.
  - Fewer moving parts for a single machine.
- Cons:
  - Regresses from Phase 4 objective (registry + IAM).
  - Not suitable foundation for CI/CD in Phase 5.

## Decision

Use an EC2 IAM instance role with AmazonEC2ContainerRegistryReadOnly for ECR pulls, and do not store static AWS keys on EC2.

This decision aligns directly with least privilege and with AWS-recommended credential handling for compute workloads. It also cleanly separates principals: push from operator/CI identity, pull from runtime host identity. The additional setup complexity is intentional learning value, not accidental overhead.

## Consequences

- Easier:
  - Secure-by-default server auth posture (no credential files to protect/rotate).
  - Clear ownership boundaries between build/push and runtime/pull identities.
  - Better Phase 5 readiness (CI can push, EC2 can pull, independent credentials).
- Harder:
  - More failure modes to understand (role detached, IMDS issues, policy mismatch).
  - Requires familiarity with AWS credential chain and metadata service behavior.
- Revisit:
  - If moving to ECS/EKS later, identity mechanism changes form (task role/service account), but the same no-static-keys principle should remain.

## Notes

A practical diagnostic for this setup is that the effective identity on EC2 should resolve to an assumed role principal and temporary session credentials, not an IAM user with long-lived keys.
