"""正文回填在真 Postgres 上跑一遍（3c 补充票）。

WHY THIS FILE EXISTS
────────────────────
隔壁 ``tests/services/search/test_backfill_body.py`` 把 ``rebuild_content`` 换成了
替身 —— 它证明的是「回填算出来的串和实时写方的渲染器一致」，而**不是**「这条链
在服务器上真的能跑通」。只有真库能回答的那几件：

* **两个候选集查询能不能执行。** 产出那条按 ``kind`` / ``ref_id`` / ``version``
  三列连回 ``run_deliverables``，而 ``search_docs.version`` 是 ``INTEGER``、
  ``run_deliverables.version`` 也是 —— 但 ``ref_id`` 两侧都是 ``TEXT``、
  ``search_docs.run_id`` 是 ``BIGINT``。一个类型配错的连接在 ORM 里编译得过，在
  服务器上是 ``operator does not exist``。这两条语句在别处一次都没被执行过。
* **``fill_empty_body`` 的 WHERE 谓词真的只命中空行。** 「永不覆盖」这条承诺整个
  挂在 ``body IS NULL OR body = ''`` 上；一个桩 session 对任何谓词都点头。
* **``updated_at`` 真的没被动。** 回填不碰那一列，而
  ``idx_search_docs_team_updated`` 是 ``(team_id, updated_at DESC)`` —— 动了它，
  几个月前的产出就会排到「最近」的最前面。只有服务器能证明 UPDATE 之后那个
  timestamptz 一字不差。
* **整条重建链要穿过真账本。** ``rebuild_content`` 读的是
  ``script_shot_ops.after_json``（jsonb）与 ``script_shots`` 当前行，三档解析全在
  服务器那边。替身版跳过了全部。

WHAT IS STUBBED
───────────────
什么都没有。夹具建真的 ``auth.users`` → team → project → script → scene → shot →
run 链（每一个外键都是真的要满足的），回填走 ``backfill_search_docs_bodies()``
这个真入口。

    INTEGRATION_DATABASE_URL=postgresql://postgres:postgres@127.0.0.1:5432/drift \
      uv run pytest tests/db/test_search_docs_body_backfill_integration.py -v

没设 ``INTEGRATION_DATABASE_URL`` 时干净跳过。每个用例自建 id、``finally`` 里拆
干净，所以彼此独立、可反复跑。
"""

from __future__ import annotations

import json
import os
import uuid
from typing import Any, Dict

import asyncpg
import pytest

pytestmark = [pytest.mark.integration, pytest.mark.asyncio]

_TEST_DSN = os.environ.get("INTEGRATION_DATABASE_URL", "").strip()

pytest.importorskip("asyncpg")

_skip = pytest.mark.skipif(
    not _TEST_DSN,
    reason="INTEGRATION_DATABASE_URL not set — the body backfill needs a DB.",
)

#: 那条 ``create`` 账本带齐六个可写字段，所以 v1 重建出来就是它。
V1 = {
    "shot_type": "WS",
    "camera_angle": "eye_level",
    "camera_movement": "static",
    "focal_length": "35mm",
    "lighting": "golden hour",
    "description": "A wide shot of the cafe at dusk.",
}


