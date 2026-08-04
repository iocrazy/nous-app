"""Session-channel (mig 398) data layer of SocialAccountsRepository.

The bug class these guard: ``session_state`` is Fernet ciphertext with exactly
the same shape as ``access_token``, so every confusion is SILENT — ciphertext
handed to the browser looks like a string until Playwright rejects it, and
plaintext written straight to the column looks fine until someone reads the
table. So the boundary is asserted from both directions:

  * the write paths must encrypt before the statement is built;
  * ``get_with_session`` must hand back PLAINTEXT and no tokens;
  * ``get_with_tokens`` must not hand back the session ciphertext at all;
  * ``_public_row`` (every list/upsert response, i.e. what the frontend sees)
    must contain neither.
"""

from contextlib import asynccontextmanager

import pytest
from sqlalchemy.dialects import postgresql

from app.core import secret_box
from app.repositories.social_accounts_repository import (
    SocialAccountsRepository,
    _decrypt_secret_cols,
    _encrypt_secret_cols,
    _public_row,
)

_STATE = '{"cookies":[{"name":"sessionid","value":"abc"}],"origins":[]}'


# ── fakes ────────────────────────────────────────────────────────────────
class _Result:
    def __init__(self, rows):
        self._rows = rows

    def mappings(self):
        return self

    def first(self):
        return self._rows[0] if self._rows else None

    def all(self):
        return self._rows


class _Session:
    """Captures the statements it is handed so tests can compile and inspect
    them (there is no DB here — the SQL itself is the assertion surface)."""

    def __init__(self, rows):
        self._rows = rows
        self.statements = []

    async def execute(self, stmt, params=None):
        self.statements.append(stmt)
        return _Result(self._rows)


def _scope_returning(rows):
    session = _Session(rows)

    @asynccontextmanager
    async def _scope():
        yield session

    return _scope, session


def _sql(stmt) -> str:
    return str(stmt.compile(dialect=postgresql.dialect()))


def _params(stmt) -> dict:
    return stmt.compile(dialect=postgresql.dialect()).params


# ── column-set boundary ──────────────────────────────────────────────────
def test_session_state_encrypt_roundtrip():
    enc = _encrypt_secret_cols({"session_state": _STATE, "username": "HEYGO"})
    assert enc["session_state"].startswith("gAAAA")
    assert enc["username"] == "HEYGO"  # 非密文列不动
    assert _decrypt_secret_cols(dict(enc))["session_state"] == _STATE


def test_public_row_strips_session_state():
    pub = _public_row(
        {
            "id": 727145299382534145,
            "username": "x",
            "access_token": "gAAAA..",
            "session_state": "gAAAA..",
            "auth_type": "session",
            "status": "needs_relogin",
        }
    )
    assert "session_state" not in pub and "access_token" not in pub
    # The two new public columns DO ride along — the frontend needs both to
    # tell the binding kinds apart and to count "needs attention" correctly.
    assert pub["auth_type"] == "session" and pub["status"] == "needs_relogin"


# ── read paths ───────────────────────────────────────────────────────────
@pytest.mark.asyncio
async def test_get_with_session_decrypts_state_and_drops_tokens(monkeypatch):
    import app.repositories.social_accounts_repository as mod

    scope, _ = _scope_returning(
        [
            {
                "id": 1,
                "auth_type": "session",
                "session_state": secret_box.encrypt(_STATE),
                "access_token": secret_box.encrypt("act-plain"),
                "refresh_token": None,
            }
        ]
    )
    monkeypatch.setattr(mod, "read_scope", scope)
    row = await SocialAccountsRepository().get_with_session(1)
    assert row["session_state"] == _STATE
    assert "access_token" not in row and "refresh_token" not in row


@pytest.mark.asyncio
async def test_get_with_tokens_never_leaks_session_ciphertext(monkeypatch):
    """Ciphertext returned on the OAuth path would eventually be passed along
    as if it were a storage_state — drop the column instead."""
    import app.repositories.social_accounts_repository as mod

    scope, _ = _scope_returning(
        [
            {
                "id": 1,
                "session_state": secret_box.encrypt(_STATE),
                "access_token": secret_box.encrypt("act-plain"),
                "refresh_token": None,
            }
        ]
    )
    monkeypatch.setattr(mod, "read_scope", scope)
    row = await SocialAccountsRepository().get_with_tokens(1)
    assert "session_state" not in row
    assert row["access_token"] == "act-plain"


