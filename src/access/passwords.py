"""Password hashing and verification.

access-v1: "Argon2id, or bcrypt at cost 12 or higher. Never reversible, never
logged." This module implements the bcrypt side of that choice. The cost
factor is a module constant, not a policy row, because it is a cryptographic
parameter baked into each stored hash at the time it was created -- unlike
`access_policy` values, changing it does not change the behaviour of existing
rows, only of hashes created from this point on.

Known gap, recorded rather than silently dropped (US-001 audit): there is no
password-strength or breach-list rejection anywhere in this module or in
``access.service``. That is not an oversight this docstring is fixing -- it is
a gap with nowhere to live yet, because no password-*set* flow (sign-up,
invite, reset, change) exists in this slice for it to guard. US-001 only
verifies a password already on the row; whichever story introduces setting
one must decide and enforce a strength/breach policy before this gap can be
called closed.
"""

from __future__ import annotations

import bcrypt

__all__ = ["hash_password", "verify_password"]

# access-v1 floor. Raising this is safe for new hashes at any time; it is not
# a tunable read from configuration because, unlike a timeout or an edit
# limit, it has no live effect on a password hashed under the old cost.
_BCRYPT_ROUNDS = 12


def hash_password(password: str) -> str:
    """Hash a password for storage. Never reversible, never logged.

    A fresh salt is drawn every call, so hashing the same password twice
    never produces the same digest -- a bare digest would be reversible by
    lookup against a precomputed table.
    """
    salt = bcrypt.gensalt(rounds=_BCRYPT_ROUNDS)
    return bcrypt.hashpw(password.encode("utf-8"), salt).decode("ascii")


def verify_password(password: str, password_hash: str) -> bool:
    """Check a password against its stored hash. Always a real bool.

    A malformed or foreign-encoding hash raises inside bcrypt rather than
    returning False; caught here so a corrupt row produces a clean
    authentication failure instead of a 500 that might echo more than it
    should into an error page.
    """
    try:
        return bcrypt.checkpw(password.encode("utf-8"), password_hash.encode("utf-8"))
    except (ValueError, TypeError):
        return False

