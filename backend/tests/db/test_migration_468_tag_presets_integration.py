"""mig 468 against a real Postgres: initial tags are per-user, and nothing shared.

Why this has to run on a live database
======================================
Every claim mig 468 makes is a claim about **Postgres objects**, and none of
them can be answered by a stubbed session:

* "two users can hold the same slug" is a question about a unique index. Under
  mig 467's global ``uniq_tags_slug`` the second user's seed is rejected — a
  unit test with a fake session would happily report success.
* "there are no system tags any more" is a CHECK constraint.
* "the write policies stopped asking about ``type``" is ``pg_policy``.
* "``merge_tags`` is no longer reachable from the browser" is an ACL.
* ``seed_initial_tags`` is PL/pgSQL. It does not exist outside a database.

The CI schema is built the usual way (ci_bootstrap → schema_baseline →
migrations above the watermark), so this also proves the migration *applies*.

  INTEGRATION_DATABASE_URL=postgresql://postgres:postgres@127.0.0.1:5432/drift \\
    uv run pytest tests/db/test_migration_468_tag_presets_integration.py -v

Skips cleanly when the DSN is unset. The schema-drift workflow runs it through
pytest-no-full-skip.sh, so a full skip there is RED, not green.
"""

from __future__ import annotations

import os
import uuid

import asyncpg
import pytest

pytestmark = [pytest.mark.integration, pytest.mark.asyncio]

_TEST_DSN = os.environ.get("INTEGRATION_DATABASE_URL", "").strip()

pytest.importorskip("asyncpg")

_skip = pytest.mark.skipif(
    not _TEST_DSN,
    reason="INTEGRATION_DATABASE_URL not set — mig 468 needs a real database.",
)

_PIPELINE_SLUGS = {"transcript", "summary", "analyze"}


@pytest.fixture
async def conn():
    """A connection whose writes are always rolled back.

    Everything here seeds ``auth.users`` and ``tags``; leaking those into a
    shared drift database would make the later assertions of whichever test
    runs next depend on ordering.
    """
    c = await asyncpg.connect(_TEST_DSN)
    tx = c.transaction()
    await tx.start()
    try:
        yield c
    finally:
        await tx.rollback()
        await c.close()


async def _mk_user(conn, email: str) -> uuid.UUID:
    """A user row, with NO signup trigger attached.

    The drift database has no ``on_auth_user_created`` — it lives on
    ``auth.users``, which CI stubs — and mig 468 deliberately does not create
    one (see the migration's comment: doing so turned five unrelated
    integration files red). So this really is just an INSERT, and every test
    below except the signup one seeds explicitly.
    """
    uid = uuid.uuid4()
    await conn.execute("INSERT INTO auth.users (id, email) VALUES ($1, $2)", uid, email)
    return uid


# ---------------------------------------------------------------------------
# The template list
# ---------------------------------------------------------------------------


@_skip
async def test_the_three_pipeline_slugs_are_in_the_preset_list(conn):
    """Without these three, the AI intent checkboxes attach nothing — silently.

    That silence is the whole reason mig 467 existed; a preset list that lost
    them would reintroduce it from the other end.
    """
    rows = await conn.fetch(
        "SELECT slug FROM tag_presets WHERE slug = ANY($1::text[])",
        sorted(_PIPELINE_SLUGS),
    )
    assert {r["slug"] for r in rows} == _PIPELINE_SLUGS


@_skip
async def test_presets_are_invisible_to_the_browser(conn):
    """RLS on, zero policies: PostgREST can reach the table and read nothing.

    Presets are a seeding template. A user editing one would change what the
    NEXT signup gets, which is not a thing any user should be able to do by
    accident.
    """
    enabled = await conn.fetchval(
        "SELECT relrowsecurity FROM pg_class WHERE oid = 'public.tag_presets'::regclass"
    )
    policies = await conn.fetchval(
        "SELECT count(*) FROM pg_policy WHERE polrelid = 'public.tag_presets'::regclass"
    )
    assert enabled is True
    assert policies == 0


# ---------------------------------------------------------------------------
# Seeding
# ---------------------------------------------------------------------------


@_skip
async def test_a_new_user_gets_their_own_copy_of_every_preset(conn):
    user = await _mk_user(conn, f"seed-{uuid.uuid4().hex[:8]}@test.dev")
    preset_count = await conn.fetchval("SELECT count(*) FROM tag_presets")

    added = await conn.fetchval("SELECT public.seed_initial_tags($1)", user)

    assert added == preset_count
    owned = await conn.fetchrow(
        "SELECT count(*) AS n, count(*) FILTER (WHERE type = 'user') AS as_user, "
        "       count(*) FILTER (WHERE slug IS NOT NULL) AS with_slug "
        "  FROM tags WHERE user_id = $1",
        user,
    )
    assert owned["n"] == preset_count
    # Owned outright: type='user' is what every downstream edit check keys on.
    assert owned["as_user"] == preset_count
    # …and carrying the slug, or the automation could not find them after a rename.
    assert owned["with_slug"] == preset_count


