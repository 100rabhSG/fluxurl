# ADR 0012: ECR repository visibility for Fluxurl runtime image

**Status:** Accepted  
**Date:** 2026-05-15  
**Phase:** 4 (ECR + IAM instance role)

## Context

Phase 4 moves image distribution from manual tar-copy to a registry-backed flow. The production image contains application code and deployment details and is intended only for this project's EC2 host and deployment operator. The project is a learning setup with one operator, free-tier cost constraints, and no requirement to distribute images publicly.

## Options considered

### Option A: ECR private repository

- Pros:
  - Image access is restricted by IAM policy.
  - Works cleanly with EC2 instance role pull model.
  - Supports least-privilege separation between push and pull principals.
  - Matches likely real-world production defaults.
- Cons:
  - Requires authentication for both push and pull.
  - Slightly more operational steps than a public registry.

### Option B: ECR public repository

- Pros:
  - Simpler pull path for unauthenticated consumers.
  - Useful if the image must be shared externally.
- Cons:
  - Not aligned with current use case (single private EC2 consumer).
  - Increases accidental exposure risk for internal build artifacts.
  - Does not exercise IAM pull-auth learning objective as strongly.

### Option C: No registry (continue tar copy)

- Pros:
  - Conceptually simple for a single box.
  - No registry auth setup required.
- Cons:
  - Not scalable and operationally awkward.
  - Couples build machine directly to runtime host.
  - Blocks clean CI/CD evolution in Phase 5.

## Decision

Use an ECR private repository for Fluxurl runtime images in Phase 4.

This best fits the learning goal of understanding IAM-based runtime auth while preserving least privilege and minimizing artifact exposure. Private ECR introduces slightly more auth ceremony than public ECR, but that ceremony is exactly the operational path needed for production-like practice and Phase 5 automation.

## Consequences

- Easier:
  - Enforce who can push and who can pull via IAM.
  - Keep deployment artifacts non-public by default.
  - Transition to CI/CD without changing registry architecture.
- Harder:
  - Manual deploys require periodic ECR login token refresh.
  - Debugging includes an additional auth layer (IAM + ECR token).
- Revisit:
  - If Fluxurl images need public distribution (for demos or reuse), reconsider public publishing strategy in a future phase.

## Notes

The key architectural seam is registry decoupling, not just storage location: producers push to ECR, consumers pull from ECR, and neither side needs direct file transfer to the other.
