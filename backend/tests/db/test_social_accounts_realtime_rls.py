"""Guard for migration 417 — social_accounts' Realtime/RLS + credential收口.

WHY THIS FILE EXISTS
────────────────────
Two earlier attempts at "绑定成功后账号卡片不出现" were declared fixed on
evidence that could not fail:

  * PR #1753 verified "the table is in the supabase_realtime publication".
    It was — and no event was ever delivered, because Realtime's
    ``postgres_changes`` evaluates RLS **as the subscriber**, and
    ``authenticated`` could see 0 rows.
  * Frontend unit tests mock the Supabase client, so they prove the component
    calls ``.on('postgres_changes', …)``. They cannot prove the server sends
    anything.

So the assertions here are deliberately about the *state the delivery path
actually reads*: table-level privileges, column-level privileges, the SELECT
policy, publication membership, replica identity — plus behavioural checks run
as the real ``authenticated`` role.

Every test below goes RED if migration 417 is reverted or weakened. Verified by
actually reverting it (see the PR description's reverse-verification table).

Runs against the CI-built ephemeral schema (ci_bootstrap.sql →
schema_baseline.sql → migrations above the watermark), same as
``test_schema_drift.py``. Skips cleanly when INTEGRATION_DATABASE_URL is unset.
"""

from __future__ import annotations

import os

import asyncpg
import pytest

pytestmark = [pytest.mark.integration, pytest.mark.asyncio]

_TEST_DSN = os.environ.get("INTEGRATION_DATABASE_URL", "").strip()

pytestmark.append(
    pytest.mark.skipif(
        not _TEST_DSN,
        reason="INTEGRATION_DATABASE_URL unset — needs a CI-built Postgres",
    )
)

TABLE = "social_accounts"

# The three Fernet-ciphertext columns. Byte-identical to
# ``social_accounts_repository._SECRET_COLS`` — if that tuple ever grows, this
# one must grow with it, and the grant in a follow-up migration must shrink.
CREDENTIAL_COLUMNS = frozenset({"access_token", "refresh_token", "session_state"})

# Exactly what migration 417 grants. Kept explicit rather than derived from
# "all columns minus credentials": a new column must be a deliberate decision to
# expose, not something a set-difference silently opts in.
EXPECTED_GRANTED_COLUMNS = frozenset(
    {
        "id",
        "scope_type",
        "scope_id",
        "platform",
        "platform_user_id",
        "username",
        "avatar_url",
        "token_expires_at",
        "status",
        "created_by",
        "created_at",
        "updated_at",
        "auth_type",
        "session_checked_at",
        "platform_handle",
        "deleted_at",
    }
)


@pytest.fixture
async def conn():
    c = await asyncpg.connect(_TEST_DSN)
    try:
        yield c
    finally:
        await c.close()


# ── Privilege shape ─────────────────────────────────────────────────────


async def test_anon_has_no_privileges_at_all(conn) -> None:
    """anon must not reach this table by any route.

    It never had a reason to: binding, listing and publishing all require a
    session, and the backend talks to Postgres directly as its own role. The
    blanket GRANT this revokes was table-creation boilerplate.
    """
    rows = await conn.fetch(
        """
        SELECT privilege_type FROM information_schema.role_table_grants
         WHERE table_schema = 'public' AND table_name = $1 AND grantee = 'anon'
        UNION ALL
        SELECT privilege_type FROM information_schema.column_privileges
         WHERE table_schema = 'public' AND table_name = $1 AND grantee = 'anon'
        """,
        TABLE,
    )
    assert rows == [], f"anon still holds {sorted(r['privilege_type'] for r in rows)}"


async def test_authenticated_has_no_table_level_privileges(conn) -> None:
    """Table-level SELECT must be gone, not merely supplemented.

    This is the assertion that catches the most tempting wrong fix. While a
    table-level SELECT grant exists, ``has_column_privilege`` returns true for
    EVERY column — so adding column grants on top of it protects nothing, and
    Realtime would happily ship the ciphertext columns in its payload.
    """
    rows = await conn.fetch(
        """
        SELECT privilege_type FROM information_schema.role_table_grants
         WHERE table_schema = 'public' AND table_name = $1
           AND grantee = 'authenticated'
        """,
        TABLE,
    )
    assert rows == [], (
        "authenticated still holds table-level "
        f"{sorted(r['privilege_type'] for r in rows)} — column grants are "
        "inert while any table-level SELECT survives"
    )


async def test_authenticated_column_grants_are_exactly_the_public_shape(conn) -> None:
    granted = {
        r["column_name"]
        for r in await conn.fetch(
            """
            SELECT DISTINCT column_name FROM information_schema.column_privileges
             WHERE table_schema = 'public' AND table_name = $1
               AND grantee = 'authenticated' AND privilege_type = 'SELECT'
            """,
            TABLE,
        )
    }
    assert granted == EXPECTED_GRANTED_COLUMNS, (
        f"unexpected: {sorted(granted - EXPECTED_GRANTED_COLUMNS)}, "
        f"missing: {sorted(EXPECTED_GRANTED_COLUMNS - granted)}"
    )


