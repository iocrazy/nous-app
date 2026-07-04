"""Unit tests for the shared secret rotate/backfill runner
(``scripts/rotate_secrets.py``).

These use the fake-session scope-mock style (copied from
``tests/test_points_repository.py``): a fake session captures every emitted
``(sql, params)`` pair so the compiled statement shape + bound values are
asserted WITHOUT a live database. ``write_scope`` is monkeypatched to yield the
same fake session, so both the scan SELECT and the batched UPDATE land in one
capture list.

Security note: NO real secret is ever exercised — only synthetic plaintext
strings ("plain-token-1", ...) round-tripped through the dev-fallback Fernet key
in ``app.core.secret_box``. Ciphertext is asserted by the public ``gAAAAA``
Fernet prefix, never by logging or comparing decrypted material outside the test.
"""

from __future__ import annotations

import uuid as _uuid
from typing import Any

import pytest

import scripts.rotate_secrets as mod
from app.core import secret_box
from app.models import ApiKeys, UserCookies, UserMcpServers
from scripts.rotate_secrets import RotateStats, rotate_column

_PREFIX = "gAAAAA"


class _FakeResult:
    def __init__(self, rows: list[Any]) -> None:
        self._rows = rows

    def all(self) -> list[Any]:
        return self._rows


class _FakeSession:
    """Captures ``execute(stmt, params)`` calls; returns configured scan rows.

    The scan SELECT reads ``result.all()`` (list of ``(pk, value)`` tuples); the
    batched UPDATE passes an executemany payload list as ``params``.
    """

    def __init__(self, rows: list[Any] | None = None) -> None:
        self.rows = rows or []
        self.calls: list[tuple[str, Any]] = []

    async def execute(self, stmt: Any, params: Any = None) -> _FakeResult:
        self.calls.append((str(stmt), params))
        return _FakeResult(self.rows)

    def updates(self) -> list[tuple[str, Any]]:
        return [c for c in self.calls if "UPDATE" in c[0]]

    def selects(self) -> list[tuple[str, Any]]:
        return [c for c in self.calls if "SELECT" in c[0]]

    def update_payload(self) -> list[dict[str, Any]]:
        """Flatten every executemany UPDATE payload into one list of dicts."""
        rows: list[dict[str, Any]] = []
        for _sql, params in self.updates():
            if isinstance(params, list):
                rows.extend(params)
            elif params is not None:
                rows.append(params)
        return rows


class _ScopeCM:
    def __init__(self, session: _FakeSession) -> None:
        self._session = session

    async def __aenter__(self) -> _FakeSession:
        return self._session

    async def __aexit__(self, *exc: Any) -> bool:
        return False


@pytest.fixture
def fake_session(monkeypatch: pytest.MonkeyPatch) -> _FakeSession:
    session = _FakeSession()
    # Writes go through the module-level write_scope(); yield the SAME fake
    # session so the scan SELECT and the batched UPDATE share one capture list.
    monkeypatch.setattr(mod, "write_scope", lambda: _ScopeCM(session))
    return session


# ─── plaintext → encrypted ─────────────────────────────────────────


@pytest.mark.asyncio
async def test_plaintext_row_is_encrypted(fake_session: _FakeSession) -> None:
    pk = _uuid.uuid4()
    fake_session.rows = [(pk, "plain-token-1")]

    stats = await rotate_column(fake_session, UserMcpServers, "bearer_token")

    assert stats.scanned == 1
    assert stats.encrypted == 1
    assert stats.reencrypted == 0
    assert stats.skipped_null == 0

    payload = fake_session.update_payload()
    assert len(payload) == 1
    new_val = payload[0]["bearer_token"]
    assert new_val.startswith(_PREFIX)  # now a Fernet token
    # round-trips back to the original synthetic plaintext
    assert secret_box.decrypt(new_val) == "plain-token-1"
    # the PK travelled with the update
    assert payload[0]["id"] == pk


# ─── already-encrypted → re-encrypted (rotate) ─────────────────────


@pytest.mark.asyncio
async def test_encrypted_row_is_reencrypted_new_iv_same_plaintext(
    fake_session: _FakeSession,
) -> None:
    original = secret_box.encrypt("plain-token-2")
    assert original.startswith(_PREFIX)
    pk = 123456789012345678
    fake_session.rows = [(pk, original)]

    stats = await rotate_column(fake_session, ApiKeys, "key_value")

    assert stats.scanned == 1
    assert stats.encrypted == 0
    assert stats.reencrypted == 1
    assert stats.skipped_null == 0

    payload = fake_session.update_payload()
    assert len(payload) == 1
    rotated = payload[0]["key_value"]
    assert rotated.startswith(_PREFIX)
    # new IV → ciphertext differs, but decrypts to the SAME plaintext
    assert rotated != original
    assert secret_box.decrypt(rotated) == "plain-token-2"