@pytest.mark.asyncio
async def test_get_with_session_attaches_environment_with_proxy_still_encrypted(
    monkeypatch,
):
    """The environment must ride along on the same read (S4's proxy is useless
    if the adapter can never see it), and proxy_url must arrive as CIPHERTEXT —
    build_environment owns that decrypt plus its fall-back-to-direct logging."""
    import app.repositories.social_accounts_repository as mod

    scope, session = _scope_returning(
        [
            {
                "id": 1,
                "session_state": secret_box.encrypt(_STATE),
                "env__account_id": 1,
                "env__proxy_url": secret_box.encrypt("http://u:p@proxy:8080"),
                "env__user_agent": "UA/1",
                "env__locale": "zh-CN",
                "env__timezone_id": "Asia/Shanghai",
                "env__geo_lat": 39.9,
                "env__geo_lng": 116.4,
                "env__fingerprint_profile_id": None,
            }
        ]
    )
    monkeypatch.setattr(mod, "read_scope", scope)
    row = await SocialAccountsRepository().get_with_session(1)

    env = row["environment"]
    assert env["proxy_url"].startswith("gAAAA")  # NOT decrypted here
    assert secret_box.decrypt(env["proxy_url"]) == "http://u:p@proxy:8080"
    assert env["timezone_id"] == "Asia/Shanghai" and env["geo_lat"] == 39.9
    assert env["account_id"] == "1"
    # The prefixed columns must not leak into the account row itself.
    assert not any(k.startswith("env__") for k in row)
    # LEFT JOIN, not INNER — assert it in the SQL so nobody "simplifies" it.
    assert "LEFT OUTER JOIN public.account_environments" in _sql(session.statements[0])


@pytest.mark.asyncio
async def test_get_with_session_environment_is_none_not_empty_dict(monkeypatch):
    """``build_environment`` branches on ``if not row`` — but every caller that
    checks ``environment is None`` would be wrong against {}. An account with no
    environment row (the norm before S4) must read as None."""
    import app.repositories.social_accounts_repository as mod

    scope, _ = _scope_returning(
        [
            {
                "id": 1,
                "session_state": secret_box.encrypt(_STATE),
                "env__account_id": None,  # LEFT JOIN found nothing
                "env__proxy_url": None,
                "env__user_agent": None,
                "env__locale": None,
                "env__timezone_id": None,
                "env__geo_lat": None,
                "env__geo_lng": None,
                "env__fingerprint_profile_id": None,
            }
        ]
    )
    monkeypatch.setattr(mod, "read_scope", scope)
    row = await SocialAccountsRepository().get_with_session(1)
    assert row["environment"] is None
    assert row["session_state"] == _STATE


@pytest.mark.asyncio
async def test_get_with_session_returns_none_when_no_row(monkeypatch):
    import app.repositories.social_accounts_repository as mod

    scope, _ = _scope_returning([])
    monkeypatch.setattr(mod, "read_scope", scope)
    assert await SocialAccountsRepository().get_with_session(1) is None


# ── health-sweep candidate scan ──────────────────────────────────────────
@pytest.mark.asyncio
async def test_list_session_accounts_for_check_orders_and_filters(monkeypatch):
    import app.repositories.social_accounts_repository as mod

    scope, session = _scope_returning(
        [
            {
                "id": 727145299382534145,
                "auth_type": "session",
                "session_state": "gAA",
                "env__account_id": 727145299382534145,
                "env__proxy_url": "gAAAA-ciphertext",
                "env__timezone_id": "Asia/Shanghai",
            },
            {"id": 2, "auth_type": "session", "session_state": None},
        ]
    )
    monkeypatch.setattr(mod, "read_scope", scope)
    rows = await SocialAccountsRepository().list_session_accounts_for_check(limit=5)

    sql = _sql(session.statements[0])
    assert "auth_type" in sql and "status" in sql
    # The sweep must validate through the SAME environment the publish path
    # uses, or "session healthy" and "publish works" drift apart. LEFT, so
    # accounts without an environment row are still swept.
    assert "LEFT OUTER JOIN public.account_environments" in sql
    # Never-checked accounts must sort FIRST, else a freshly bound account is
    # the last thing the sweep ever gets to.
    assert "ORDER BY public.social_accounts.session_checked_at ASC NULLS FIRST" in sql
    assert "LIMIT" in sql
    assert _params(session.statements[0])["param_1"] == 5
    # No session_state in bulk: the sweep pulls plaintext per account instead.
    assert all("session_state" not in r for r in rows)
    assert rows[0]["id"] == "727145299382534145"
    assert rows[0]["environment"]["proxy_url"] == "gAAAA-ciphertext"
    assert rows[1]["environment"] is None  # unconfigured account still swept


