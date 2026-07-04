"""Unit tests for the user_cookies SECRET boundary: encrypt-at-rest +
DECRYPT-ON-READ.

No DB / network — these exercise the pure crypto boundary of
``CookiesRepository`` in isolation. The KEY DIFFERENCE vs api_keys: the cookie
consumers (downloaders: ytdlp_service / abogus_parser / drissionpage_parser /
soda cookie_source / media_fetch_helpers) need the REAL plaintext to drive a
browser / yt-dlp session, so the repo DECRYPTS ON READ and returns plaintext
dicts exactly as today. Only the at-rest representation changes:

  * ``upsert()`` — encrypts ``cookie_text`` AND ``cookie_file`` at the write
    boundary (Fernet ``gAAAAA`` prefix); ``None`` passes through unchanged;
    ``custom_headers`` is NOT a scoped secret column and stays verbatim.
  * reads (``get_all_by_user`` / ``get_by_user_and_platform``) and the upsert
    RETURNING row — DECRYPT the two secret columns back to plaintext, so
    consumers are zero-touch.
  * DUAL-READ: a legacy plaintext row (no ``gAAAAA`` prefix) reads back as-is
    (``secret_box.decrypt`` passthrough), keeping the migration window seamless.

The real DB round-trip (INSERT/SELECT, exposure parity, ON CONFLICT merge) lives
in tests/integration/test_cookies_repository_orm.py.
"""

from __future__ import annotations

import datetime as _dt
import uuid as _uuid
from contextlib import asynccontextmanager

import pytest
from sqlalchemy.dialects import postgresql

from app.core import secret_box
from app.models import UserCookies
from app.repositories import cookies_repository as _mod
from app.repositories.cookies_repository import CookiesRepository, _row

pytestmark = pytest.mark.unit

_USER_ID = _uuid.UUID("11111111-1111-1111-1111-111111111111")


def _make_orm_row(**overrides) -> UserCookies:
    """Build a transient UserCookies ORM row (no DB) with sensible defaults.

    ``cookie_text`` / ``cookie_file`` default to ``None``; callers pass the
    AT-REST representation (ciphertext or legacy plaintext) they want to test the
    read path against."""
    row = UserCookies()
    row.id = 123456789
    row.user_id = _USER_ID
    row.platform = "douyin"
    row.is_valid = True
    row.created_at = _dt.datetime(2026, 7, 4, tzinfo=_dt.timezone.utc)
    row.updated_at = _dt.datetime(2026, 7, 4, tzinfo=_dt.timezone.utc)
    row.cookie_text = None
    row.cookie_file = None
    row.error_message = None
    row.custom_headers = None
    for key, value in overrides.items():
        setattr(row, key, value)
    return row


# ─── DECRYPT-ON-READ: ciphertext at rest → plaintext dict ────────────────


def test_row_decrypts_both_secret_columns():
    """``_row`` DECRYPTS ``cookie_text`` and ``cookie_file`` so consumers get the
    real plaintext (round-trip through a row carrying ciphertext)."""
    secret_cookie = "sessionid=SECRET_ABC123; ttwid=XYZ-987"
    secret_file = '{"cookies": [{"name": "sid", "value": "deadbeef"}]}'
    orm_row = _make_orm_row(
        cookie_text=secret_box.encrypt(secret_cookie),
        cookie_file=secret_box.encrypt(secret_file),
    )
    # Sanity: the AT-REST values are genuinely ciphertext, not plaintext.
    assert orm_row.cookie_text.startswith("gAAAAA")
    assert orm_row.cookie_file.startswith("gAAAAA")

    out = _row(orm_row)

    # Reads return the ORIGINAL plaintext (consumers zero-touch).
    assert out["cookie_text"] == secret_cookie
    assert out["cookie_file"] == secret_file
    # Strategy-C value-type parity is preserved alongside decryption.
    assert type(out["user_id"]) is str and out["user_id"] == str(_USER_ID)
    assert type(out["id"]) is int
    assert type(out["updated_at"]) is str and "T" in out["updated_at"]


def test_row_none_passthrough_on_read():
    """A NULL secret column decrypts to ``None`` (no crash, no fabricated
    value)."""
    out = _row(_make_orm_row(cookie_text=None, cookie_file=None))
    assert out["cookie_text"] is None
    assert out["cookie_file"] is None


