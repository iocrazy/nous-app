"""DB-backed guard for migration 496: the four SECURITY DEFINER identity helpers
that RLS policies call must answer only for the caller, not for any uuid.

``is_conversation_member`` / ``conversation_member_joined_at`` /
``get_user_team_ids`` / ``get_user_team_ids_text`` bypass RLS (definer) and take
the target user as a plain argument, and anon/authenticated keep EXECUTE because
49 policies (61 call sites) call them with the CALLER's privileges (so 491's
REVOKE was not an option). Before 496, the publishable anon key could ask PostgREST
``/rest/v1/rpc/get_user_team_ids`` which teams any uuid belongs to.

Only Postgres can answer this: the guard reads the ``role`` GUC and the JWT
claims, and the backend unit lane never runs as ``anon``/``authenticated``.

Four things are pinned, all inside rolled-back transactions on production-shaped
rows (two users, two teams, one library per team, a ``joined``-history
conversation per user):

  (a) anon — with and without an anon JWT — gets the empty answer for anyone;
  (b) authenticated A gets the empty answer for B and the real answer for A;
  (c) the backend's direct ``postgres`` connection and ``service_role`` get the
      real answer for any user (negative control: a guard that returned empty
      for everyone would pass (a) and (b));
  (d) the policies that call the helpers still show A its own rows and not B's.

Claims are set ONLY as ``request.jwt.claims`` (what current PostgREST and the
backend's ``caller_scope`` set); the legacy ``request.jwt.claim.sub`` /
``.role`` GUCs are cleared. That relies on the CI stub ``auth.uid()`` in
supabase/ci_bootstrap.sql mirroring gotrue's claims fallback — pinned by
test_ci_bootstrap_auth_stubs_integration.py. Under the old sub-only stub the
"own answer" and policy tests below go red (auth.uid() would be NULL).

Gated on INTEGRATION_DATABASE_URL — skips cleanly in the unit lane:

  INTEGRATION_DATABASE_URL=postgresql://postgres:postgres@127.0.0.1:5432/drift \
      uv run pytest tests/db/test_migration_496_rls_helper_identity_guard_integration.py
"""

from __future__ import annotations

import json
import os
import uuid
from dataclasses import dataclass

import pytest

pytest.importorskip("asyncpg")

import asyncpg  # noqa: E402

pytestmark = [pytest.mark.integration, pytest.mark.asyncio]

_TEST_DSN = os.environ.get("INTEGRATION_DATABASE_URL", "").strip()

_skip = pytest.mark.skipif(
    not _TEST_DSN,
    reason="INTEGRATION_DATABASE_URL not set — RLS helper tests need a DB.",
)

# Tables the policy checks read. Production Supabase grants these to the browser
# roles; ci_bootstrap.sql creates the roles bare, so grant inside the
# rolled-back transaction to get as far as the policy.
_POLICY_TABLES = (
    "public.team_members",
    "public.libraries",
    "public.conversations",
    "public.conversation_members",
    "public.messages",
)


@dataclass(frozen=True)
class World:
    user_a: str
    user_b: str
    team_a: int
    team_b: int
    conv_a: int
    conv_b: int
    joined_a: object  # datetime of A's join to conv_a


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


async def _new_user(pg) -> str:
    uid = str(uuid.uuid4())
    await pg.execute(
        "INSERT INTO auth.users (id, email) VALUES ($1::uuid, $2)",
        uid,
        f"rls-guard-{uid[:8]}@example.com",
    )
    return uid


async def _new_team(pg, owner: str) -> int:
    # teams_add_owner_trigger inserts the owner into team_members, exactly as
    # production does — no hand-written membership row.
    return await pg.fetchval(
        "INSERT INTO public.teams (name, owner_id) VALUES ($1, $2::uuid) RETURNING id",
        f"RLS Guard Team {owner[:8]}",
        owner,
    )


