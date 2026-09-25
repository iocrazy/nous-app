"""Share passwords are bcrypt hashes (mig 504); the legacy column is a lock.

The DB half (pgcrypto backfill + trigger, cross-verification with the
binding) is ``tests/db/test_migration_504_share_password_hash_integration.py``.
"""

from __future__ import annotations

import pytest

from app.api import share_access
from app.services.library.share_passwords import (
    LEGACY_PASSWORD_LOCK_PREFIX,
    has_password,
    hash_share_password,
    password_matches,
    password_matches_async,
    share_password_columns,
)


def test_hash_is_bcrypt_2a_and_verifies() -> None:
    hashed = hash_share_password("s3cret")
    assert hashed.startswith("$2a$10$")
    assert "s3cret" not in hashed
    assert password_matches(hashed, "s3cret")
    assert not password_matches(hashed, "s3cret ")
    assert not password_matches(hashed, "")


def test_same_password_hashes_differently() -> None:
    """A new salt every time: that is what lets a password change (even to
    the same value) revoke the visitor grants signed over the old hash."""
    assert hash_share_password("s3cret") != hash_share_password("s3cret")


def test_long_multibyte_password_is_cut_at_72_bytes_like_pgcrypto() -> None:
    password = "密" * 100  # 300 bytes; the binding alone would raise
    hashed = hash_share_password(password)
    assert password_matches(hashed, password)
    # The first 72 bytes are all bcrypt reads (24 characters x 3 bytes).
    assert password_matches(hashed, "密" * 24)


def test_columns_never_carry_the_password() -> None:
    cols = share_password_columns("s3cret")
    assert set(cols) == {"password", "password_hash"}
    assert cols["password"].startswith(LEGACY_PASSWORD_LOCK_PREFIX)
    assert "s3cret" not in cols["password"]
    assert password_matches(cols["password_hash"], "s3cret")
    # The lock is random per share, so nobody can type it.
    assert share_password_columns("s3cret")["password"] != cols["password"]


@pytest.mark.parametrize("value", [None, ""])
def test_no_password_clears_both_columns(value) -> None:
    assert share_password_columns(value) == {"password": None, "password_hash": None}


def test_has_password_reads_only_the_hash() -> None:
    assert has_password({"password_hash": "$2a$10$x"})
    # A row with only the legacy column (lock or stale plain text) is not
    # "protected by that value": nothing reads it any more.
    assert not has_password({"password": "plain", "password_hash": None})


@pytest.mark.parametrize("stored", [None, "", "not-a-bcrypt-hash", "plain-text"])
def test_missing_or_malformed_hash_denies(stored) -> None:
    assert password_matches(stored, "plain-text") is False


@pytest.mark.asyncio
async def test_async_check_matches_the_sync_one() -> None:
    hashed = hash_share_password("s3cret")
    assert await password_matches_async(hashed, "s3cret") is True
    assert await password_matches_async(hashed, "nope") is False


def test_grant_fingerprint_follows_the_hash(monkeypatch) -> None:
    monkeypatch.setattr(
        "app.api.media_auth._signing_secret", lambda: "test-secret", raising=False
    )
    monkeypatch.setattr(
        "app.api.media_auth._verify_secrets", lambda: ["test-secret"], raising=False
    )
    share = {"id": 42, **share_password_columns("s3cret")}
    grant = share_access.sign_share_grant(share, now=1_000)
    _, share_id, expires, sig = grant.split(".")
    ok = share_access._grant_signature_ok
    assert ok(share, int(share_id), int(expires), sig)
    rotated = {**share, **share_password_columns("s3cret")}
    assert not ok(rotated, int(share_id), int(expires), sig)
    # The legacy column plays no part: changing only it keeps the grant.
    relocked = {**share, "password": f"{LEGACY_PASSWORD_LOCK_PREFIX}other"}
    assert ok(relocked, int(share_id), int(expires), sig)