@_skip
async def test_seeding_twice_adds_nothing(conn):
    """The signup trigger is not the only caller (the migration backfills too),
    and a re-run must not double a user's sidebar."""
    user = await _mk_user(conn, f"twice-{uuid.uuid4().hex[:8]}@test.dev")

    first = await conn.fetchval("SELECT public.seed_initial_tags($1)", user)
    second = await conn.fetchval("SELECT public.seed_initial_tags($1)", user)

    assert first > 0
    assert second == 0


@_skip
async def test_two_users_hold_the_same_slug(conn):
    """**The point of the whole migration.**

    Under mig 467's global ``uniq_tags_slug`` this insert is a unique violation
    and the second user ends up with no transcript tag at all. Per-user
    uniqueness is what lets everyone own their own copy.
    """
    a = await _mk_user(conn, f"a-{uuid.uuid4().hex[:8]}@test.dev")
    b = await _mk_user(conn, f"b-{uuid.uuid4().hex[:8]}@test.dev")

    await conn.execute("SELECT public.seed_initial_tags($1)", a)
    await conn.execute("SELECT public.seed_initial_tags($1)", b)

    holders = await conn.fetchval(
        "SELECT count(*) FROM tags WHERE slug = 'transcript' AND user_id = ANY($1::uuid[])",
        [a, b],
    )
    assert holders == 2


@_skip
async def test_one_users_rename_does_not_touch_anyone_else(conn):
    """ "用户都能修改" has to mean *their own*. Before this migration the row was
    shared, so A's rename was B's rename."""
    a = await _mk_user(conn, f"ra-{uuid.uuid4().hex[:8]}@test.dev")
    b = await _mk_user(conn, f"rb-{uuid.uuid4().hex[:8]}@test.dev")
    await conn.execute("SELECT public.seed_initial_tags($1)", a)
    await conn.execute("SELECT public.seed_initial_tags($1)", b)

    await conn.execute(
        "UPDATE tags SET name = '转录啦' WHERE user_id = $1 AND slug = 'transcript'", a
    )

    assert (
        await conn.fetchval(
            "SELECT name FROM tags WHERE user_id = $1 AND slug = 'transcript'", b
        )
        == "Transcript"
    )
    # A's automation still resolves — that is what the slug is for.
    assert (
        await conn.fetchval(
            "SELECT name FROM tags WHERE user_id = $1 AND slug = 'transcript'", a
        )
        == "转录啦"
    )


@_skip
async def test_a_user_who_already_has_the_name_keeps_their_own_row(conn):
    """No duplicate, and no second row fighting for ``unique_tag_per_scope``.

    The migration's step 3a stamps the slug onto the row they already own; this
    checks the seeder's half — that it declines to create a twin.
    """
    user = await _mk_user(conn, f"dup-{uuid.uuid4().hex[:8]}@test.dev")
    await conn.execute(
        "INSERT INTO tags (name, type, user_id) VALUES ('Music', 'user', $1)", user
    )

    await conn.execute("SELECT public.seed_initial_tags($1)", user)

    named_music = await conn.fetchval(
        "SELECT count(*) FROM tags WHERE user_id = $1 AND lower(name) = 'music'", user
    )
    assert named_music == 1


# ---------------------------------------------------------------------------
# "There are no system tags any more" — structurally, not by convention
# ---------------------------------------------------------------------------


@_skip
async def test_a_system_tag_can_no_longer_be_created(conn):
    user = await _mk_user(conn, f"sys-{uuid.uuid4().hex[:8]}@test.dev")
    with pytest.raises(asyncpg.CheckViolationError):
        await conn.execute(
            "INSERT INTO tags (name, type, user_id) VALUES ('Nope', 'system', $1)", user
        )


@_skip
async def test_no_shared_rows_survived_the_migration(conn):
    """A tag with no owner is a tag everybody can see (the old ``tags_select``
    arm) and nobody can own."""
    assert await conn.fetchval("SELECT count(*) FROM tags WHERE user_id IS NULL") == 0


@_skip
async def test_the_write_policies_stopped_asking_about_type(conn):
    """``type = 'user'`` was a proxy for "is it yours"; ``user_id = auth.uid()``
    says it directly, and it is the clause that was actually load-bearing."""
    rows = await conn.fetch(
        "SELECT polname, "
        "       coalesce(pg_get_expr(polqual, polrelid), '') "
        "       || coalesce(pg_get_expr(polwithcheck, polrelid), '') AS expr "
        "  FROM pg_policy WHERE polrelid = 'public.tags'::regclass"
    )
    assert rows, "the tags policies vanished — RLS is not optional here"
    for row in rows:
        assert "type" not in row["expr"], f"{row['polname']} still gates on type"
        assert "auth.uid()" in row["expr"], f"{row['polname']} lost its owner check"