async def _new_conversation(pg, team: int, user: str) -> tuple[int, object]:
    conv = await pg.fetchval(
        "INSERT INTO public.conversations (type, scope_id, created_by, history_mode)"
        " VALUES ('group', $1, $2::uuid, 'joined') RETURNING id",
        team,
        user,
    )
    joined = await pg.fetchval(
        "INSERT INTO public.conversation_members"
        " (conversation_id, member_type, user_id, joined_at)"
        " VALUES ($1, 'user', $2::uuid, now() - interval '1 hour')"
        " RETURNING joined_at",
        conv,
        user,
    )
    # One message from before the member joined (hidden under 'joined' history)
    # and one after — the only policy use of conversation_member_joined_at.
    for seq, offset in ((1, "-2 hours"), (2, "-5 minutes")):
        await pg.execute(
            "INSERT INTO public.messages"
            " (conversation_id, seq, sender_type, sender_id, body, created_at)"
            " VALUES ($1, $2, 'user', $3::uuid, '{\"text\": \"Test Message\"}',"
            " now() + $4::text::interval)",
            conv,
            seq,
            user,
            offset,
        )
    return conv, joined


@pytest.fixture
async def world(pg) -> World:
    user_a = await _new_user(pg)
    user_b = await _new_user(pg)
    team_a = await _new_team(pg, user_a)
    team_b = await _new_team(pg, user_b)
    for team, owner in ((team_a, user_a), (team_b, user_b)):
        await pg.execute(
            "INSERT INTO public.libraries (name, scope_type, scope_id, created_by)"
            " VALUES ('Test Library', 'team', $1, $2::uuid)",
            str(team),
            owner,
        )
    conv_a, joined_a = await _new_conversation(pg, team_a, user_a)
    conv_b, _ = await _new_conversation(pg, team_b, user_b)
    for table in _POLICY_TABLES:
        await pg.execute(f"GRANT SELECT ON {table} TO anon, authenticated")
    await pg.execute("GRANT USAGE ON SCHEMA auth TO anon, authenticated")
    return World(user_a, user_b, team_a, team_b, conv_a, conv_b, joined_a)


async def _become(pg, role: str | None, sub: str | None = None, jwt: bool = True):
    """Switch the session to what that caller looks like on the wire.

    role=None is the backend's direct Supavisor connection: no SET ROLE, no
    claims. jwt=False with anon is a PostgREST request carrying no
    Authorization header at all — claims empty, auth.role() NULL.
    """
    await pg.execute("RESET ROLE")
    claims: dict[str, str] = {}
    if role is not None and jwt:
        claims["role"] = role
    if sub is not None:
        claims["sub"] = sub
    # Only the claims GUC carries identity; the legacy per-claim GUCs are
    # cleared so auth.uid() must resolve through the claims fallback — the
    # path production's PostgREST and caller_scope take.
    await pg.execute(
        "SELECT set_config('request.jwt.claims', $1, true),"
        " set_config('request.jwt.claim.sub', '', true),"
        " set_config('request.jwt.claim.role', '', true)",
        json.dumps(claims) if claims else "",
    )
    if role is not None:
        await pg.execute(f"SET LOCAL ROLE {role}")


async def _answers(pg, w: World, target: str) -> dict[str, object]:
    conv = w.conv_a if target == w.user_a else w.conv_b
    return {
        "team_ids": sorted(
            await pg.fetchval(
                "SELECT coalesce(array_agg(t), '{}') FROM public.get_user_team_ids($1::uuid) t",
                target,
            )
        ),
        "team_ids_text": sorted(
            await pg.fetchval(
                "SELECT coalesce(array_agg(t), '{}')"
                " FROM public.get_user_team_ids_text($1::uuid) t",
                target,
            )
        ),
        "is_member": await pg.fetchval(
            "SELECT public.is_conversation_member($1::uuid, $2)", target, conv
        ),
        "joined_at": await pg.fetchval(
            "SELECT public.conversation_member_joined_at($1::uuid, $2)", target, conv
        ),
    }


_EMPTY = {"team_ids": [], "team_ids_text": [], "is_member": False, "joined_at": None}


