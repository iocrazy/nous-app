"""DB-backed integration tests for ``services.deliverables.revert`` (3b Task 3).

WHY THIS FILE EXISTS
────────────────────
``revert_output`` is the first writer in the deliverables domain that changes
USER CONTENT, and every unit test around it stubs the six外部面 — the session,
the ledger readers, the registration口. So without this file not one of its ORM
statements had ever been executed by Postgres, while the endpoint writes through
them on every call. Same shape of gap as
``tests/db/test_run_deliverables_repository_integration.py`` opens with. What
only a server can answer:

  * ``select(...).with_for_update()`` over a column LIST (not an entity) — the
    row lock the shot arm relies on, since nothing else serialises a revert
    against a concurrent edit.
  * ``insert(ScriptShotOps).values(run_id=None, actor=...).returning(id)`` must
    pass ``script_shot_ops_run_or_actor`` — a CHECK that exists precisely because
    mig 466 dropped the NOT NULL on ``run_id``. A stubbed session accepts any
    values; Postgres is the only thing that enforces the CHECK, and the human
    ledger row is the ONLY row in the system shaped this way.
  * ``script_shot_ops_action_check`` limits ``action`` to create/update — the
    revert row says ``'update'`` and nothing else in the codebase proves that is
    an accepted value.
  * the registration row likewise has to pass ``run_deliverables_run_or_actor``
    with ``run_id NULL``.
  * **the three writes are ONE transaction.** ``register_deliverable(session=…)``
    plus ``write_scope()``-joins-the-ambient-UoW is the whole atomicity story, and
    a stubbed session cannot roll anything back. The rollback case below is the
    only proof that a failed registration takes the content change with it —
    i.e. that a revert can never leave "内容回了、版本没记".
  * **the 409-on-race depends on SQLSTATE 23505 arriving as an
    ``IntegrityError`` whose ``orig`` carries ``sqlstate``.** The unit test
    fabricates that shape; only asyncpg + the real
    ``run_deliverables_kind_ref_version_key`` prove the bet.
  * the scene arm goes through ``apply_element_ops``, whose own transaction
    must JOIN ours (``write_scope()`` → ambient ``unit_of_work()``), and whose
    optimistic ``content_version`` guard must accept ``max(op_seq)`` as the
    expected version.

WHAT IS STUBBED, AND WHY
────────────────────────
Only the two authorization surfaces: ``visible_chain`` (fed the REAL chain from
``RunDeliverablesRepository.lineage_for``, just without the issue-visibility
gate) and ``verify_shot_access`` / ``verify_scene_access``. Building a
team-membership + issue-visibility fixture here would test ``outputs_router``
and ``scope_guards`` — both of which have their own suites — while adding
nothing to the question this file asks, which is whether Postgres accepts what
the revert writes. Everything below the guards is real.

Point it at any CI-way Postgres (ci_bootstrap.sql → schema_baseline.sql →
migrations above the watermark, which includes 453/462/466):

  INTEGRATION_DATABASE_URL=postgresql://postgres:postgres@127.0.0.1:5432/drift \
    uv run pytest tests/db/test_revert_output_integration.py -v

Skips cleanly when INTEGRATION_DATABASE_URL is unset. Every test builds its own
project → script → scene → shot chain with fresh ids and tears it down in a
``finally``, so the cases are independent and the file is re-runnable.
"""

from __future__ import annotations

import os
import uuid
from types import SimpleNamespace
from typing import Any, Dict

import asyncpg
import pytest

pytestmark = [pytest.mark.integration, pytest.mark.asyncio]

_TEST_DSN = os.environ.get("INTEGRATION_DATABASE_URL", "").strip()

pytest.importorskip("asyncpg")

_skip = pytest.mark.skipif(
    not _TEST_DSN,
    reason="INTEGRATION_DATABASE_URL not set — revert integration tests need a DB.",
)

#: v1 的六个字段（create 那行账本带齐每个可写字段）。
V1 = {
    "shot_type": "WS",
    "camera_angle": "eye_level",
    "camera_movement": "static",
    "focal_length": "35mm",
    "lighting": "golden hour",
    "description": "A wide shot of the cafe at dusk.",
}
#: v2 只改了一个字段（update 那行账本只带它改过的）。
V2_DELTA = {"description": "A close-up of the cup."}