@pytest.fixture
async def orm_dsn():
    """把 ORM engine 指向测试库，跑完还原并 dispose。同 revert 那个夹具。"""
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
    """一条真链：auth.users → team → project → script → scene → shot + run。

    每个外键都是真的要满足的 —— ``script_projects`` 要 project + team + 创建人，
    ``script_shots`` 从 scene 链下来，``agent_runs`` 要 agent 和 user。这段夹具本身
    就是这个文件在赚它的房租：替身 session 结构上不可能注意到其中任何一条。
    """
    user_id = await pg.fetchval("INSERT INTO auth.users DEFAULT VALUES RETURNING id")
    team_id = await pg.fetchval(
        "INSERT INTO public.teams (name, owner_id, invite_code) "
        "VALUES ($1, $2, $3) RETURNING id",
        "Backfill Test Team",
        user_id,
        uuid.uuid4().hex[:12],
    )
    project_id = await pg.fetchval(
        "INSERT INTO public.projects (name, owner_id, team_id) "
        "VALUES ($1, $2, $3) RETURNING id",
        "Backfill Test Project",
        user_id,
        team_id,
    )
    script_id = await pg.fetchval(
        "INSERT INTO public.script_projects (project_id, team_id, name, created_by) "
        "VALUES ($1, $2, $3, $4) RETURNING id",
        project_id,
        team_id,
        "Backfill Test Script",
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
        f"backfill-test-agent-{uuid.uuid4().hex[:8]}",
    )
    run_id = await pg.fetchval(
        "INSERT INTO public.agent_runs "
        "(agent_id, user_id, team_id, project_id, status, trigger, output_summary) "
        "VALUES ($1, $2, $3, $4, 'completed', 'test', $5) RETURNING id",
        agent_id,
        user_id,
        team_id,
        project_id,
        "Rewrote scene 1 and regenerated one shot.",
    )
    try:
        yield {
            "user_id": user_id,
            "team_id": team_id,
            "project_id": project_id,
            "scene_id": scene_id,
            "shot_id": shot_id,
            "run_id": run_id,
        }
    finally:
        await pg.execute(
            "DELETE FROM public.search_docs WHERE run_id = $1 OR ref_id = ANY($2::text[])",
            run_id,
            [str(shot_id), str(scene_id)],
        )
        await pg.execute(
            "DELETE FROM public.run_deliverables WHERE ref_id = ANY($1::text[])",
            [str(shot_id), str(scene_id)],
        )
        await pg.execute(
            "DELETE FROM public.generated_media WHERE scope_id = $1", team_id
        )
        await pg.execute("DELETE FROM public.agent_runs WHERE id = $1", run_id)
        await pg.execute("DELETE FROM public.ai_agents WHERE id = $1", agent_id)
        await pg.execute("DELETE FROM public.script_projects WHERE id = $1", script_id)
        await pg.execute("DELETE FROM public.projects WHERE id = $1", project_id)
        await pg.execute("DELETE FROM public.teams WHERE id = $1", team_id)
        await pg.execute("DELETE FROM auth.users WHERE id = $1", user_id)


# ── 建局用的小工具 ───────────────────────────────────────────────────────


async def _shot_ledger_row(pg, fx) -> None:
    """一条 ``create`` 账本：v1 的六个字段全在 ``after_json`` 里。"""
    await pg.execute(
        "INSERT INTO public.script_shot_ops "
        "(shot_id, scene_id, run_id, action, after_json) "
        "VALUES ($1, $2, $3, 'create', $4::jsonb)",
        fx["shot_id"],
        fx["scene_id"],
        fx["run_id"],
        json.dumps(V1),
    )


async def _deliverable(pg, fx, *, kind: str, ref_id: str, version: int = 1) -> None:
    await pg.execute(
        "INSERT INTO public.run_deliverables (run_id, kind, ref_id, version, title) "
        "VALUES ($1, $2, $3, $4, $5)",
        fx["run_id"],
        kind,
        ref_id,
        version,
        f"{kind} {ref_id}",
    )


async def _output_doc(pg, fx, *, kind, ref_id, version=1, body=None) -> int:
    """mig 472 回填段写出来的那种产出行：坐标齐全、``body`` 为空。"""
    return await pg.fetchval(
        "INSERT INTO public.search_docs "
        "(entity_kind, entity_id, kind, ref_id, version, title, body, "
        " team_id, project_id, run_id) "
        "VALUES ('output', $1, $2, $3, $4, $5, $6, $7, $8, $9) RETURNING id",
        f"{kind}:{ref_id}:{version}",
        kind,
        ref_id,
        version,
        f"{kind} {ref_id}",
        body,
        fx["team_id"],
        fx["project_id"],
        fx["run_id"],
    )


async def _run_doc(pg, fx, *, body=None) -> int:
    return await pg.fetchval(
        "INSERT INTO public.search_docs "
        "(entity_kind, entity_id, title, body, team_id, project_id, run_id) "
        "VALUES ('run', $1, 'MH-1 · demo', $2, $3, $4, $5) RETURNING id",
        str(fx["run_id"]),
        body,
        fx["team_id"],
        fx["project_id"],
        fx["run_id"],
    )


async def _media(pg, fx, *, prompt: str) -> int:
    return await pg.fetchval(
        "INSERT INTO public.generated_media "
        "(scope_id, creator_id, media_kind, file_path, origin_kind, prompt) "
        "VALUES ($1, $2, 'image', $3, 'canvas', $4) RETURNING id",
        fx["team_id"],
        fx["user_id"],
        f"sb://test/{uuid.uuid4().hex}.png",
        prompt,
    )


