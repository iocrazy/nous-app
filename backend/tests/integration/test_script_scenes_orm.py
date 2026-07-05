"""Integration round-trip for the ORM-backed scene layer against real PG.

Phase B Task 7. The value of THIS test (over the mocked pins in
``test_scene_convert_dispatch.py``) is the real asyncpg type surface —
JSONB binding (``content_json`` = a Python list of element dicts; ``op_json``
= a dict), bigint id/FK coercion, and the ``content_version`` optimistic guard
under a genuine committing transaction. Mocked tests cannot catch a JSONB bind
mismatch or a silent-rollback write_scope bug; a live round-trip can.

Exercises the full data chain the editor + convert-to-scenes rely on:

    team → project → episode → script(script_projects, episode_id) →
    chapter → scene

    - ``ScriptSceneRepository.create`` (empty scene, content_version 0)
    - ``apply_element_ops``: insert 3 elements → content_version 1, one
      ``script_ops`` row (op_seq 1), derived ``content`` carries element text
    - stale + wrong ``expected_version`` → ``VersionConflict`` carrying the
      current version; NO duplicate elements applied
    - ``move_scene``: reorder a scene past its sibling, read the new order back
    - ``create_with_content`` (Task 6): a fully-formed scene + genesis op ledger
      row (op_seq 1) in one transaction

Setup: requires INTEGRATION_DATABASE_URL (plain ``postgresql://`` DSN — the
engine coerces it to ``postgresql+asyncpg://`` itself). Skips cleanly otherwise:

    source .../scratchpad/dev_db.env
    export INTEGRATION_DATABASE_URL="$DEV_DSN"
    uv run pytest tests/integration/test_script_scenes_orm.py -v
"""

from __future__ import annotations

import os
import uuid

import asyncpg
import pytest

pytestmark = [pytest.mark.integration, pytest.mark.asyncio]

_TEST_DSN = os.environ.get("INTEGRATION_DATABASE_URL", "").strip()


@pytest.fixture(scope="module")
def integration_db_url() -> str:
    if not _TEST_DSN:
        pytest.skip("INTEGRATION_DATABASE_URL not set — skipping integration tests")
    return _TEST_DSN


@pytest.fixture
async def patched_engine(integration_db_url):
    """Point the process-wide SQLAlchemy engine at the integration DB, exactly
    as test_projects_repository_orm.py does."""
    from unittest.mock import patch

    from app.db import engine as db_engine
    from app.db import session as db_session

    db_engine._engine = None
    db_session.dispose_sessionmaker()
    with patch.object(db_engine.settings, "SUPAVISOR_DATABASE_URL", integration_db_url):
        yield
    await db_engine.dispose_engine()
    db_engine._engine = None
    db_session.dispose_sessionmaker()


def _repo():
    from app.repositories.script_scene_repository import ScriptSceneRepository

    return ScriptSceneRepository()


async def _real_user_id(conn):
    uid = await conn.fetchval("SELECT id FROM auth.users LIMIT 1")
    if not uid:
        pytest.skip("No auth.users rows to satisfy owner_id / created_by")
    return uid