@pytest.fixture
async def orm_dsn():
    """Repoint the ORM engine at the test DSN, then restore + dispose."""
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
async def fx(pg) -> Dict[str, Any]:
    """auth.users → team + project → script → scene → shot, plus an agent run.

    Every FK above is REAL and had to be satisfied: ``script_projects`` needs a
    project, a team AND an ``auth.users`` creator; ``script_scenes.script_id``
    and ``script_shots.scene_id`` chain down from there; ``script_shot_ops``
    references both the shot and (when an agent wrote it) the run. That chain is
    the file earning its keep before it asserts anything — the unit tests, whose
    session is stubbed, are structurally unable to notice a single one.
    """
    user_id = await pg.fetchval("INSERT INTO auth.users DEFAULT VALUES RETURNING id")
    team_id = await pg.fetchval(
        "INSERT INTO public.teams (name, owner_id, invite_code) "
        "VALUES ($1, $2, $3) RETURNING id",
        "Revert Test Team",
        user_id,
        uuid.uuid4().hex[:12],
    )
    project_id = await pg.fetchval(
        "INSERT INTO public.projects (name, owner_id, team_id) "
        "VALUES ($1, $2, $3) RETURNING id",
        "Revert Test Project",
        user_id,
        team_id,
    )
    script_id = await pg.fetchval(
        "INSERT INTO public.script_projects (project_id, team_id, name, created_by) "
        "VALUES ($1, $2, $3, $4) RETURNING id",
        project_id,
        team_id,
        "Revert Test Script",
        user_id,
    )
    scene_id = await pg.fetchval(
        "INSERT INTO public.script_scenes (script_id, sort_order) "
        "VALUES ($1, 1) RETURNING id",
        script_id,
    )
    shot_id = await pg.fetchval(
        "INSERT INTO public.script_shots (scene_id, sort_order) "
        "VALUES ($1, 1) RETURNING id",
        scene_id,
    )
    agent_id = await pg.fetchval(
        "INSERT INTO public.ai_agents (name) VALUES ($1) RETURNING id",
        f"revert-test-agent-{uuid.uuid4().hex[:8]}",
    )
    run_id = await pg.fetchval(
        "INSERT INTO public.agent_runs (agent_id, user_id, status, trigger) "
        "VALUES ($1, $2, 'completed', 'test') RETURNING id",
        agent_id,
        user_id,
    )
    try:
        yield {
            "user_id": str(user_id),
            "team_id": team_id,
            "project_id": project_id,
            "script_id": script_id,
            "scene_id": scene_id,
            "shot_id": shot_id,
            "run_id": run_id,
        }
    finally:
        await pg.execute(
            "DELETE FROM public.run_deliverables WHERE ref_id = ANY($1::text[])",
            [str(shot_id), str(scene_id)],
        )
        await pg.execute("DELETE FROM public.agent_runs WHERE id = $1", run_id)
        await pg.execute("DELETE FROM public.ai_agents WHERE id = $1", agent_id)
        # script_projects → scenes → shots → shot_ops all cascade from the script.
        await pg.execute("DELETE FROM public.script_projects WHERE id = $1", script_id)
        await pg.execute("DELETE FROM public.projects WHERE id = $1", project_id)
        await pg.execute("DELETE FROM public.teams WHERE id = $1", team_id)
        await pg.execute("DELETE FROM auth.users WHERE id = $1", user_id)


@pytest.fixture
def unguarded(monkeypatch):
    """Swap ONLY the two authorization surfaces (see the module docstring).

    ``visible_chain`` still returns the REAL chain, read back through
    ``RunDeliverablesRepository.lineage_for`` — the rows the arms consume are
    Postgres's, not a hand-written fixture's.
    """
    from app.repositories.run_deliverables_repository import (
        get_run_deliverables_repository,
    )
    from app.services.deliverables import revert as mod

    async def _chain(kind, ref_id, auth):
        rows = await get_run_deliverables_repository().lineage_for(
            kind=kind, ref_id=str(ref_id)
        )
        if not rows:
            raise AssertionError(f"{kind}/{ref_id} is not registered — fixture bug")
        return rows

    async def _allow(*a, **k):
        return None

    monkeypatch.setattr(mod, "visible_chain", _chain)
    monkeypatch.setattr(mod, "verify_shot_access", _allow)
    monkeypatch.setattr(mod, "verify_scene_access", _allow)
    return mod