# ---------------------------------------------------------------------------
# merge_tags
# ---------------------------------------------------------------------------


@_skip
async def test_merge_tags_is_not_reachable_from_the_browser(conn):
    """SECURITY DEFINER + caller-supplied ``p_user`` + EXECUTE for authenticated
    is the mig 463 shape (CLAUDE.md 2026-09-11): the publishable key is in the
    browser bundle and ``/rest/v1/rpc/merge_tags`` is a public endpoint, so that
    grant let any signed-in user pass someone else's uuid and delete their tags.

    Nothing needs it — the browser posts to /api/v1/tags/merge and the backend
    reaches the proc over its own SQL session.
    """
    for role in ("anon", "authenticated"):
        assert not await conn.fetchval(
            "SELECT has_function_privilege($1, "
            "'public.merge_tags(text, text[], uuid)', 'EXECUTE')",
            role,
        ), f"{role} can still call merge_tags"

    assert await conn.fetchval(
        "SELECT has_function_privilege('service_role', "
        "'public.merge_tags(text, text[], uuid)', 'EXECUTE')"
    ), "service_role lost the grant the backend path relies on"


@_skip
async def test_merge_tags_merges_two_initial_tags(conn):
    """Previously impossible: both are presets, and the proc refused anything
    that was not ``type='user'``. Now they are the user's own tags."""
    user = await _mk_user(conn, f"merge-{uuid.uuid4().hex[:8]}@test.dev")
    await conn.execute("SELECT public.seed_initial_tags($1)", user)

    target, source = await conn.fetch(
        "SELECT id FROM tags WHERE user_id = $1 AND slug IN ('music', 'dance') "
        "ORDER BY slug",
        user,
    )

    await conn.fetchval(
        "SELECT merge_tags($1::text, ARRAY[$2]::text[], $3::uuid)",
        str(target["id"]),
        str(source["id"]),
        user,
    )

    assert (
        await conn.fetchval("SELECT count(*) FROM tags WHERE id = $1", source["id"])
        == 0
    )
    assert (
        await conn.fetchval("SELECT count(*) FROM tags WHERE id = $1", target["id"])
        == 1
    )


@_skip
async def test_merge_tags_still_refuses_someone_elses_tag(conn):
    """Dropping the ``type`` guard must not have dropped the ownership guard —
    they were in the same two IF statements."""
    a = await _mk_user(conn, f"ma-{uuid.uuid4().hex[:8]}@test.dev")
    b = await _mk_user(conn, f"mb-{uuid.uuid4().hex[:8]}@test.dev")
    await conn.execute("SELECT public.seed_initial_tags($1)", a)
    await conn.execute("SELECT public.seed_initial_tags($1)", b)

    a_music = await conn.fetchval(
        "SELECT id FROM tags WHERE user_id = $1 AND slug = 'music'", a
    )
    b_dance = await conn.fetchval(
        "SELECT id FROM tags WHERE user_id = $1 AND slug = 'dance'", b
    )

    with pytest.raises(asyncpg.RaiseError, match="not yours"):
        await conn.fetchval(
            "SELECT merge_tags($1::text, ARRAY[$2]::text[], $3::uuid)",
            str(a_music),
            str(b_dance),
            a,
        )


# ---------------------------------------------------------------------------
# The signup path
# ---------------------------------------------------------------------------


@_skip
async def test_a_signup_arrives_already_stocked(conn):
    """End to end through the REAL trigger, not by calling the seeder directly.

    A wiring mistake in ``handle_new_user`` is exactly the kind of thing that
    only shows up when a human signs up and finds an empty sidebar, so asserting
    "the function body mentions seed_initial_tags" would not be worth much.

    The trigger is created HERE rather than by the migration, and that is a
    deliberate trade. It exists in prod already (mig 053; `pg_get_triggerdef`
    confirms `AFTER INSERT ON auth.users ... EXECUTE FUNCTION
    handle_new_user()`), but not in the drift database, because it hangs off
    `auth.users` — a Supabase-runtime table that `schema_baseline.sql` does not
    dump. Creating it in the migration instead would attach it to every other
    integration file's synthetic `auth.users` inserts; five of them went red
    that way. Creating it inside this rolled-back transaction gives the same
    end-to-end proof and touches nobody else.
    """
    await conn.execute(
        "CREATE TRIGGER on_auth_user_created AFTER INSERT ON auth.users "
        "FOR EACH ROW EXECUTE FUNCTION public.handle_new_user()"
    )
    preset_count = await conn.fetchval("SELECT count(*) FROM tag_presets")

    user = await _mk_user(conn, f"signup-{uuid.uuid4().hex[:8]}@test.dev")

    owned = await conn.fetchval("SELECT count(*) FROM tags WHERE user_id = $1", user)
    assert owned == preset_count, (
        "a fresh signup came out with %d tags instead of %d — handle_new_user "
        "is not calling seed_initial_tags" % (owned, preset_count)
    )