async def test_credential_columns_are_never_granted(conn) -> None:
    """The point of the whole exercise, asserted directly."""
    granted = {
        r["column_name"]
        for r in await conn.fetch(
            """
            SELECT DISTINCT column_name FROM information_schema.column_privileges
             WHERE table_schema = 'public' AND table_name = $1
               AND grantee IN ('authenticated', 'anon')
            """,
            TABLE,
        )
    }
    leaked = granted & CREDENTIAL_COLUMNS
    assert not leaked, f"ciphertext columns exposed to clients: {sorted(leaked)}"


async def test_primary_key_column_is_granted(conn) -> None:
    """``id`` must stay granted or Realtime delivers nothing to anyone.

    ``realtime.apply_rls`` short-circuits an event to ``Error 401:
    Unauthorized`` when the subscriber lacks SELECT on any primary-key column:

        elsif action <> 'DELETE' and sum(c.is_selectable::int) <> count(1)
              from unnest(columns) c where c.is_pkey then … 401

    Dropping ``id`` from the grant list therefore silently reinstates the exact
    bug this migration fixes, while every other assertion here still passes.
    """
    assert "id" in EXPECTED_GRANTED_COLUMNS
    row = await conn.fetchrow(
        """
        SELECT 1 FROM information_schema.column_privileges
         WHERE table_schema = 'public' AND table_name = $1
           AND grantee = 'authenticated' AND column_name = 'id'
           AND privilege_type = 'SELECT'
        """,
        TABLE,
    )
    assert row is not None, "authenticated lost SELECT on the primary key column"


# ── Policy / replication shape ──────────────────────────────────────────


async def test_authenticated_select_policy_exists(conn) -> None:
    """Without this, authenticated sees 0 rows and Realtime delivers nothing."""
    rows = await conn.fetch(
        """
        SELECT p.polname, pg_get_expr(p.polqual, p.polrelid) AS using_expr
          FROM pg_policy p
         WHERE p.polrelid = 'public.social_accounts'::regclass
           AND p.polcmd = 'r'
           AND 'authenticated' = ANY (
                 SELECT r.rolname FROM pg_roles r WHERE r.oid = ANY (p.polroles))
        """
    )
    assert rows, "no SELECT policy targets authenticated"
    expr = " ".join(r["using_expr"] for r in rows)
    assert "created_by" in expr, f"policy does not key off created_by: {expr}"


async def test_rls_is_enabled(conn) -> None:
    """Column grants alone would otherwise expose every row to every user."""
    enabled = await conn.fetchval(
        "SELECT relrowsecurity FROM pg_class WHERE oid = 'public.social_accounts'::regclass"
    )
    assert enabled is True


async def test_table_is_in_realtime_publication(conn) -> None:
    row = await conn.fetchrow(
        """
        SELECT 1 FROM pg_publication_tables
         WHERE pubname = 'supabase_realtime' AND schemaname = 'public'
           AND tablename = $1
        """,
        TABLE,
    )
    assert row is not None, "necessary (not sufficient) — see migration 413/417"


async def test_replica_identity_stays_default(conn) -> None:
    """FULL would write the whole old row — ciphertext included — into the WAL.

    The subscriber only needs "some row changed, go refetch"; it never reads the
    payload. So there is no upside to trade against that exposure.
    """
    # ::text because pg_class.relreplident is `"char"`, which asyncpg hands back
    # as bytes — comparing that to 'd' fails for the wrong reason.
    identity = await conn.fetchval(
        "SELECT relreplident::text FROM pg_class"
        " WHERE oid = 'public.social_accounts'::regclass"
    )
    assert identity == "d", f"replica identity is {identity!r}, expected 'd'"


# ── Behavioural checks, run as the real `authenticated` role ────────────

_OWNER = "b2180063-6860-4f97-9785-ad4eede16064"
_STRANGER = "8e1584e3-9c29-4a5b-90fe-125b74259f7f"

# Literals rather than bind parameters: each uid feeds both `scope_id` (text)
# and `created_by` (uuid), and asyncpg infers exactly one type per placeholder
# ("inconsistent types deduced for parameter $1"). The values are the two
# module constants above, so there is nothing to interpolate unsafely.
_SEED = f"""
INSERT INTO public.social_accounts
    (id, scope_type, scope_id, platform, platform_user_id, username,
     access_token, refresh_token, session_state, status, created_by, auth_type)
VALUES
    (9200000000000001, 'user', '{_OWNER}', 'douyin', 'guard-own', 'OwnAcct',
     'CIPHER-A', 'CIPHER-R', 'CIPHER-S', 'active', '{_OWNER}', 'session'),
    (9200000000000002, 'user', '{_STRANGER}', 'douyin', 'guard-other', 'OtherAcct',
     'CIPHER-A', 'CIPHER-R', 'CIPHER-S', 'active', '{_STRANGER}', 'session')
"""


