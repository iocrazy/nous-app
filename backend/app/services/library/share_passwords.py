"""Share passwords: bcrypt hashes in ``shares.password_hash`` (mig 504).

Until mig 504 the ``shares.password`` column held the password in plain text.
Now the hash is the only thing any reader looks at, and the legacy column gets
a random lock value (:func:`share_password_columns`): an old image that still
compares against that column can never match it, so a rollback fails closed.
Mig 504's trigger hashes whatever a legacy writer still puts there in plain
text.

The visitor grant (``app/api/share_access.py``) signs a fingerprint of the
hash, so changing the password revokes every grant issued before.
"""

from __future__ import annotations

import asyncio
import uuid
from typing import Any, Mapping

import bcrypt
from loguru import logger

# Same cost mig 504 uses for the backfill (``gen_salt('bf', 10)``).
BCRYPT_ROUNDS = 10
# bcrypt reads at most 72 bytes; pgcrypto truncates silently, the Python
# binding raises. Truncate here so both sides hash the same bytes.
_BCRYPT_MAX_BYTES = 72
# What the legacy ``password`` column holds for a protected share. Random per
# row, so no typed password can ever equal it (see the module docstring).
LEGACY_PASSWORD_LOCK_PREFIX = "!locked:"


def _bcrypt_input(password: str) -> bytes:
    return password.encode()[:_BCRYPT_MAX_BYTES]


def hash_share_password(password: str) -> str:
    """A bcrypt hash of ``password`` (``$2a$10$...``).

    ``$2a$`` rather than the binding's default ``$2b$``: pgcrypto (mig 504's
    backfill and trigger) only reads and writes ``$2a$``, and for input capped
    at 72 bytes the two variants are the same algorithm. One format means
    either side can verify any stored hash."""
    salt = bcrypt.gensalt(rounds=BCRYPT_ROUNDS, prefix=b"2a")
    return bcrypt.hashpw(_bcrypt_input(password), salt).decode()


def share_password_columns(password: str | None) -> dict[str, str | None]:
    """The two ``shares`` columns to write for ``password`` (None = no
    password). The hash goes to ``password_hash``; the legacy column gets a
    random lock, never the password."""
    if not password:
        return {"password_hash": None, "password": None}
    return {
        "password_hash": hash_share_password(password),
        "password": f"{LEGACY_PASSWORD_LOCK_PREFIX}{uuid.uuid4()}",
    }


def has_password(share: Mapping[str, Any]) -> bool:
    return bool(share.get("password_hash"))


def password_matches(stored_hash: str | None, given: str | None) -> bool:
    """bcrypt check (constant-time inside ``checkpw``). A malformed stored
    hash denies instead of raising."""
    if not stored_hash or given is None:
        return False
    try:
        return bcrypt.checkpw(_bcrypt_input(given), stored_hash.encode())
    except ValueError:
        logger.error("Share password hash is malformed; denying access")
        return False


async def password_matches_async(stored_hash: str | None, given: str | None) -> bool:
    """:func:`password_matches` off the event loop (bcrypt is deliberately
    slow)."""
    return await asyncio.to_thread(password_matches, stored_hash, given)