async def _seed_chain(conn, owner_id) -> dict:
    """Seed team → project → episode → script → chapter via raw asyncpg,
    returning all created ids. Uses server-default snowflake ids (no explicit
    id supplied)."""
    team = await conn.fetchrow(
        "INSERT INTO teams (name, owner_id, invite_code, kind) "
        "VALUES ($1, $2, $3, 'collaborative') RETURNING id",
        f"__test_scenes_team_{uuid.uuid4().hex[:6]}",
        owner_id,
        uuid.uuid4().hex[:12],
    )
    project = await conn.fetchrow(
        "INSERT INTO projects (name, owner_id, team_id) "
        "VALUES ($1, $2, $3) RETURNING id",
        f"__test_scenes_proj_{uuid.uuid4().hex[:6]}",
        owner_id,
        team["id"],
    )
    episode = await conn.fetchrow(
        "INSERT INTO episodes (project_id, title, sort_order) "
        "VALUES ($1, 'Ep 1', 0) RETURNING id",
        project["id"],
    )
    script = await conn.fetchrow(
        "INSERT INTO script_projects "
        "(project_id, team_id, name, created_by, episode_id) "
        "VALUES ($1, $2, $3, $4, $5) RETURNING id",
        project["id"],
        team["id"],
        f"__test_scenes_script_{uuid.uuid4().hex[:6]}",
        owner_id,
        episode["id"],
    )
    chapter = await conn.fetchrow(
        "INSERT INTO script_chapters (script_id, title, chapter_number) "
        "VALUES ($1, 'Chapter 1', 1) RETURNING id",
        script["id"],
    )
    return {
        "team_id": team["id"],
        "project_id": project["id"],
        "episode_id": episode["id"],
        "script_id": script["id"],
        "chapter_id": chapter["id"],
    }


async def _cleanup_chain(conn, ids) -> None:
    """Tear down in FK order: script_projects CASCADEs chapters/scenes/ops;
    projects CASCADEs episodes; teams last (projects.team_id is SET NULL, but
    the script row still references the team, so scripts must go first)."""
    await conn.execute("DELETE FROM script_projects WHERE id = $1", ids["script_id"])
    await conn.execute("DELETE FROM episodes WHERE id = $1", ids["episode_id"])
    await conn.execute("DELETE FROM projects WHERE id = $1", ids["project_id"])
    await conn.execute("DELETE FROM teams WHERE id = $1", ids["team_id"])