def _real(w: World, target: str) -> dict[str, object]:
    team = w.team_a if target == w.user_a else w.team_b
    return {
        "team_ids": [team],
        "team_ids_text": [str(team)],
        "is_member": True,
    }


def _without_joined(d: dict[str, object]) -> dict[str, object]:
    return {k: v for k, v in d.items() if k != "joined_at"}


@_skip
@pytest.mark.parametrize("jwt", [True, False], ids=["anon-jwt", "no-jwt"])
async def test_anon_learns_nothing_about_anyone(pg, world: World, jwt: bool):
    await _become(pg, "anon", jwt=jwt)
    for target in (world.user_a, world.user_b):
        got = await _answers(pg, world, target)
        assert got == _EMPTY, (
            f"anon (jwt={jwt}) got {got} for {target} — the publishable key can "
            f"enumerate memberships through /rest/v1/rpc for any uuid."
        )


@_skip
async def test_authenticated_learns_nothing_about_someone_else(pg, world: World):
    await _become(pg, "authenticated", sub=world.user_a)
    got = await _answers(pg, world, world.user_b)
    assert got == _EMPTY, f"user A read user B's memberships: {got}"


@_skip
async def test_authenticated_still_gets_its_own_answer(pg, world: World):
    await _become(pg, "authenticated", sub=world.user_a)
    got = await _answers(pg, world, world.user_a)
    assert _without_joined(got) == _real(world, world.user_a)
    assert got["joined_at"] == world.joined_a


@_skip
@pytest.mark.parametrize(
    "role", [None, "service_role"], ids=["postgres", "service_role"]
)
async def test_trusted_server_roles_answer_for_any_user(pg, world: World, role):
    """Negative control: without it, a guard that returns empty for everyone
    would pass both tests above while silently hiding every team-scoped row."""
    await _become(pg, role)
    for target in (world.user_a, world.user_b):
        got = await _answers(pg, world, target)
        assert _without_joined(got) == _real(world, target), (role, target, got)
        assert got["joined_at"] is not None


async def _visible(pg, sql: str, *args) -> int:
    return await pg.fetchval(f"SELECT count(*) FROM ({sql}) s", *args)


@_skip
async def test_policies_still_scope_rows_to_the_caller(pg, world: World):
    """(d) one table per helper: the policies pass auth.uid(), so the guard is
    always satisfied for them — A sees exactly A's rows, never B's."""
    await _become(pg, "authenticated", sub=world.user_a)

    # get_user_team_ids — "Members can view team members"
    team_rows = await pg.fetch("SELECT team_id, user_id::text FROM public.team_members")
    assert [(r["team_id"], r["user_id"]) for r in team_rows] == [
        (world.team_a, world.user_a)
    ]

    # get_user_team_ids_text — "Team members can read libraries"
    lib_scopes = [
        r["scope_id"] for r in await pg.fetch("SELECT scope_id FROM public.libraries")
    ]
    assert lib_scopes == [str(world.team_a)]

    # is_conversation_member — conversation_members_select / conversations_select
    member_convs = [
        r["conversation_id"]
        for r in await pg.fetch(
            "SELECT conversation_id FROM public.conversation_members"
        )
    ]
    assert member_convs == [world.conv_a]
    convs = [r["id"] for r in await pg.fetch("SELECT id FROM public.conversations")]
    assert convs == [world.conv_a]

    # conversation_member_joined_at — messages_select under 'joined' history:
    # only the message after A joined, nothing from B's conversation.
    msgs = await pg.fetch("SELECT conversation_id, seq FROM public.messages")
    assert [(r["conversation_id"], r["seq"]) for r in msgs] == [(world.conv_a, 2)]


@_skip
async def test_anon_sees_no_policy_rows(pg, world: World):
    """The policies apply TO PUBLIC (anon included). They must keep evaluating —
    no permission error — and return nothing, as before 496."""
    await _become(pg, "anon")
    for table in _POLICY_TABLES:
        assert await _visible(pg, f"SELECT 1 FROM {table}") == 0, table