async def _seed_shot_chain(pg, fx) -> Dict[str, Any]:
    """The state a revert starts from: a shot at v2, two ops rows, two
    registered versions whose ``ledger_ref`` points at those rows."""
    shot_id, run_id = fx["shot_id"], fx["run_id"]
    await pg.execute(
        "UPDATE public.script_shots SET shot_type=$2, camera_angle=$3, "
        "camera_movement=$4, focal_length=$5, lighting=$6, description=$7 "
        "WHERE id=$1",
        shot_id,
        *[
            V1[f]
            for f in (
                "shot_type",
                "camera_angle",
                "camera_movement",
                "focal_length",
                "lighting",
                "description",
            )
        ],
    )
    op1 = await pg.fetchval(
        "INSERT INTO public.script_shot_ops "
        "(run_id, shot_id, scene_id, action, after_json) "
        "VALUES ($1, $2, $3, 'create', $4::jsonb) RETURNING id",
        run_id,
        shot_id,
        fx["scene_id"],
        __import__("json").dumps(V1),
    )
    await pg.execute(
        "UPDATE public.script_shots SET description=$2 WHERE id=$1",
        shot_id,
        V2_DELTA["description"],
    )
    op2 = await pg.fetchval(
        "INSERT INTO public.script_shot_ops "
        "(run_id, shot_id, scene_id, action, after_json) "
        "VALUES ($1, $2, $3, 'update', $4::jsonb) RETURNING id",
        run_id,
        shot_id,
        fx["scene_id"],
        __import__("json").dumps(V2_DELTA),
    )
    for version, ledger_ref in ((1, op1), (2, op2)):
        await pg.execute(
            "INSERT INTO public.run_deliverables "
            "(run_id, kind, ref_id, version, parent_version, title, ledger_ref) "
            "VALUES ($1, 'script_shot', $2, $3, $4, $5, $6)",
            run_id,
            str(shot_id),
            version,
            version - 1 or None,
            f"S1 · Shot 1 · v{version}",
            str(ledger_ref),
        )
    return {"op1": op1, "op2": op2}


async def _shot_fields(pg, shot_id) -> Dict[str, Any]:
    row = await pg.fetchrow(
        "SELECT shot_type, camera_angle, camera_movement, focal_length, "
        "lighting, description FROM public.script_shots WHERE id = $1",
        shot_id,
    )
    return dict(row)


def _auth(fx):
    return SimpleNamespace(user_id=fx["user_id"])


# ---------------------------------------------------------------------------
# 分镜臂 — 六字段 UPDATE + 人手账本行 + 人手登记行，一个事务
# ---------------------------------------------------------------------------


@_skip
async def test_a_shot_revert_writes_content_ledger_and_version_in_one_go(
    orm_dsn, fx, pg, unguarded
):
    await _seed_shot_chain(pg, fx)
    shot_id = fx["shot_id"]

    out = await unguarded.revert_output(
        kind="script_shot",
        ref_id=str(shot_id),
        to_version=1,
        expected_latest=2,
        auth=_auth(fx),
    )

    # ① 内容真的回到了 v1 —— 包括 create 之后没人再提过的那四个字段。
    assert await _shot_fields(pg, shot_id) == V1

    # ② 账本多了一行人手行：run_id NULL + actor，正是 mig 466 的
    #    script_shot_ops_run_or_actor 放行的那个形状（也是系统里唯一这个形状的行）。
    ops = await pg.fetch(
        "SELECT run_id, actor, action, before_json, after_json FROM "
        "public.script_shot_ops WHERE shot_id = $1 ORDER BY id",
        shot_id,
    )
    assert len(ops) == 3
    human = ops[-1]
    assert human["run_id"] is None
    assert human["actor"] == f"revert:{fx['user_id']}"
    assert human["action"] == "update"  # script_shot_ops_action_check 只认两个值

    # ③ 登记行：没有 run，由人署名（run_deliverables_run_or_actor），并且
    #    ledger_ref 指向**刚写的**那行账本，不是目标版的。
    assert out.kept_version is None
    version = out.version
    assert version["version"] == 3 and version["reverted_from_version"] == 1
    assert version["run_id"] is None
    assert version["actor_user_id"] == fx["user_id"]

    stored = await pg.fetchrow(
        "SELECT run_id, actor_user_id, reverted_from_version, ledger_ref "
        "FROM public.run_deliverables WHERE ref_id = $1 AND version = 3",
        str(shot_id),
    )
    assert stored["run_id"] is None
    assert str(stored["actor_user_id"]) == fx["user_id"]
    assert stored["reverted_from_version"] == 1
    new_op_id = await pg.fetchval(
        "SELECT id FROM public.script_shot_ops WHERE shot_id = $1 "
        "ORDER BY id DESC LIMIT 1",
        shot_id,
    )
    assert stored["ledger_ref"] == str(new_op_id)

    # ④ 回退本身还能再被回退：v3 现在就是链上的一版。
    chain = await pg.fetch(
        "SELECT version FROM public.run_deliverables WHERE ref_id = $1 "
        "ORDER BY version",
        str(shot_id),
    )
    assert [r["version"] for r in chain] == [1, 2, 3]


