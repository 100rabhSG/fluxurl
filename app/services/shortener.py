"""Short-code generation.

Per ADR 0004:
- random generation, not hash- or counter-based
- uses `secrets` (CSPRNG) not `random` (predictable)
- collision handling is the caller's responsibility (optimistic
  retry on DB unique-violation)
"""

import secrets
import string

# 62-character URL-safe alphabet: 0-9, A-Z, a-z (no padding chars).
ALPHABET = string.digits + string.ascii_uppercase + string.ascii_lowercase

DEFAULT_LENGTH = 7


def generate_short_code(length: int = DEFAULT_LENGTH) -> str:
    return "".join(secrets.choice(ALPHABET) for _ in range(length))
