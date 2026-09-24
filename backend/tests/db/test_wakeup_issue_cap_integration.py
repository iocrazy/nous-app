"""FH2 T2, against real Postgres: the per-issue wake-up cap's count.

``count_agent_wakeups_since_human(issue_id)`` counts the ``issue_wakeup``
rows the AGENT armed on an issue after the last message a PERSON wrote on it.
Only the database can settle the parts that decide whether the cap is real:

* the anchor is the issue's session conversation (``issues.ai_session_id``),
  because comments on an agent-assigned issue live in ``messages``, not in
  ``issue_messages`` (prod 2026-09-23: 0 human rows there for agent issues;
  the only ``author_user_id`` rows are wake-up mirrors);
* a user-role message that is NOT a person — a wake-up delivery
  (``body.meta.source``) or the continuation nudge — must not reset the
  count, or every fired wake-up would re-arm a fresh budget;
* with no human message at all the anchor falls back to ``issues.created_at``;
* ``payload->>'issue_id'`` (a JSON number) matches the text form, and a
  user's wake-up / another issue's wake-up is not counted.

Transport: asyncpg on ``INTEGRATION_DATABASE_URL`` for fixtures; the
repository runs through ``app.db.session`` repointed at the same DSN.
"""

from __future__ import annotations

import datetime as dt
import json
import os
import uuid

import pytest

pytestmark = [pytest.mark.integration, pytest.mark.asyncio]

_TEST_DSN = os.environ.get("INTEGRATION_DATABASE_URL", "").strip()
asyncpg = pytest.importorskip("asyncpg")

_skip = pytest.mark.skipif(
    not _TEST_DSN,
    reason="INTEGRATION_DATABASE_URL not set — the wake-up cap needs a DB.",
)

NOW = dt.datetime.now(dt.timezone.utc)


def _ago(**kw) -> dt.datetime:
    return NOW - dt.timedelta(**kw)


@pytest.fixture
async def orm_dsn():
    from app.core.config import settings
    from app.db import engine as engine_mod
    from app.db import session as session_mod

    old = settings.SUPAVISOR_DATABASE_URL
    settings.SUPAVISOR_DATABASE_URL = _TEST_DSN
    await engine_mod.dispose_engine()
    session_mod.dispose_sessionmaker()
    try:
        yield _TEST_DSN
    finally:
        await engine_mod.dispose_engine()
        session_mod.dispose_sessionmaker()
        settings.SUPAVISOR_DATABASE_URL = old


@pytest.fixture
async def pg():
    conn = await asyncpg.connect(_TEST_DSN)
    try:
        yield conn
    finally:
        await conn.close()


@pytest.fixture
async def world(pg):
    """A user, a team and a conversation; torn down in reverse."""
    user_id = uuid.uuid4()
    await pg.execute("INSERT INTO auth.users (id) VALUES ($1)", user_id)
    team_id = await pg.fetchval(
        "INSERT INTO teams (name, owner_id, invite_code) VALUES ($1, $2, $3)"
        " RETURNING id",
        f"fh2-t2-{uuid.uuid4().hex[:8]}",
        user_id,
        uuid.uuid4().hex[:10],
    )
    conv_id = await pg.fetchval(
        "INSERT INTO conversations (type, scope_id, created_by) VALUES"
        " ('ai', $1, $2) RETURNING id",
        team_id,
        user_id,
    )
    made: dict = {"issues": [], "schedules": []}
    try:
        yield {"user": user_id, "conv": conv_id, "made": made}
    finally:
        await pg.execute(
            "DELETE FROM user_schedules WHERE id = ANY($1::uuid[])", made["schedules"]
        )
        await pg.execute(
            "DELETE FROM issues WHERE id = ANY($1::bigint[])", made["issues"]
        )
        await pg.execute("DELETE FROM conversations WHERE id = $1", conv_id)
        await pg.execute("DELETE FROM teams WHERE id = $1", team_id)
        await pg.execute("DELETE FROM auth.users WHERE id = $1", user_id)


async def _issue(pg, world, *, session: bool) -> int:
    iid = await pg.fetchval(
        """INSERT INTO issues (issue_number, identifier, title, status, priority,
                               origin_kind, created_by_user_id, ai_session_id,
                               created_at)
           VALUES ((SELECT COALESCE(MAX(issue_number), 0) + 1 FROM issues),
                   $1, 'Wake-up cap fixture', 'in_progress', 'medium', 'manual',
                   $2, $3, $4)
           RETURNING id""",
        f"FH2T2-{uuid.uuid4().hex[:8]}",
        world["user"],
        world["conv"] if session else None,
        _ago(days=10),
    )
    world["made"]["issues"].append(iid)
    return iid