@pytest.mark.asyncio
async def test_list_session_accounts_for_check_platform_filter(monkeypatch):
    import app.repositories.social_accounts_repository as mod

    scope, session = _scope_returning([])
    monkeypatch.setattr(mod, "read_scope", scope)
    await SocialAccountsRepository().list_session_accounts_for_check(
        platform="douyin"
    )
    assert "platform" in _sql(session.statements[0])
    assert "douyin" in _params(session.statements[0]).values()


# ── write paths ──────────────────────────────────────────────────────────
@pytest.mark.asyncio
async def test_upsert_session_account_encrypts_and_marks_auth_type(monkeypatch):
    import app.repositories.social_accounts_repository as mod

    returned = {
        "id": 727145299382534145,
        "username": "HEYGO",
        "auth_type": "session",
        "session_state": secret_box.encrypt(_STATE),
        "access_token": None,
    }
    scope, session = _scope_returning([returned])
    monkeypatch.setattr(mod, "write_scope", scope)

    pub = await SocialAccountsRepository().upsert_session_account(
        scope_type="user",
        scope_id="u1",
        platform="douyin",
        platform_user_id="p1",
        username="HEYGO",
        session_state=_STATE,
        created_by="11111111-1111-1111-1111-111111111111",
    )

    params = _params(session.statements[0])
    stored = params["session_state"]
    assert stored != _STATE and stored.startswith("gAAAA")
    assert secret_box.decrypt(stored) == _STATE
    assert params["auth_type"] == "session"
    # The response is what the router hands the frontend — no ciphertext in it.
    assert "session_state" not in pub and pub["id"] == "727145299382534145"


@pytest.mark.asyncio
async def test_update_session_state_encrypts_write_back(monkeypatch):
    import app.repositories.social_accounts_repository as mod

    scope, session = _scope_returning([])
    monkeypatch.setattr(mod, "write_scope", scope)
    await SocialAccountsRepository().update_session_state(1, _STATE)

    params = _params(session.statements[0])
    assert secret_box.decrypt(params["session_state"]) == _STATE
    sql = _sql(session.statements[0])
    assert "session_checked_at=now()" in sql.replace(" ", "")


@pytest.mark.asyncio
async def test_update_session_state_without_state_only_stamps_checked_at(monkeypatch):
    """A health check that found the session alive but produced no new state
    must NOT blank the live session out of the row."""
    import app.repositories.social_accounts_repository as mod

    scope, session = _scope_returning([])
    monkeypatch.setattr(mod, "write_scope", scope)
    await SocialAccountsRepository().update_session_state(1)

    sql = _sql(session.statements[0])
    assert "session_state" not in sql
    assert "session_checked_at" in sql


@pytest.mark.asyncio
async def test_update_session_state_can_set_status(monkeypatch):
    import app.repositories.social_accounts_repository as mod

    scope, session = _scope_returning([])
    monkeypatch.setattr(mod, "write_scope", scope)
    await SocialAccountsRepository().update_session_state(
        1, _STATE, status="needs_relogin"
    )
    assert _params(session.statements[0])["status"] == "needs_relogin"


@pytest.mark.asyncio
async def test_mark_needs_relogin_is_distinct_from_expired(monkeypatch):
    import app.repositories.social_accounts_repository as mod

    scope, session = _scope_returning([])
    monkeypatch.setattr(mod, "write_scope", scope)
    await SocialAccountsRepository().mark_needs_relogin(1)
    assert _params(session.statements[0])["status"] == "needs_relogin"


# ── response contract ────────────────────────────────────────────────────
def test_account_out_carries_auth_type_and_checked_at():
    """FastAPI response models are a whitelist: a field the repo returns but
    the schema omits is dropped SILENTLY, so the frontend's auth_type chip
    would render 'oauth' for every account with nothing anywhere to explain
    why. Same failure mode as the attachment_failures gap in CLAUDE.md."""
    from datetime import datetime, timezone

    from app.schemas.distribution import SocialAccountOut

    out = SocialAccountOut(
        id="727145299382534145",
        scope_type="user",
        scope_id="u1",
        platform="douyin",
        platform_user_id="p1",
        username="HEYGO",
        auth_type="session",
        status="needs_relogin",
        session_checked_at=datetime(2026, 8, 4, tzinfo=timezone.utc),
        created_at=datetime(2026, 8, 1, tzinfo=timezone.utc),
    ).model_dump()
    assert out["auth_type"] == "session"
    assert out["status"] == "needs_relogin"
    assert out["session_checked_at"] is not None
    # Never in the response contract at all — ciphertext included.
    assert "session_state" not in out
