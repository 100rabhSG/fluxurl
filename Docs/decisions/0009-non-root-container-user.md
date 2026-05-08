# ADR 0009: Run container as a non-root user (`appuser`, UID 1000)

- **Status:** Accepted
- **Date:** 2026-05-08
- **Phase:** 2 (Dockerize)

## Context

The Dockerfile from ADRs 0007–0008 currently runs as `root` — the default user in `python:3.12-slim`. Once the container is exposed to the public internet on EC2, every line of code inside executes as UID 0. That UID maps to UID 0 on the host kernel; only namespaces, cgroups, and capability dropping prevent in-container root from becoming host-root.

That separation usually holds, but root inside the container is the *amplifier* in any compromise. If the app has an RCE (malicious dep, deserialization bug, path traversal), the attacker can read every file, modify the running app, and has the shortest possible path to host-root the day a separate vulnerability appears (kernel CVE, Docker daemon CVE, accidentally writable mount).

The principle: **build phase and runtime phase have different threat models.** Build runs once on a trusted machine and needs privileges to install packages — root is appropriate. Runtime is long-lived, internet-facing, and is where any compromise lands — root is *inappropriate*. The same separation the project already applies at the AWS account level (root for setup, IAM for ongoing work, captured in `NOTES.md`).

## Options considered

### Option A: Run as root (status quo)

- **Pros:** Zero Dockerfile changes. No file-permission concerns.
- **Cons:** Maximum amplification factor for any in-container compromise. Hard to defend without a specific reason to *prefer* root.

### Option B: Bake a non-root user into the image (`appuser`, UID 1000)

Add `RUN useradd -u 1000 -m appuser` and `USER appuser` after all build-time steps. Build still runs as root; runtime drops to `appuser`.

- **Pros:**
  - Removes the amplifier permanently — every running process is non-privileged.
  - Defense in depth at near-zero cost; matches industry consensus for production containers.
  - Build-time privilege preserved for the steps that genuinely need it.
- **Cons:**
  - Cannot bind to ports < 1024. Irrelevant today (we use 8000).
  - File-ownership bookkeeping if the runtime ever writes to disk. Trivial today — we don't.

### Option C: Don't bake it in; force `--user 1000:1000` at runtime

Leave the image as root and rely on operators to pass `docker run --user ...`.

- **Pros:** Image stays simple. Operator can override per-deploy.
- **Cons:** Inverts secure-by-default — the safe path requires extra effort, the unsafe path is the default. Easy to forget under pressure. No human operator typing flags in the EC2 deployment we're heading toward.

## Decision

**Option B: bake `appuser` (UID 1000) into the image, switch via `USER appuser` after all build-time steps.**

In one line: **build phase runs as root (setup, trusted machine, ephemeral); runtime runs as non-root (long-lived, internet-facing, attacker-reachable). Each phase picks the privilege level that matches its threat model.**

Option C inverts the secure default and depends on operator discipline — brittle once the image runs by automation. Option A leaves the amplifier in place for no benefit. Option B costs one line of Dockerfile complexity and removes the largest single multiplier on any future container compromise.

## Consequences

- **`USER appuser` placed after all `COPY` and `RUN` steps**, before `CMD`. Build-time install/copy still runs as root.
- **Fixed UID 1000.** Conventional first non-system user on Debian; predictable across rebuilds and hosts (matters once volume mounts enter the picture).
- **Cannot bind to ports < 1024.** Currently irrelevant. If anyone proposes binding to 80 directly, the answer is "use a reverse proxy or port mapping," not "drop the non-root user."
- **`/opt/venv` works without `chown`.** Files copied as root are world-readable (644) and directories world-executable (755) by default — `appuser` can execute Python and import packages without permission tweaks. Revisit if we ever restrict venv permissions or write to disk.
- **Revisit if:** (a) the app legitimately needs a privileged port without a reverse proxy, or (b) we need to write to a host-mounted volume and the host UID/GID doesn't match 1000.

## Notes

The likely interview pushback: *"Namespaces already isolate the container — root inside isn't really host-root."* True in the normal case. The point isn't that namespaces are broken today; it's that they are the *only* thing standing between in-container root and host-root, and "only one layer between attacker and host" is exactly the kind of thin defence that fails the day a kernel CVE is published. Non-root containers add a second independent layer at near-zero cost. Defence in depth is cheap precisely *because* the underlying isolation usually works — until it doesn't.

The AWS root vs IAM analogy in `NOTES.md` is the same decision in a different domain: privileged identity for one-time setup, scoped identity for ongoing work. Worth flagging in interviews — it shows the principle is being applied consistently, not learned ad-hoc per technology.