async def _body_of(pg, doc_id: int):
    return await pg.fetchval("SELECT body FROM public.search_docs WHERE id=$1", doc_id)


async def _run_backfill(**kwargs):
    from app.services.search.backfill import backfill_search_docs_bodies

    return await backfill_search_docs_bodies(**kwargs)


# ── 三类正文，各自走真链 ─────────────────────────────────────────────────


@_skip
async def test_a_shot_body_is_rebuilt_from_the_real_ledger(orm_dsn, pg, fx):
    """整条链穿过服务器：jsonb 账本 → 三档折叠 → ``render_shot``。

    候选集那条查询的三列连接（``kind`` / ``ref_id`` TEXT + ``version`` INTEGER）
    也在这里第一次被真的执行 —— 类型配错在 ORM 里编译得过，在服务器上不行。
    """
    from app.services.deliverables.diff import render_shot

    await _shot_ledger_row(pg, fx)
    await _deliverable(pg, fx, kind="script_shot", ref_id=str(fx["shot_id"]))
    doc_id = await _output_doc(pg, fx, kind="script_shot", ref_id=str(fx["shot_id"]))

    stats = await _run_backfill()
    assert stats.filled >= 1
    assert await _body_of(pg, doc_id) == render_shot(V1)


@_skip
async def test_a_media_body_is_the_whole_prompt_column(orm_dsn, pg, fx):
    """登记口交的是 ``origin.prompt`` 全文（标题才取首行）。回填补的必须是同一个
    值 —— 补成标题就等于让正文检索只能命中第一行。"""
    prompt = "A lantern-lit alley at dusk.\nShot on 35mm, warm highlights."
    media_id = await _media(pg, fx, prompt=prompt)
    await _deliverable(pg, fx, kind="generated_media", ref_id=str(media_id))
    doc_id = await _output_doc(pg, fx, kind="generated_media", ref_id=str(media_id))

    await _run_backfill()
    assert await _body_of(pg, doc_id) == prompt


@_skip
async def test_a_run_body_is_its_output_summary(orm_dsn, pg, fx):
    """run 行走 ``agent_runs`` 上的连接（``search_docs.run_id`` BIGINT → 主键）。
    mig 472 已经写过一次，所以这条在生产上命中 0 行 —— 但那正是要证明它**能**跑
    的理由：一条永远不执行的语句和一条执行会报错的语句，在日志里长得一样。"""
    doc_id = await _run_doc(pg, fx)

    await _run_backfill()
    assert await _body_of(pg, doc_id) == "Rewrote scene 1 and regenerated one shot."


# ── 幂等、不覆盖、不动时间戳 ─────────────────────────────────────────────


@_skip
async def test_running_it_twice_changes_nothing_the_second_time(orm_dsn, pg, fx):
    """幂等靠的是「补过的行不再是候选」。第二轮 ``scanned`` 必须掉到 0 —— 掉不到
    就说明候选集的谓词没真的生效，而那是一条每次都重写全表的回填。"""
    from app.services.deliverables.diff import render_shot

    await _shot_ledger_row(pg, fx)
    await _deliverable(pg, fx, kind="script_shot", ref_id=str(fx["shot_id"]))
    doc_id = await _output_doc(pg, fx, kind="script_shot", ref_id=str(fx["shot_id"]))

    first = await _run_backfill()
    after_first = await _body_of(pg, doc_id)
    assert after_first == render_shot(V1)
    assert first.filled >= 1

    second = await _run_backfill()
    assert await _body_of(pg, doc_id) == after_first
    # 这一条的 scanned 只能来自别的空行；本行必须已经退出候选集。
    remaining = await pg.fetchval(
        "SELECT count(*) FROM public.search_docs WHERE id=$1 "
        "AND (body IS NULL OR body='')",
        doc_id,
    )
    assert remaining == 0
    assert second.filled == 0


