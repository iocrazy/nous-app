"""mig 469: a signup must not be able to fail on its own username.

`handle_new_user()` is an AFTER INSERT trigger on `auth.users`, so anything it
raises takes the user row with it — the person does not end up with a broken
profile, they end up with no account and a "signup failed".

Two shapes could do that, and both are reachable in production today:

* **No email.** `split_part(NULL,'@',1)` is NULL → the team name is NULL →
  `teams.name` NOT NULL. Production has `GOTRUE_EXTERNAL_PHONE_ENABLED=true`
  and `GOTRUE_EXTERNAL_ANONYMOUS_USERS_ENABLED=true`; the only reason this has
  never fired is that all 11 users so far signed up with an email.
* **Duplicate local part.** `user_profiles.username` is UNIQUE and the
  derivation is the email's local part, so `test@a.com` and `test@b.com` both
  want `test`. `ON CONFLICT (id) DO NOTHING` does not cover a violation of
  `user_profiles_username_key`. Production holds the near miss already:
  `test@mediahub.dev` is `test`, and `test@test.com` only escaped because its
  signup form passed a username in the metadata.

Each case below **also runs the pre-469 function** and asserts it fails, so
these are regression pins rather than descriptions of current behaviour.

  INTEGRATION_DATABASE_URL=postgresql://postgres:postgres@127.0.0.1:5432/drift \\
    uv run pytest tests/db/test_migration_469_signup_username_integration.py -v

Skips cleanly when the DSN is unset; the schema-drift workflow runs it through
pytest-no-full-skip.sh, so a full skip there is RED.
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
    reason="INTEGRATION_DATABASE_URL not set — mig 469 needs a real database.",
)

# The function exactly as it stood before this migration. Installed by the
# `pre_469` fixture so each case can show the failure it fixes, in the same
# transaction, against the same schema. Copied from prod's `pg_proc.prosrc`.
_OLD_FUNCTION = """
CREATE OR REPLACE FUNCTION public.handle_new_user()
RETURNS TRIGGER AS $$
DECLARE
    uname TEXT;