async def _become_authenticated(conn, uid: str) -> None:
    """Switch to the authenticated role and publish `uid` as the JWT subject.

    Both GUC spellings are set on purpose. Production's ``auth.uid()``
    coalesces ``request.jwt.claim.sub`` with ``request.jwt.claims->>'sub'``,
    and Realtime sets the latter; the CI stub in ci_bootstrap.sql reads only
    the former. Setting both makes this test exercise the same policy in either
    environment instead of passing vacuously in one of them.
    """
    await conn.execute("SET LOCAL ROLE authenticated")
    await conn.execute("SELECT set_config('request.jwt.claim.sub', $1, true)", uid)
    await conn.execute(
        "SELECT set_config('request.jwt.claims', $1, true)",
        f'{{"sub":"{uid}","role":"authenticated"}}',
    )


@pytest.mark.parametrize("column", sorted(CREDENTIAL_COLUMNS))
async def test_authenticated_cannot_select_credential_column(conn, column) -> None:
    tr = conn.transaction()
    await tr.start()
    try:
        await _become_authenticated(conn, _OWNER)
        with pytest.raises(asyncpg.InsufficientPrivilegeError):
            await conn.fetch(f"SELECT {column} FROM public.social_accounts")
    finally:
        await tr.rollback()


async def test_authenticated_cannot_select_star(conn) -> None:
    """``SELECT *`` expands to every column, so it must be refused outright.

    This is the shape a curious client actually types, and the one a
    table-level grant would have quietly allowed.
    """
    tr = conn.transaction()
    await tr.start()
    try:
        await _become_authenticated(conn, _OWNER)
        with pytest.raises(asyncpg.InsufficientPrivilegeError):
            await conn.fetch("SELECT * FROM public.social_accounts")
    finally:
        await tr.rollback()


@pytest.mark.parametrize(
    "statement",
    [
        "UPDATE public.social_accounts SET username = 'x' WHERE id = 9200000000000001",
        "DELETE FROM public.social_accounts WHERE id = 9200000000000001",
        "INSERT INTO public.social_accounts (id, scope_type, scope_id, platform,"
        " platform_user_id, username, status, created_by, auth_type) VALUES"
        " (9200000000000003, 'user', 'x', 'douyin', 'y', 'z', 'active',"
        " '00000000-0000-0000-0000-000000000000', 'session')",
    ],
)
async def test_authenticated_cannot_write(conn, statement) -> None:
    """Every write goes through the backend; clients get read-only access."""
    tr = conn.transaction()
    await tr.start()
    try:
        await conn.execute(_SEED)
        await _become_authenticated(conn, _OWNER)
        with pytest.raises(asyncpg.InsufficientPrivilegeError):
            await conn.execute(statement)
    finally:
        await tr.rollback()


async def test_owner_sees_own_row_and_only_own_row(conn) -> None:
    """The positive case and the isolation case in one assertion.

    Asserting only "the owner sees a row" would stay green if the policy were
    ``USING (true)`` — which is precisely the over-correction this migration
    must not become.
    """
    tr = conn.transaction()
    await tr.start()
    try:
        await conn.execute(_SEED)
        await _become_authenticated(conn, _OWNER)
        visible = [
            r["id"]
            for r in await conn.fetch(
                "SELECT id FROM public.social_accounts"
                " WHERE id IN (9200000000000001, 9200000000000002) ORDER BY id"
            )
        ]
        assert visible == [
            9200000000000001
        ], f"expected only the owner's row, got {visible}"
    finally:
        await tr.rollback()


async def test_walrus_rls_check_passes_for_the_owner(conn) -> None:
    """Run the exact statement Realtime runs before it delivers an event.

    ``realtime.build_prepared_statement_sql`` produces
    ``select exists(select 1 from <entity> where <pk> = <value>)`` and
    ``apply_rls`` delivers the event only when that returns true. Asserting the
    ORM-visible row count is not the same thing: this is the predicate the
    delivery path itself evaluates, under the same role and claims.
    """
    tr = conn.transaction()
    await tr.start()
    try:
        await conn.execute(_SEED)
        await _become_authenticated(conn, _OWNER)
        assert (
            await conn.fetchval(
                "SELECT exists(SELECT 1 FROM public.social_accounts"
                " WHERE id = 9200000000000001)"
            )
            is True
        )
        assert (
            await conn.fetchval(
                "SELECT exists(SELECT 1 FROM public.social_accounts"
                " WHERE id = 9200000000000002)"
            )
            is False
        )
    finally:
        await tr.rollback()