@_skip
async def test_an_unregistered_hand_edit_is_kept_as_its_own_version(
    orm_dsn, fx, pg, unguarded
):
    """``PATCH /shots/{id}`` 改内容但不写 ops 行、不占版本号。回退前必须先把那个
    状态登记成一版并补一行账本，否则用户手打的那段被冲掉且再也指认不出来。"""
    await _seed_shot_chain(pg, fx)
    shot_id = fx["shot_id"]
    await pg.execute(
        "UPDATE public.script_shots SET description = $2 WHERE id = $1",
        shot_id,
        "hand-typed, never registered",
    )

    out = await unguarded.revert_output(
        kind="script_shot",
        ref_id=str(shot_id),
        to_version=1,
        expected_latest=2,
        auth=_auth(fx),
    )

    assert await _shot_fields(pg, shot_id) == V1
    assert out.kept_version is not None
    assert out.kept_version["version"] == 3
    assert out.kept_version["reverted_from_version"] is None
    assert out.version["version"] == 4
    assert out.version["reverted_from_version"] == 1

    # 两行账本 + 两行登记，都在同一个事务里落下（keep 在前，revert 在后）。
    ops = await pg.fetch(
        "SELECT actor, after_json FROM public.script_shot_ops "
        "WHERE shot_id = $1 AND run_id IS NULL ORDER BY id",
        shot_id,
    )
    assert [o["actor"] for o in ops] == [
        f"keep:{fx['user_id']}",
        f"revert:{fx['user_id']}",
    ]
    # 保留版那行账本要能重建出「人手打的那个状态」，否则登记它没有意义。
    import json

    assert json.loads(ops[0]["after_json"])["description"] == (
        "hand-typed, never registered"
    )

    rows = await pg.fetch(
        "SELECT version, ledger_ref, reverted_from_version FROM "
        "public.run_deliverables WHERE ref_id = $1 AND run_id IS NULL "
        "ORDER BY version",
        str(shot_id),
    )
    assert [r["version"] for r in rows] == [3, 4]
    op_ids = await pg.fetch(
        "SELECT id FROM public.script_shot_ops WHERE shot_id = $1 "
        "AND run_id IS NULL ORDER BY id",
        shot_id,
    )
    assert [r["ledger_ref"] for r in rows] == [str(o["id"]) for o in op_ids]


@_skip
async def test_a_failed_registration_rolls_the_content_back(
    orm_dsn, fx, pg, unguarded, monkeypatch
):
    """整个文件最重要的一条。登记落在事务外就会留下「内容回了、版本没记」——
    血缘要回答的恰好是那个问题。桩 session 回滚不了任何东西，只有真库能证明
    ``register_deliverable(session=…)`` 真的把那三次写绑在一起。"""
    await _seed_shot_chain(pg, fx)
    shot_id = fx["shot_id"]
    before_fields = await _shot_fields(pg, shot_id)
    before_ops = await pg.fetchval(
        "SELECT count(*) FROM public.script_shot_ops WHERE shot_id = $1", shot_id
    )

    async def _explode(**kw):
        raise RuntimeError("registration blew up after the content was written")

    monkeypatch.setattr(unguarded, "register_deliverable", _explode)

    with pytest.raises(RuntimeError):
        await unguarded.revert_output(
            kind="script_shot",
            ref_id=str(shot_id),
            to_version=1,
            expected_latest=2,
            auth=_auth(fx),
        )

    # 内容没动、账本没多、版本没多 —— 一次失败的回退什么都没留下。
    assert await _shot_fields(pg, shot_id) == before_fields
    assert (
        await pg.fetchval(
            "SELECT count(*) FROM public.script_shot_ops WHERE shot_id = $1", shot_id
        )
        == before_ops
    )
    assert (
        await pg.fetchval(
            "SELECT count(*) FROM public.run_deliverables WHERE ref_id = $1",
            str(shot_id),
        )
        == 2
    )