def test_row_legacy_plaintext_dual_read():
    """DUAL-READ: a legacy plaintext row (written before encrypt-at-rest, no
    ``gAAAAA`` prefix) reads back UNCHANGED — ``secret_box.decrypt`` passes
    non-Fernet values through so the migration window is seamless."""
    legacy_cookie = "sessionid=LEGACY_PLAINTEXT; not-encrypted"
    legacy_file = "netscape\tcookie\tfile\tlegacy"
    orm_row = _make_orm_row(cookie_text=legacy_cookie, cookie_file=legacy_file)
    assert not orm_row.cookie_text.startswith("gAAAAA")

    out = _row(orm_row)
    assert out["cookie_text"] == legacy_cookie
    assert out["cookie_file"] == legacy_file


def test_row_custom_headers_not_decrypted():
    """``custom_headers`` is NOT a scoped secret column — it is neither encrypted
    on write nor decrypted on read; it passes through verbatim."""
    headers = "Referer: https://www.douyin.com/"
    out = _row(_make_orm_row(custom_headers=headers))
    assert out["custom_headers"] == headers


# ─── ENCRYPT-AT-REST: upsert write boundary ──────────────────────────────


class _FakeResult:
    def __init__(self, row):
        self._row = row

    def scalars(self):
        return self

    def first(self):
        return self._row


class _CapturingSession:
    """Captures the compiled bind params of the upsert INSERT and echoes them
    back as a transient UserCookies row, so upsert() can build its return dict
    (which then DECRYPTS) without a DB."""

    def __init__(self):
        self.captured: dict = {}

    async def execute(self, stmt, *args, **kwargs):
        params = dict(stmt.compile(dialect=postgresql.dialect()).params)
        self.captured = params
        row = UserCookies()
        for col, value in params.items():
            if col in {p.key for p in UserCookies.__mapper__.column_attrs}:
                setattr(row, col, value)
        return _FakeResult(row)


def _patch_write_scope(monkeypatch, session):
    @asynccontextmanager
    async def fake_write_scope():
        yield session

    monkeypatch.setattr(_mod, "write_scope", fake_write_scope)


async def test_upsert_encrypts_both_secret_columns(monkeypatch):
    """upsert binds Fernet CIPHERTEXT (``gAAAAA``) for BOTH ``cookie_text`` and
    ``cookie_file``; the returned dict DECRYPTS back to the original plaintext."""
    session = _CapturingSession()
    _patch_write_scope(monkeypatch, session)

    secret_cookie = "sessionid=SECRET_ABC123; ttwid=XYZ-987"
    secret_file = '{"cookies": [{"name": "sid", "value": "deadbeef"}]}'

    out = await CookiesRepository().upsert(
        str(_USER_ID),
        "douyin",
        {"cookie_text": secret_cookie, "cookie_file": secret_file},
    )

    # AT REST: both columns are bound as Fernet ciphertext, NOT plaintext.
    stored_text = session.captured["cookie_text"]
    stored_file = session.captured["cookie_file"]
    assert stored_text.startswith("gAAAAA") and stored_text != secret_cookie
    assert stored_file.startswith("gAAAAA") and stored_file != secret_file
    # …and each ciphertext decrypts back to the original (round-trip).
    assert secret_box.decrypt(stored_text) == secret_cookie
    assert secret_box.decrypt(stored_file) == secret_file

    # The RETURNED dict is DECRYPTED plaintext (consumers zero-touch).
    assert out is not None
    assert out["cookie_text"] == secret_cookie
    assert out["cookie_file"] == secret_file
    # Freshness semantics preserved: upsert forces is_valid=True + no error.
    assert out["is_valid"] is True
    assert out["error_message"] is None


async def test_upsert_none_passthrough(monkeypatch):
    """A ``None`` secret column is NOT encrypted (encrypt(None)→None) and reads
    back as ``None`` — no ciphertext fabricated for an absent value."""
    session = _CapturingSession()
    _patch_write_scope(monkeypatch, session)

    out = await CookiesRepository().upsert(
        str(_USER_ID),
        "bilibili",
        {"cookie_text": "only-text", "cookie_file": None},
    )

    assert session.captured["cookie_text"].startswith("gAAAAA")
    assert session.captured["cookie_file"] is None
    assert out is not None
    assert out["cookie_text"] == "only-text"
    assert out["cookie_file"] is None


async def test_upsert_custom_headers_not_encrypted(monkeypatch):
    """``custom_headers`` is stored VERBATIM — it is out of the encrypt scope
    (only ``cookie_text`` / ``cookie_file`` are the at-rest secret columns)."""
    session = _CapturingSession()
    _patch_write_scope(monkeypatch, session)

    headers = "Referer: https://www.douyin.com/"
    await CookiesRepository().upsert(
        str(_USER_ID),
        "douyin",
        {"cookie_text": "x", "custom_headers": headers},
    )

    assert session.captured["custom_headers"] == headers
    assert not session.captured["custom_headers"].startswith("gAAAAA")