async def _wakeup(pg, world, *, issue_id: int, created_by: str, at: dt.datetime):
    payload = {"issue_id": issue_id, "text": "check back", "once": True}
    payload["created_by"] = created_by
    sid = await pg.fetchval(
        """INSERT INTO user_schedules (user_id, name, cron_expr, task_type,
                                       payload, next_fire_at, created_at)
           VALUES ($1, 'wake', NULL, 'issue_wakeup', $2::jsonb, $3, $4)
           RETURNING id""",
        world["user"],
        json.dumps(payload),
        NOW + dt.timedelta(hours=1),
        at,
    )
    world["made"]["schedules"].append(sid)


_SEQ = iter(range(1, 10_000))


async def _message(pg, world, *, sender: str, body: dict, at: dt.datetime):
    await pg.execute(
        "INSERT INTO messages (conversation_id, seq, sender_type, sender_id, type,"
        " body, created_at) VALUES ($1, $2, $3, $4, 'text', $5::jsonb, $6)",
        world["conv"],
        next(_SEQ),
        sender,
        world["user"] if sender == "user" else None,
        json.dumps(body),
        at,
    )


@_skip
async def test_the_count_resets_on_a_person_and_only_on_a_person(orm_dsn, pg, world):
    from app.repositories.user_schedules_repository import (
        count_agent_wakeups_since_human,
    )
    from app.services.issues.issue_agent_executor import CONTINUATION_NUDGE

    iid = await _issue(pg, world, session=True)
    other = await _issue(pg, world, session=False)

    # Before anyone spoke: anchored on issues.created_at (10 days ago).
    await _wakeup(pg, world, issue_id=iid, created_by="agent", at=_ago(days=5))
    await _wakeup(pg, world, issue_id=iid, created_by="agent", at=_ago(days=4))
    assert await count_agent_wakeups_since_human(iid) == 2

    # A person comments: the two earlier rows no longer count.
    await _message(
        pg, world, sender="user", body={"text": "any news?"}, at=_ago(days=3)
    )
    await _wakeup(pg, world, issue_id=iid, created_by="agent", at=_ago(days=2))
    assert await count_agent_wakeups_since_human(iid) == 1

    # Three user-role/agent messages that are NOT a person must not reset it.
    await _message(
        pg,
        world,
        sender="user",
        body={"text": "check back", "meta": {"source": {"kind": "schedule"}}},
        at=_ago(days=1),
    )
    await _message(
        pg, world, sender="user", body={"text": CONTINUATION_NUDGE}, at=_ago(hours=20)
    )
    await _message(
        pg, world, sender="agent", body={"text": "working"}, at=_ago(hours=18)
    )
    await _wakeup(pg, world, issue_id=iid, created_by="agent", at=_ago(hours=12))
    # Not counted: a person's wake-up, and another issue's agent wake-up.
    await _wakeup(pg, world, issue_id=iid, created_by="user", at=_ago(hours=11))
    await _wakeup(pg, world, issue_id=other, created_by="agent", at=_ago(hours=11))
    assert await count_agent_wakeups_since_human(iid) == 2

    # System-written user-role messages from the sub-issue barrier and the
    # pipeline relay carry their own provenance keys (no ``source``) — they
    # are not a person either (T2 review H1: they used to reset 2 → 0).
    await _message(
        pg,
        world,
        sender="user",
        body={
            "text": "children done",
            "meta": {"barrier_key": "k", "subissue_barrier": True},
        },
        at=_ago(hours=10),
    )
    await _message(
        pg,
        world,
        sender="user",
        body={
            "text": "stage handed over",
            "meta": {"pipeline_key": "k", "pipeline_relay": True},
        },
        at=_ago(hours=9),
    )
    assert await count_agent_wakeups_since_human(iid) == 2

    # A reassignment note IS a person acting (PR-C): it resets the count.
    await _message(
        pg,
        world,
        sender="user",
        body={
            "text": "reassigned",
            "meta": {"issue_reassigned": {"from": "a", "to": "b"}},
        },
        at=_ago(hours=8),
    )
    assert await count_agent_wakeups_since_human(iid) == 0

    # An issue with no session at all: anchored on its own created_at.
    assert await count_agent_wakeups_since_human(other) == 1
    # An issue that does not exist counts nothing (no anchor → no match).
    assert await count_agent_wakeups_since_human(int(uuid.uuid4().int % 10**15)) == 0