@_skip
async def test_losing_the_version_race_is_a_typed_409_on_a_real_index(
    orm_dsn, fx, pg, unguarded
):
    """并发回退的仲裁者是 ``run_deliverables_kind_ref_version_key``。单测伪造了
    ``IntegrityError.orig.sqlstate``；只有 asyncpg + 真索引能证明 23505 确实以那个
    形状到达 ``_is_unique_violation``，而不是变成一个没类型的 500。"""
    from fastapi import HTTPException

    await _seed_shot_chain(pg, fx)
    shot_id = fx["shot_id"]
    before_fields = await _shot_fields(pg, shot_id)
    # 「对方」在我们进事务之后抢先落了 v3 —— 事务内那次 latest 复查不加锁，
    # 所以这里模拟的是它刚好读完之后发生的那一下。
    await pg.execute(
        "INSERT INTO public.run_deliverables "
        "(run_id, kind, ref_id, version, parent_version, title) "
        "VALUES ($1, 'script_shot', $2, 3, 2, 'someone else')",
        fx["run_id"],
        str(shot_id),
    )

    original = unguarded._latest_version

    async def _stale_inside_the_transaction(*, kind, ref_id, session):
        # 事务内仍报 2（放行），事务外重读拿真相 —— 竞态窗口的忠实复现。
        if session is not None:
            return 2
        return await original(kind=kind, ref_id=ref_id, session=session)

    unguarded._latest_version = _stale_inside_the_transaction
    try:
        with pytest.raises(HTTPException) as err:
            await unguarded.revert_output(
                kind="script_shot",
                ref_id=str(shot_id),
                to_version=1,
                expected_latest=2,
                auth=_auth(fx),
            )
    finally:
        unguarded._latest_version = original

    assert err.value.status_code == 409
    assert err.value.detail["code"] == "version_conflict"
    assert err.value.detail["latest_version"] == 3
    # 输家的内容改动随事务一起退回 —— 重试一次就该成功。
    assert await _shot_fields(pg, shot_id) == before_fields
    assert (
        await pg.fetchval(
            "SELECT count(*) FROM public.script_shot_ops WHERE shot_id = $1 "
            "AND run_id IS NULL",
            shot_id,
        )
        == 0
    )


# ---------------------------------------------------------------------------
# 场次臂 — 逆操作批经 apply_element_ops，必须与我们同一个事务
# ---------------------------------------------------------------------------


async def _seed_scene_chain(pg, fx, *, extra_edit: bool) -> None:
    """用真 ``apply_element_ops`` 造场次账本（手写 inverse 等于把被测的重放路径
    换成另一套说法）。``extra_edit`` 决定最后一笔在不在最新登记版的水位之后。"""
    from app.repositories.script_scene_repository import get_script_scene_repository

    repo = get_script_scene_repository()
    scene_id, run_id = fx["scene_id"], fx["run_id"]
    seqs = []
    for index, (eid, text, after) in enumerate(
        [("el_1", "He enters.", None), ("el_2", "Hello.", "el_1")]
    ):
        result = await repo.apply_element_ops(
            str(scene_id),
            [
                {
                    "op": "insert",
                    "element_id": eid,
                    "after_id": after,
                    "payload": {"type": "action", "text": text},
                }
            ],
            expected_version=index,
            actor=f"agent:{run_id}",
        )
        seqs.append(result["content_version"])

    for version, seq in enumerate(seqs, start=1):
        await pg.execute(
            "INSERT INTO public.run_deliverables "
            "(run_id, kind, ref_id, version, parent_version, title, ledger_ref) "
            "VALUES ($1, 'script_scene', $2, $3, $4, $5, $6)",
            run_id,
            str(scene_id),
            version,
            version - 1 or None,
            f"Scene v{version}",
            str(seq),
        )

    if extra_edit:
        # 编辑器里手写的一笔：写了 script_ops，但不占版本号。
        await repo.apply_element_ops(
            str(scene_id),
            [
                {
                    "op": "insert",
                    "element_id": "el_3",
                    "after_id": "el_2",
                    "payload": {"type": "action", "text": "She leaves."},
                }
            ],
            expected_version=seqs[-1],
            actor=f"user:{fx['user_id']}",
        )