@_skip
async def test_a_row_that_already_has_a_body_is_not_a_candidate(orm_dsn, pg, fx):
    """第一道：有正文的行压根不进候选集，所以整批回填碰都不碰它。

    ⚠️ 这一条**不**证明 SQL 谓词 —— 候选集查询先把它滤掉了，``fill_empty_body``
    根本没被调用。实测过：把那条 ``body IS NULL OR body=''`` 从 UPDATE 里整个摘
    掉，本用例照样绿。真正钉住谓词的是下面那条竞态用例，两条都要有。
    """
    live = "whatever the live writer wrote, and it is newer than the ledger"
    await _shot_ledger_row(pg, fx)
    await _deliverable(pg, fx, kind="script_shot", ref_id=str(fx["shot_id"]))
    doc_id = await _output_doc(
        pg, fx, kind="script_shot", ref_id=str(fx["shot_id"]), body=live
    )

    stats = await _run_backfill()
    assert await _body_of(pg, doc_id) == live
    assert stats.filled == 0


@_skip
async def test_the_live_writer_wins_a_row_filled_after_it_was_read(orm_dsn, pg, fx):
    """第二道，也是唯一真的钉住 SQL 谓词的一条：**读回候选之后**那一行才被填上。

    回填是「读一批空行 → 逐条按账本重建 → 再写回去」，中间这段时间实时写方完全
    可能投过同一行，而它写的是**现在**的内容。这里直接调 ``fill_empty_body``
    模拟那一刻：行已经不空了，UPDATE 必须命中 0 行并回报 False，调用方据此记
    ``raced`` 而不是覆盖。

    谓词放在 Python 侧只能缩小窗口，放进 ``WHERE`` 才是关掉它 —— 所以这一条是在
    服务器上问的，不是在替身上。实测：摘掉谓词本条立刻转红。
    """
    from app.repositories.search_docs_repository import get_search_docs_repository

    live = "the live writer got here first, and this text is newer"
    doc_id = await _output_doc(pg, fx, kind="script_shot", ref_id=str(fx["shot_id"]))
    # 候选集已经读过了（body 那时是 NULL），现在实时写方填上它。
    await pg.execute("UPDATE public.search_docs SET body=$1 WHERE id=$2", live, doc_id)

    wrote = await get_search_docs_repository().fill_empty_body(
        entity_kind="output",
        entity_id=f"script_shot:{fx['shot_id']}:1",
        body="the stale text the backfill rebuilt from the ledger",
    )
    assert wrote is False
    assert await _body_of(pg, doc_id) == live


@_skip
async def test_the_backfill_does_not_bump_updated_at(orm_dsn, pg, fx):
    """``idx_search_docs_team_updated`` 是 ``(team_id, updated_at DESC)``。把几个月
    前的产出统统盖上今天的时间戳，就是让它们在「最近」里排到最前面 —— 回填不是
    一次新事件。这也正是它用 ``fill_empty_body`` 而不是 ``upsert`` 的理由之一。"""
    await _shot_ledger_row(pg, fx)
    await _deliverable(pg, fx, kind="script_shot", ref_id=str(fx["shot_id"]))
    doc_id = await _output_doc(pg, fx, kind="script_shot", ref_id=str(fx["shot_id"]))
    before = await pg.fetchval(
        "SELECT updated_at FROM public.search_docs WHERE id=$1", doc_id
    )

    await _run_backfill()
    after = await pg.fetchval(
        "SELECT updated_at FROM public.search_docs WHERE id=$1", doc_id
    )
    assert after == before
    assert await _body_of(pg, doc_id) is not None


@_skip
async def test_a_dry_run_writes_nothing_at_all(orm_dsn, pg, fx):
    """预演要跑真读、真重建，只跳过写。运维拿它的计数决定要不要真跑，所以它必须
    是同一条路径上的同一批行。"""
    await _shot_ledger_row(pg, fx)
    await _deliverable(pg, fx, kind="script_shot", ref_id=str(fx["shot_id"]))
    doc_id = await _output_doc(pg, fx, kind="script_shot", ref_id=str(fx["shot_id"]))

    stats = await _run_backfill(dry_run=True)
    assert stats.filled >= 1
    assert await _body_of(pg, doc_id) is None


@_skip
async def test_a_chapter_is_counted_unavailable_not_failed(orm_dsn, pg, fx):
    """章节没有账本，谁都重建不出来。它必须落在 ``unavailable`` 上 ——
    记进 ``failed`` 会让每一次回填都看起来有缺陷，而那条噪音很快就没人看了。"""
    await _deliverable(pg, fx, kind="script_chapter", ref_id="chapter-1")
    doc_id = await _output_doc(pg, fx, kind="script_chapter", ref_id="chapter-1")

    stats = await _run_backfill()
    assert await _body_of(pg, doc_id) is None
    assert stats.unavailable >= 1
    assert stats.failed == 0