async def test_scene_ops_live_round_trip(integration_db_url, patched_engine):
    from app.repositories.script_scene_repository import VersionConflict

    conn = await asyncpg.connect(integration_db_url)
    try:
        owner_id = await _real_user_id(conn)
        ids = await _seed_chain(conn, owner_id)
    finally:
        await conn.close()

    repo = _repo()
    script_id = str(ids["script_id"])
    chapter_id = str(ids["chapter_id"])

    try:
        # ── create empty scene (content_version defaults 0) ─────────────
        scene = await repo.create(
            {
                "script_id": script_id,
                "chapter_id": chapter_id,
                "heading_int_ext": "INT",
                "location_text": "War Room",
                "time_of_day": "NIGHT",
            }
        )
        scene_id = str(scene["id"])
        assert type(scene["id"]) is int  # bigint stays native int (5.3 trap)
        assert type(scene["script_id"]) is int
        assert scene["content_version"] == 0
        assert scene["content_json"] == []  # JSONB default, native list

        # ── apply_element_ops: insert 3 elements ────────────────────────
        ops = [
            {
                "op": "insert",
                "element_id": "el_aaaa0001",
                "payload": {"type": "action", "text": "Rain hammers the glass."},
            },
            {
                "op": "insert",
                "element_id": "el_bbbb0002",
                "payload": {"type": "character", "text": "MARA"},
            },
            {
                "op": "insert",
                "element_id": "el_cccc0003",
                "payload": {"type": "dialogue", "text": "We move at dawn."},
            },
        ]
        applied = await repo.apply_element_ops(scene_id, ops, 0, actor="user")
        assert applied["content_version"] == 1
        assert [e["id"] for e in applied["elements"]] == [
            "el_aaaa0001",
            "el_bbbb0002",
            "el_cccc0003",
        ]

        # Fresh read: JSONB persisted as a native list; derived content has text.
        stored = await repo.get_by_id(scene_id)
        assert stored["content_version"] == 1
        assert isinstance(stored["content_json"], list)
        assert len(stored["content_json"]) == 3
        assert "Rain hammers the glass." in stored["content"]
        assert "We move at dawn." in stored["content"]
        assert "MARA:" in stored["content"]  # character element gets a colon

        # ── stale expected_version replay → VersionConflict, no dup apply ─
        with pytest.raises(VersionConflict) as stale_exc:
            await repo.apply_element_ops(scene_id, ops, 0, actor="user")
        assert stale_exc.value.current_version == 1
        # No duplicate elements landed — still exactly 3.
        after_stale = await repo.get_by_id(scene_id)
        assert len(after_stale["content_json"]) == 3
        assert after_stale["content_version"] == 1

        # ── wrong (too-high) expected_version → VersionConflict w/ current ─
        with pytest.raises(VersionConflict) as wrong_exc:
            await repo.apply_element_ops(
                scene_id,
                [
                    {
                        "op": "insert",
                        "element_id": "el_dddd0004",
                        "payload": {"type": "action", "text": "A pause."},
                    }
                ],
                99,
                actor="user",
            )
        assert wrong_exc.value.current_version == 1
        assert len((await repo.get_by_id(scene_id))["content_json"]) == 3

        # ── ledger check: one op row so far, op_seq = 1 = content_version ──
        conn = await asyncpg.connect(integration_db_url)
        try:
            op_rows = await conn.fetch(
                "SELECT op_seq, actor, op_json FROM script_ops "
                "WHERE scene_id = $1 ORDER BY op_seq",
                int(scene_id),
            )
        finally:
            await conn.close()
        assert [r["op_seq"] for r in op_rows] == [1]
        assert op_rows[0]["actor"] == "user"

        # ── move_scene: reorder past a sibling, read the new order back ───
        scene_b = await repo.create(
            {
                "script_id": script_id,
                "chapter_id": chapter_id,
                "heading_int_ext": "EXT",
                "location_text": "Rooftop",
            }
        )
        scene_b_id = str(scene_b["id"])
        # Move the first scene AFTER scene_b.
        await repo.move_scene(scene_id, after_scene_id=scene_b_id)
        ordered = await repo.list_by_script(script_id)
        order_ids = [
            str(s["id"]) for s in ordered if str(s["id"]) in {scene_id, scene_b_id}
        ]
        assert order_ids == [scene_b_id, scene_id]  # A now trails B

        # ── create_with_content (Task 6): scene + genesis ledger, 1 txn ───
        elements = [
            {"id": "el_eeee0005", "type": "action", "text": "Dawn breaks grey."},
            {"id": "el_ffff0006", "type": "character", "text": "JONAH"},
            {"id": "el_99990007", "type": "dialogue", "text": "It's time."},
        ]
        cwc = await repo.create_with_content(
            {
                "script_id": script_id,
                "chapter_id": chapter_id,
                "heading_int_ext": "EXT",
                "location_text": "Ridge",
                "time_of_day": "DAWN",
            },
            elements,
            actor="copilot",
        )
        cwc_id = str(cwc["id"])
        assert cwc["content_version"] == 1
        assert len(cwc["content_json"]) == 3
        assert "Dawn breaks grey." in cwc["content"]
        assert "It's time." in cwc["content"]
        assert "JONAH:" in cwc["content"]

        conn = await asyncpg.connect(integration_db_url)
        try:
            genesis = await conn.fetch(
                "SELECT op_seq, actor, op_json FROM script_ops "
                "WHERE scene_id = $1 ORDER BY op_seq",
                int(cwc_id),
            )
        finally:
            await conn.close()
        assert [r["op_seq"] for r in genesis] == [1]
        assert genesis[0]["actor"] == "copilot"
        # op_json genesis = 3 inserts + 3 inverse deletes (JSONB round-trip).
        import json as _json

        op_json = genesis[0]["op_json"]
        if isinstance(op_json, str):  # asyncpg returns jsonb as str unless codec set
            op_json = _json.loads(op_json)
        assert len(op_json["ops"]) == 3
        assert len(op_json["inverse"]) == 3
        assert {o["op"] for o in op_json["ops"]} == {"insert"}
        assert {o["op"] for o in op_json["inverse"]} == {"delete"}
    finally:
        conn = await asyncpg.connect(integration_db_url)
        try:
            await _cleanup_chain(conn, ids)
        finally:
            await conn.close()