BEGIN
    uname := COALESCE(
        NEW.raw_user_meta_data->>'username',
        split_part(NEW.email, '@', 1)
    );
    INSERT INTO public.user_profiles (id, username, role)
    VALUES (NEW.id, uname, 'user')
    ON CONFLICT (id) DO NOTHING;
    INSERT INTO public.teams (name, owner_id, kind)
    VALUES (uname || '''s Workspace', NEW.id, 'personal')
    ON CONFLICT DO NOTHING;
    PERFORM public.seed_initial_tags(NEW.id);
    RETURN NEW;
END;
$$ LANGUAGE plpgsql SECURITY DEFINER SET search_path = public;
"""


@pytest.fixture
async def conn():
    """A connection whose writes always roll back, with the signup trigger on.

    The trigger lives on `auth.users` and the drift database has never carried
    it (baseline dumps `public` only), so each test installs it here — the same
    contained approach mig 468's suite uses, for the same reason: creating it in
    a migration would attach it to every other integration file's synthetic
    `auth.users` inserts.
    """
    c = await asyncpg.connect(_TEST_DSN)
    tx = c.transaction()
    await tx.start()
    await c.execute(
        "CREATE TRIGGER on_auth_user_created AFTER INSERT ON auth.users "
        "FOR EACH ROW EXECUTE FUNCTION public.handle_new_user()"
    )
    try:
        yield c
    finally:
        await tx.rollback()
        await c.close()


@pytest.fixture
async def pre_469(conn):
    """Swap in the pre-migration function, so a test can watch it fail."""
    await conn.execute(_OLD_FUNCTION)
    return conn


async def _signup(conn, **cols) -> uuid.UUID:
    """Insert an auth.users row — i.e. sign somebody up through the trigger."""
    uid = uuid.uuid4()
    keys = ["id"] + list(cols)
    placeholders = ", ".join(f"${i + 1}" for i in range(len(keys)))
    await conn.execute(
        f"INSERT INTO auth.users ({', '.join(keys)}) VALUES ({placeholders})",
        uid,
        *cols.values(),
    )
    return uid


async def _profile(conn, uid):
    return await conn.fetchrow(
        "SELECT p.username, t.name AS team_name "
        "  FROM public.user_profiles p "
        "  LEFT JOIN public.teams t ON t.owner_id = p.id AND t.kind = 'personal' "
        " WHERE p.id = $1",
        uid,
    )


# ---------------------------------------------------------------------------
# The happy path must not move
# ---------------------------------------------------------------------------


@_skip
async def test_an_email_signup_still_becomes_its_local_part(conn):
    """No existing user's name changes: the suffix only appears on a collision."""
    uid = await _signup(conn, email="alice@example.com")

    row = await _profile(conn, uid)
    assert row["username"] == "alice"
    assert row["team_name"] == "alice's Workspace"


@_skip
async def test_an_explicit_username_still_wins(conn):
    uid = await _signup(
        conn, email="bob@example.com", raw_user_meta_data='{"username": "bobby"}'
    )

    assert (await _profile(conn, uid))["username"] == "bobby"


# ---------------------------------------------------------------------------
# ① No email
# ---------------------------------------------------------------------------


@_skip
async def test_an_emailless_signup_gets_a_name_from_its_uuid(conn):
    """Covers both live emailless paths — `GOTRUE_EXTERNAL_PHONE_ENABLED=true`
    and `GOTRUE_EXTERNAL_ANONYMOUS_USERS_ENABLED=true`.

    A phone signup lands here too rather than on its phone number: see the
    migration's comment — a username is shown to other people, and a phone
    number is PII, not a display name.

    (The stub `auth.users` in the drift database has no `phone` column, so a
    phone row cannot be inserted here anyway. That is a second reason the
    function does not read `NEW.phone`: it would depend on a field no test we
    own could supply.)
    """
    uid = await _signup(conn)

    row = await _profile(conn, uid)
    expected = "user_" + uid.hex[:8]
    assert row["username"] == expected
    assert row["team_name"] == f"{expected}'s Workspace"


@_skip
async def test_an_emailless_signup_used_to_kill_the_whole_signup(pre_469):
    """Not a hypothetical: the old function raises, and because the trigger is
    AFTER INSERT the `auth.users` row goes with it — no account at all."""
    with pytest.raises(asyncpg.NotNullViolationError):
        await _signup(pre_469)


@_skip
async def test_an_empty_string_email_counts_as_absent(conn):
    """`''` would sail straight through COALESCE and produce a "'s Workspace"
    team owned by a nameless profile. NULLIF is what stops that."""
    uid = await _signup(conn, email="", raw_user_meta_data='{"username": ""}')

    row = await _profile(conn, uid)
    assert row["username"] == "user_" + uid.hex[:8]
    assert not row["team_name"].startswith("'s ")


# ---------------------------------------------------------------------------
# ② Duplicate local part
# ---------------------------------------------------------------------------


@_skip
async def test_two_emails_sharing_a_local_part_can_both_sign_up(conn):
    first = await _signup(conn, email="sam@one.example")
    second = await _signup(conn, email="sam@two.example")

    assert (await _profile(conn, first))["username"] == "sam"
    # The loser keeps the readable base and gains a uuid-derived suffix, so the
    # name is still recognisably theirs.
    assert (await _profile(conn, second))["username"] == "sam_" + second.hex[:8]


@_skip
async def test_a_duplicate_local_part_used_to_kill_the_second_signup(pre_469):
    await _signup(pre_469, email="dup@one.example")

    with pytest.raises(asyncpg.UniqueViolationError):
        await _signup(pre_469, email="dup@two.example")


# ---------------------------------------------------------------------------
# Whatever the shape, the signup is complete
# ---------------------------------------------------------------------------


@_skip
async def test_every_shape_still_arrives_stocked_with_tags(conn):
    """The reason to care that these signups *complete*: a user who fails here
    has no profile, no workspace and no tags. mig 468's seeding hangs off the
    same function."""
    preset_count = await conn.fetchval("SELECT count(*) FROM tag_presets")
    assert preset_count > 0

    for label, cols in (
        ("email", {"email": "stocked@example.com"}),
        ("emailless", {}),
    ):
        uid = await _signup(conn, **cols)
        owned = await conn.fetchval("SELECT count(*) FROM tags WHERE user_id = $1", uid)
        assert owned == preset_count, f"{label} signup came out with {owned} tags"