# ─── None → skipped, never written ─────────────────────────────────


@pytest.mark.asyncio
async def test_none_value_is_skipped(fake_session: _FakeSession) -> None:
    pk = _uuid.uuid4()
    fake_session.rows = [(pk, None)]

    stats = await rotate_column(fake_session, UserMcpServers, "bearer_token")

    assert stats.scanned == 1
    assert stats.skipped_null == 1
    assert stats.encrypted == 0
    assert stats.reencrypted == 0
    # a NULL row must never appear in any UPDATE payload
    assert fake_session.update_payload() == []


# ─── dry-run → full scan + stats, zero UPDATEs ─────────────────────


@pytest.mark.asyncio
async def test_dry_run_performs_zero_updates(fake_session: _FakeSession) -> None:
    fake_session.rows = [
        (_uuid.uuid4(), "plain-a"),
        (_uuid.uuid4(), secret_box.encrypt("plain-b")),
        (_uuid.uuid4(), None),
    ]

    stats = await rotate_column(
        fake_session, UserMcpServers, "bearer_token", dry_run=True
    )

    # full scan + accurate stats
    assert stats.scanned == 3
    assert stats.encrypted == 1
    assert stats.reencrypted == 1
    assert stats.skipped_null == 1
    # but ZERO writes
    assert fake_session.updates() == []


# ─── decrypt failure → counted, logged by PK, run continues ────────


@pytest.mark.asyncio
async def test_decrypt_failure_is_counted_and_run_continues(
    fake_session: _FakeSession,
) -> None:
    # A gAAAAA-prefixed value that is NOT a valid token under any key → decrypt
    # raises ValueError. The runner must branch on the prefix, catch, count it as
    # failed (implicitly: scanned but not in any success bucket), and continue.
    good_pk = _uuid.uuid4()
    bad_pk = _uuid.uuid4()
    fake_session.rows = [
        (bad_pk, "gAAAAA-not-a-real-token"),
        (good_pk, "plain-token-3"),
    ]

    stats = await rotate_column(fake_session, UserMcpServers, "bearer_token")

    assert stats.scanned == 2
    assert stats.encrypted == 1  # the good plaintext row still processed
    assert stats.reencrypted == 0
    assert stats.skipped_null == 0
    assert stats.failed == 1  # derived: scanned - encrypted - reencrypted - null

    # only the good row is written; the bad PK never lands in a payload
    payload = fake_session.update_payload()
    assert len(payload) == 1
    assert payload[0]["id"] == good_pk


# ─── batching: >200 rows flush in 200-row chunks ───────────────────


@pytest.mark.asyncio
async def test_updates_flush_in_batches_of_200(fake_session: _FakeSession) -> None:
    fake_session.rows = [(_uuid.uuid4(), f"plain-{i}") for i in range(450)]

    stats = await rotate_column(fake_session, UserMcpServers, "bearer_token")

    assert stats.scanned == 450
    assert stats.encrypted == 450
    # 450 rows → chunks of 200, 200, 50 → three executemany UPDATEs
    updates = fake_session.updates()
    assert len(updates) == 3
    sizes = [len(params) for _sql, params in updates]
    assert sizes == [200, 200, 50]


# ─── cookies target has two secret columns ─────────────────────────


@pytest.mark.asyncio
async def test_cookies_second_column_encrypts_independently(
    fake_session: _FakeSession,
) -> None:
    pk = 987654321098765432
    fake_session.rows = [(pk, "cookie-blob-1")]

    stats = await rotate_column(fake_session, UserCookies, "cookie_file")

    assert stats.encrypted == 1
    payload = fake_session.update_payload()
    assert payload[0]["cookie_file"].startswith(_PREFIX)
    assert payload[0]["id"] == pk


# ─── CLI registry wiring ───────────────────────────────────────────


def test_targets_registry_matches_reflected_models() -> None:
    assert mod.TARGETS["user_mcp_servers"] == (UserMcpServers, ["bearer_token"])
    assert mod.TARGETS["api_keys"] == (ApiKeys, ["key_value"])
    assert mod.TARGETS["cookies"] == (UserCookies, ["cookie_text", "cookie_file"])


def test_rotate_stats_failed_is_derived() -> None:
    s = RotateStats(scanned=5, encrypted=2, reencrypted=1, skipped_null=1)
    assert s.failed == 1
