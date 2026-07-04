"""Unit tests for the api_keys SECRET boundary: encrypt-at-rest + masked reads.

No DB / network — these exercise the pure crypto boundary of
``ApiKeyRepository`` in isolation:

  * ``_mask_key`` — the read-side masking helper the router applies to
    list / get / update rows: it replaces the ``key_value`` column (ciphertext
    at rest) with the public ``key_prefix`` display form + an ellipsis and adds
    a boolean ``key_value_set``. Never surfaces plaintext or ciphertext.
  * ``create()`` — encrypts ``key_value`` at the write boundary: the value bound
    into the INSERT is a Fernet token (``gAAAAA`` prefix) that decrypts back to
    the full key, while the one-time ``secret_key`` reveal stays plaintext.
  * ``validate_key()`` — proven UNCHANGED: hash-based lookup, no decrypt of
    ``key_value``. An encrypted ``key_value`` on the row does not affect it.

The DB round-trip (real INSERT/SELECT, exposure parity, RPC) lives in
tests/integration/test_api_key_repository_orm.py.
"""

from __future__ import annotations

from contextlib import asynccontextmanager
from unittest.mock import MagicMock

import pytest
from sqlalchemy.dialects import postgresql

from app.core import secret_box
from app.models import ApiKeys
from app.repositories.api_key_repository import ApiKeyRepository, _mask_key

pytestmark = pytest.mark.unit


# ─── _mask_key — read-side masking (no plaintext, no ciphertext) ─────────


def test_mask_key_replaces_value_with_prefix_and_sets_flag():
    row = {
        "key_id": "abc",
        "key_prefix": "dk_12345678...",
        "key_value": "gAAAAAplaintext-or-ciphertext-does-not-matter",
        "name": "k",
    }
    masked = _mask_key(row)

    # key_value is replaced by the public prefix display form + ellipsis —
    # never the stored value (ciphertext) nor any plaintext.
    assert masked["key_value"] == "dk_12345678...…"
    assert masked["key_value_set"] is True
    # Other columns pass through untouched.
    assert masked["key_id"] == "abc"
    assert masked["name"] == "k"


def test_mask_key_is_immutable():
    """Global coding rule: masking returns a NEW dict, never mutates input."""
    row = {"key_prefix": "dk_deadbeef...", "key_value": "gAAAAAsecret"}
    masked = _mask_key(row)
    assert masked is not row
    # The original row is untouched — still carries its stored value.
    assert row["key_value"] == "gAAAAAsecret"
    assert "key_value_set" not in row


def test_mask_key_absent_value_sets_flag_false():
    """A row with no stored key (legacy NULL) reports key_value_set=False and
    leaves key_value as None — no fabricated masked string."""
    masked = _mask_key({"key_prefix": "dk_00000000...", "key_value": None})
    assert masked["key_value"] is None
    assert masked["key_value_set"] is False


# ─── create() — encrypt at the write boundary ───────────────────────────


class _FakeResult:
    def __init__(self, row):
        self._row = row

    def scalars(self):
        return self

    def first(self):
        return self._row


class _CapturingSession:
    """Captures the compiled bind params of the INSERT and echoes them back as
    a transient ApiKeys row, so create() can build its return dict without a DB.
    """

    def __init__(self):
        self.captured: dict = {}

    async def execute(self, stmt, *args, **kwargs):
        params = dict(stmt.compile(dialect=postgresql.dialect()).params)
        self.captured = params
        row = ApiKeys()
        for col, value in params.items():
            setattr(row, col, value)
        return _FakeResult(row)


async def test_create_encrypts_key_value_at_rest(monkeypatch):
    session = _CapturingSession()

    @asynccontextmanager
    async def fake_write_scope():
        yield session

    monkeypatch.setattr(
        "app.repositories.api_key_repository.write_scope", fake_write_scope
    )

    record = await ApiKeyRepository().create(
        user_id="11111111-1111-1111-1111-111111111111",
        name="unit-key",
        scopes=["read:media"],
    )

    # One-time reveal stays PLAINTEXT.
    full_key = record["secret_key"]
    assert full_key.startswith("dk_") and len(full_key) == 67

    # The value bound into the INSERT is a Fernet ciphertext, NOT the plaintext.
    stored = session.captured["key_value"]
    assert stored.startswith("gAAAAA")
    assert stored != full_key
    # …and it decrypts back to the full key (round-trip).
    assert secret_box.decrypt(stored) == full_key

    # The returned row carries the ciphertext (not plaintext) in key_value.
    assert record["key_value"].startswith("gAAAAA")
    assert record["key_value"] != full_key
    # key_hash is SHA-256(full_key) — hash-on-write untouched.
    assert record["key_hash"] == ApiKeyRepository.hash_key(full_key)


# ─── validate_key — hash-based, UNCHANGED by encryption ──────────────────


async def test_validate_key_is_hash_based_and_ignores_encrypted_value(monkeypatch):
    """validate_key must stay hash-based: it looks the row up by SHA-256(full_key)
    and never decrypts key_value. An encrypted key_value on the row is inert."""
    repo = ApiKeyRepository()
    full_key = "dk_" + "a" * 64
    expected_hash = ApiKeyRepository.hash_key(full_key)

    fake_row = {
        "key_id": "kid",
        "key_hash": expected_hash,
        "key_value": secret_box.encrypt(full_key),  # ciphertext at rest
        "status": "active",
        "expires_at": None,
        "user_id": "22222222-2222-2222-2222-222222222222",
        "scopes": ["read:media"],
    }

    seen = {}

    async def fake_get_by_key_hash(key_hash):
        seen["key_hash"] = key_hash
        return fake_row if key_hash == expected_hash else None

    monkeypatch.setattr(repo, "get_by_key_hash", fake_get_by_key_hash)

    result = await repo.validate_key(full_key)

    # Looked up by hash (not by decrypting the value) and returned the row.
    assert seen["key_hash"] == expected_hash
    assert result is fake_row
    # A non-dk key short-circuits before any lookup.
    assert await repo.validate_key("not-a-key") is None
