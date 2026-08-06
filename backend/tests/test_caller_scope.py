"""caller_scope() mechanism test (RLS 第三层 PR-2a).

Proves the context manager runs the exact privilege-drop SQL that migration
408's tenant policies key off — WITHOUT a live DB. The RLS *enforcement* of
that SQL is proven separately against nous-db (see PR #1717 / migration 408:
`SET LOCAL ROLE authenticated` + injected `request.jwt.claims` → cross-tenant
SELECT returns 0, cross-tenant INSERT is refused by WITH CHECK, postgres still
sees all). This test locks in that caller_scope() actually emits that SQL, in
its own transaction, with the server-supplied user id.
"""

import json
from unittest.mock import AsyncMock, MagicMock, patch

import pytest


class _SpySession:
    """Records every execute() (statement text + bind params) and the
    transaction lifecycle, standing in for a real AsyncSession."""

    def __init__(self) -> None:
        self.executed: list[tuple[str, dict | None]] = []
        self.began = False
        self.committed = False

    async def execute(self, statement, params=None):
        self.executed.append((str(statement), params))
        return MagicMock()

    def begin(self):
        session = self

        class _Txn:
            async def __aenter__(self):
                session.began = True
                return session

            async def __aexit__(self, *exc):
                if exc[0] is None:
                    session.committed = True
                return False

        return _Txn()

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False


@pytest.mark.asyncio
async def test_caller_scope_emits_role_drop_and_claims_in_own_transaction():
    from app.db import session as session_mod

    spy = _SpySession()
    maker = MagicMock(return_value=spy)

    with patch.object(session_mod, "get_sessionmaker", return_value=maker):
        async with session_mod.caller_scope("user-abc") as s:
            assert s is spy

    stmts = [text for text, _ in spy.executed]
    # 1. drops to the non-superuser authenticated role (loses BYPASSRLS)
    assert any("SET LOCAL ROLE authenticated" in t for t in stmts), stmts
    # 2. injects request.jwt.claims (LOCAL) — what auth.uid() reads in the policies
    claim_calls = [
        (t, p)
        for t, p in spy.executed
        if "set_config" in t and "request.jwt.claims" in t
    ]
    assert claim_calls, spy.executed
    _, params = claim_calls[0]
    claims = json.loads(params["claims"])
    assert claims == {"sub": "user-abc", "role": "authenticated"}
    # 3. own transaction (begun + committed on clean exit) — never an ambient UoW
    assert spy.began and spy.committed


@pytest.mark.asyncio
async def test_caller_scope_never_joins_ambient_unit_of_work():
    """Even with an ambient _request_session set (a unit_of_work() open on the
    task), caller_scope opens its OWN session — so the SET LOCAL ROLE can never
    leak into the outer postgres transaction."""
    from app.db import session as session_mod

    ambient = _SpySession()
    own = _SpySession()
    token = session_mod._request_session.set(ambient)
    try:
        maker = MagicMock(return_value=own)
        with patch.object(session_mod, "get_sessionmaker", return_value=maker):
            async with session_mod.caller_scope("u") as s:
                assert s is own  # NOT the ambient session
        assert not ambient.executed  # ambient transaction untouched
        assert any("SET LOCAL ROLE authenticated" in t for t, _ in own.executed)
    finally:
        session_mod._request_session.reset(token)


@pytest.mark.asyncio
async def test_caller_scope_publishes_itself_as_the_ambient_session():
    """PR-2b: caller_scope BECOMES the ambient _request_session inside the
    block, so repo / gateway read_scope()/write_scope() calls made within it
    JOIN this authenticated transaction (and thus run under RLS) instead of
    opening a competing postgres transaction. This is the whole reason RLS
    reaches the ORM path."""
    from app.db import session as session_mod

    own = _SpySession()
    maker = MagicMock(return_value=own)

    assert session_mod._request_session.get() is None
    with patch.object(session_mod, "get_sessionmaker", return_value=maker):
        async with session_mod.caller_scope("user-xyz") as s:
            # the ambient session repos join IS caller_scope's own session
            assert session_mod._request_session.get() is s is own
            # read_scope()/write_scope() therefore yield THAT session and open
            # NO second transaction of their own (they join the ambient one)
            async with session_mod.read_scope() as rs:
                assert rs is own
            async with session_mod.write_scope() as ws:
                assert ws is own

    # torn down before the block's own begin() commits — ambient restored
    assert session_mod._request_session.get() is None
    # exactly ONE transaction: read/write scope joined, never began a new one
    assert own.began and own.committed


@pytest.mark.asyncio
async def test_caller_scope_restores_the_outer_session_on_exit():
    """When entered inside an outer unit_of_work(), caller_scope temporarily
    points _request_session at its OWN session and restores the outer one via
    reset(token) — never set(None) — on exit."""
    from app.db import session as session_mod

    outer = _SpySession()
    own = _SpySession()
    token = session_mod._request_session.set(outer)
    try:
        maker = MagicMock(return_value=own)
        with patch.object(session_mod, "get_sessionmaker", return_value=maker):
            async with session_mod.caller_scope("u"):
                # inside: the ambient is caller_scope's own session, NOT outer
                assert session_mod._request_session.get() is own
            # after: the OUTER session is restored (reset by token, not None)
            assert session_mod._request_session.get() is outer
    finally:
        session_mod._request_session.reset(token)
