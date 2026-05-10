"""P7 — application-layer symmetric encryption for secrets stored in
Postgres (currently: user_mcp_servers.bearer_token).

Uses Fernet (cryptography lib) — AES-128-CBC + HMAC-SHA256 with a
random 128-bit IV per ciphertext. Output is URL-safe base64 string,
stored as TEXT in Postgres so existing queries don't need bytea
handling.

Key management
==============
The Fernet key comes from env ``MEDIAHUB_TOKEN_ENCRYPTION_KEY`` (44-char
URL-safe base64). Generate one with:

    python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"

To rotate: deploy a new key as ``MEDIAHUB_TOKEN_ENCRYPTION_KEY`` AND
keep the old one as ``MEDIAHUB_TOKEN_ENCRYPTION_KEY_OLD``. The decryptor
tries new key first, falls back to old. After running
``rotate_all_tokens()`` (one-shot script TBD), drop the OLD env var.

When the env var is unset
=========================
``encrypt`` raises RuntimeError → router returns 500 on token-bearing
operations. ``decrypt`` of an existing ciphertext also raises. This is
intentional: silent fallback to plain text would defeat the purpose.
For dev environments without a configured key, the operator should
either (a) set MEDIAHUB_TOKEN_ENCRYPTION_KEY in .env, or (b) use the
DEV_TOKEN_ENCRYPTION_KEY constant below for local-only convenience
(NEVER ship to prod).
"""

from __future__ import annotations

import os
from typing import Optional

from cryptography.fernet import Fernet, InvalidToken, MultiFernet

# Local-dev convenience key — intentionally NOT a real secret. Generated
# once via Fernet.generate_key(); committed so dev environments have a
# deterministic decryption key. Production MUST override via env.
# Detected and warned at startup (see is_configured()).
DEV_TOKEN_ENCRYPTION_KEY = "elPmFWmZ0El3Dtmj-vzj9k6tmKy8vZxaIKhikCWNavY="


class SecretBoxNotConfigured(RuntimeError):
    """Raised when encrypt/decrypt is called but no key is configured
    (and dev fallback is disabled)."""


def _resolve_keys(*, allow_dev_fallback: bool = True) -> list[bytes]:
    """Return ordered list of Fernet keys to try (new first, old second)."""
    keys: list[bytes] = []
    primary = os.environ.get("MEDIAHUB_TOKEN_ENCRYPTION_KEY")
    legacy = os.environ.get("MEDIAHUB_TOKEN_ENCRYPTION_KEY_OLD")
    if primary:
        keys.append(primary.encode())
    if legacy:
        keys.append(legacy.encode())
    if not keys and allow_dev_fallback:
        # Only used when no env at all — log loudly elsewhere
        keys.append(DEV_TOKEN_ENCRYPTION_KEY.encode())
    return keys


def _fernet(*, allow_dev_fallback: bool = True) -> Optional[MultiFernet]:
    keys = _resolve_keys(allow_dev_fallback=allow_dev_fallback)
    if not keys:
        return None
    return MultiFernet([Fernet(k) for k in keys])


def encrypt(plaintext: Optional[str]) -> Optional[str]:
    """Encrypt a string. ``None`` passes through unchanged so callers
    can preserve "no token" semantics.

    Raises SecretBoxNotConfigured if no key is configured.
    """
    if plaintext is None:
        return None
    f = _fernet()
    if f is None:
        raise SecretBoxNotConfigured(
            "MEDIAHUB_TOKEN_ENCRYPTION_KEY not set; cannot encrypt secret"
        )
    return f.encrypt(plaintext.encode()).decode()


def decrypt(ciphertext: Optional[str]) -> Optional[str]:
    """Decrypt a string previously produced by ``encrypt``.

    Returns None when input is None. Raises SecretBoxNotConfigured if
    no key is configured. Raises ValueError on tamper / wrong-key.

    Backward-compat: if ``ciphertext`` doesn't look like a Fernet token
    (starts with 'gAAAAA' base64 prefix), returns it as-is — this lets
    callers transparently read legacy plaintext rows during the
    migration window. Once all rows are encrypted, this branch never
    fires.
    """
    if ciphertext is None:
        return None
    if not ciphertext.startswith("gAAAAA"):
        # Looks like legacy plain-text — return as-is for back-compat
        return ciphertext
    f = _fernet()
    if f is None:
        raise SecretBoxNotConfigured(
            "MEDIAHUB_TOKEN_ENCRYPTION_KEY not set; cannot decrypt secret"
        )
    try:
        return f.decrypt(ciphertext.encode()).decode()
    except InvalidToken as exc:
        raise ValueError(
            "decrypt failed — wrong key, tampered ciphertext, or " "expired token"
        ) from exc


def is_configured() -> bool:
    """Diagnostic — used by startup probe to log a warning when running
    on the dev fallback key."""
    return bool(os.environ.get("MEDIAHUB_TOKEN_ENCRYPTION_KEY"))


__all__ = [
    "SecretBoxNotConfigured",
    "encrypt",
    "decrypt",
    "is_configured",
    "DEV_TOKEN_ENCRYPTION_KEY",
]