async def _scene_texts(pg, scene_id):
    content = await pg.fetchval(
        "SELECT content_json FROM public.script_scenes WHERE id = $1", scene_id
    )
    import json

    return [e.get("text") for e in json.loads(content)]


@_skip
async def test_a_scene_revert_replays_the_inverse_batch_in_our_transaction(
    orm_dsn, fx, pg, unguarded
):
    """``apply_element_ops`` opens its own ``write_scope()``, which JOINs the
    ambient ``unit_of_work()`` — that join is the scene arm's entire atomicity
    story and nothing but a real session proves it."""
    await _seed_scene_chain(pg, fx, extra_edit=False)
    scene_id = fx["scene_id"]
    assert await _scene_texts(pg, scene_id) == ["He enters.", "Hello."]

    out = await unguarded.revert_output(
        kind="script_scene",
        ref_id=str(scene_id),
        to_version=1,
        expected_latest=2,
        auth=_auth(fx),
    )

    assert await _scene_texts(pg, scene_id) == ["He enters."]
    assert out.kept_version is None
    assert out.version["version"] == 3 and out.version["reverted_from_version"] == 1

    # 逆操作批是一条新的**前进** op（历史只增不减），署名是人。
    newest = await pg.fetchrow(
        "SELECT op_seq, actor FROM public.script_ops WHERE scene_id = $1 "
        "ORDER BY op_seq DESC LIMIT 1",
        scene_id,
    )
    assert newest["actor"] == f"revert:{fx['user_id']}"
    stored = await pg.fetchrow(
        "SELECT run_id, actor_user_id, ledger_ref FROM public.run_deliverables "
        "WHERE ref_id = $1 AND version = 3",
        str(scene_id),
    )
    assert stored["run_id"] is None
    assert str(stored["actor_user_id"]) == fx["user_id"]
    # 新版的水位就是刚落下的那条 op。
    assert stored["ledger_ref"] == str(newest["op_seq"])


@_skip
async def test_a_scene_edit_past_the_watermark_is_kept_before_the_revert(
    orm_dsn, fx, pg, unguarded
):
    """场次的每次编辑都写 ``script_ops``，但**登记只开给 agent 产出与回退**，所以
    「当前内容 ≠ 最新登记版内容」在这条臂上一样会发生，而且正是用户刚写的那段。"""
    await _seed_scene_chain(pg, fx, extra_edit=True)
    scene_id = fx["scene_id"]
    assert await _scene_texts(pg, scene_id) == [
        "He enters.",
        "Hello.",
        "She leaves.",
    ]
    watermark = await pg.fetchval(
        "SELECT max(op_seq) FROM public.script_ops WHERE scene_id = $1", scene_id
    )

    out = await unguarded.revert_output(
        kind="script_scene",
        ref_id=str(scene_id),
        to_version=1,
        expected_latest=2,
        auth=_auth(fx),
    )

    assert out.kept_version is not None
    assert out.kept_version["version"] == 3
    assert out.kept_version["reverted_from_version"] is None
    assert out.version["version"] == 4 and out.version["reverted_from_version"] == 1
    # 保留版**不写新账本** —— 场次的账本本来就是全的，它只需要记下自己的水位。
    kept = await pg.fetchrow(
        "SELECT ledger_ref FROM public.run_deliverables WHERE ref_id = $1 "
        "AND version = 3",
        str(scene_id),
    )
    assert kept["ledger_ref"] == str(watermark)
    # 而那个水位真的重建得出被保留的内容 —— 否则登记它没有意义。
    from app.services.deliverables.diff import rebuild_content

    content, reason = await rebuild_content(
        "script_scene", str(scene_id), {"version": 3, "ledger_ref": str(watermark)}
    )
    assert reason is None
    assert [e.get("text") for e in content] == [
        "He enters.",
        "Hello.",
        "She leaves.",
    ]
    assert await _scene_texts(pg, scene_id) == ["He enters."]
