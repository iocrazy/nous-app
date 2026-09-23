"""Pins the CI stubs of ``auth.uid()`` / ``auth.role()`` / ``auth.jwt()`` to
gotrue's real definitions.

Every DB-backed RLS test in this directory runs against the functions that
``supabase/ci_bootstrap.sql`` creates, not gotrue's. Until 2026-09-23 the stub
``auth.uid()`` read only ``request.jwt.claim.sub``, while production's reads
that first and falls back to ``request.jwt.claims ->> 'sub'`` — the GUC current
PostgREST and the backend's ``caller_scope`` (``app/db/session.py``) actually
set. A test that set claims the production way saw ``auth.uid() = NULL`` in CI:
an RLS policy could be green here and behave differently in prod.

Upstream bodies (github.com/supabase/auth, ``migrations/``):
``20220224000811_update_auth_functions.up.sql`` (uid / role) and
``20220531120530_add_auth_jwt_function.up.sql`` (jwt). The cases below are the
behaviour those bodies have — including the ones that look unfriendly (a
non-JSON claims GUC raises, ``jwt()`` is NULL rather than ``{}``). If the stub
is "improved" past upstream, these go red: the point is equality with prod,
not niceness.

Gated on INTEGRATION_DATABASE_URL — skips cleanly in the unit lane:

  INTEGRATION_DATABASE_URL=postgresql://postgres:postgres@127.0.0.1:5432/drift \
      uv run pytest tests/db/test_ci_bootstrap_auth_stubs_integration.py
"""

from __future__ import annotations

import json
import os
import uuid

import pytest

pytest.importorskip("asyncpg")

import asyncpg  # noqa: E402

pytestmark = [pytest.mark.integration, pytest.mark.asyncio]

_TEST_DSN = os.environ.get("INTEGRATION_DATABASE_URL", "").strip()

_skip = pytest.mark.skipif(
    not _TEST_DSN,
    reason="INTEGRATION_DATABASE_URL not set — auth stub tests need a DB.",
)

SUB = "request.jwt.claim.sub"
ROLE = "request.jwt.claim.role"
CLAIM = "request.jwt.claim"
CLAIMS = "request.jwt.claims"


@pytest.fixture
async def pg():
    conn = await asyncpg.connect(_TEST_DSN)
    tr = conn.transaction()
    await tr.start()
    try:
        yield conn
    finally:
        await tr.rollback()
        await conn.close()


async def _set(pg, gucs: dict[str, str] | None = None) -> None:
    """Set all four request GUCs transaction-locally; any not given become ''
    (what PostgREST leaves behind for an absent claim)."""
    values = {name: "" for name in (SUB, ROLE, CLAIM, CLAIMS)}
    values.update(gucs or {})
    for name, value in values.items():
        await pg.execute("SELECT set_config($1, $2, true)", name, value)


async def _read(pg) -> tuple[object, object, object]:
    row = await pg.fetchrow(
        "SELECT auth.uid()::text AS uid, auth.role() AS role, auth.jwt()::text AS jwt"
    )
    jwt = None if row["jwt"] is None else json.loads(row["jwt"])
    return row["uid"], row["role"], jwt


@_skip
async def test_legacy_per_claim_gucs_only(pg):
    sub = str(uuid.uuid4())
    await _set(pg, {SUB: sub, ROLE: "authenticated"})
    assert await _read(pg) == (sub, "authenticated", None)


@_skip
async def test_claims_guc_only_is_what_the_backend_and_postgrest_set(pg):
    """The case the old stub got wrong: it returned NULL for uid and role here."""
    sub = str(uuid.uuid4())
    claims = {"sub": sub, "role": "authenticated", "aud": "authenticated"}
    await _set(pg, {CLAIMS: json.dumps(claims)})
    assert await _read(pg) == (sub, "authenticated", claims)


@_skip
async def test_per_claim_guc_wins_over_claims(pg):
    legacy, modern = str(uuid.uuid4()), str(uuid.uuid4())
    await _set(
        pg,
        {
            SUB: legacy,
            ROLE: "service_role",
            CLAIM: json.dumps({"sub": legacy}),
            CLAIMS: json.dumps({"sub": modern, "role": "authenticated"}),
        },
    )
    assert await _read(pg) == (legacy, "service_role", {"sub": legacy})


@_skip
async def test_all_empty_is_null_everywhere(pg):
    """No Authorization header at all — same shape as the backend's direct
    connection. jwt() is NULL, not '{}' (the old stub said '{}')."""
    await _set(pg)
    assert await _read(pg) == (None, None, None)


@_skip
async def test_never_set_is_null_not_an_error():
    """missing_ok = true: a fresh session that never touched the GUCs."""
    conn = await asyncpg.connect(_TEST_DSN)
    try:
        assert await _read(conn) == (None, None, None)
    finally:
        await conn.close()


@_skip
async def test_claims_without_sub_or_role_is_null(pg):
    await _set(pg, {CLAIMS: json.dumps({"aud": "anon"})})
    assert await _read(pg) == (None, None, {"aud": "anon"})


@_skip
async def test_empty_per_claim_sub_falls_through_to_claims(pg):
    """'' is absent, not a value: nullif lets the claims fallback answer."""
    sub = str(uuid.uuid4())
    await _set(pg, {SUB: "", CLAIMS: json.dumps({"sub": sub})})
    uid, _, _ = await _read(pg)
    assert uid == sub


@_skip
async def test_malformed_claims_raise_like_upstream(pg):
    """Upstream does not guard the ::jsonb cast, so neither does the stub — a
    stub that swallowed this would make CI more lenient than prod."""
    await _set(pg, {CLAIMS: "not-json"})
    with pytest.raises(asyncpg.InvalidTextRepresentationError):
        await pg.fetchval("SELECT auth.uid()")


@_skip
async def test_non_uuid_sub_raises_like_upstream(pg):
    await _set(pg, {CLAIMS: json.dumps({"sub": "not-a-uuid"})})
    with pytest.raises(asyncpg.InvalidTextRepresentationError):
        await pg.fetchval("SELECT auth.uid()")
