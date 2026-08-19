"""封面链路的 **tenant scope 边界** 回归测试。

起因是一次生产故障：用户在发布页点封面，五次全部看到"该视频已不可用 —— 请
换一个"，而视频完好无损。真实链条是四段，每一段单独看都"合理"：

    1. distribution_router 的两个封面端点**没有建立 ambient scope**
       （本路由整个没挂 ScopedRequestDep）
    2. → 选择点 fail-closed，抛 UnscopedQueryError
    3. → ResourcesRepository.get_resource_by_id **把异常吞成 None**
    4. → load_source_video 判成 "source resource not found" → 404
       → 前端 coverSourceMissing:"该视频已不可用"

第 3 段是故障能**伪装成用户问题**的关键：一个服务端缺陷被翻译成了关于用户
数据的错误答案，全程零 5xx，所以错误漏斗里什么都看不见。

因此这里分两组测，对应两道独立的防线 —— 任何一道单独回退都必须让测试变红：

  A. **边界存在**：三个入口（extract 端点 / select 端点 / workflow step）在调
     业务代码时都必须已经有 ambient scope，且带对了 user_id。
  B. **异常不被降级**：缺 scope 必须一路抛到底，绝不退化成 None / 404。

A 组防的是"忘了开 scope"，B 组防的是"开漏了也看不出来"。只有 A 组的话，下一
个忘开 scope 的端点会重演同一场静默故障。
"""

from __future__ import annotations

from typing import Any, Dict, Optional
from unittest.mock import AsyncMock

import pytest

from app.db.scope import SYSTEM, Scope, UnscopedQueryError, current_scope
from app.services.distribution import cover_frames as cf

pytestmark = pytest.mark.unit

USER_ID = "8e1584e3-9c29-4a5b-90fe-125b74259f7f"
SOURCE_ID = "314463745430475"


# ============================================================
# A 组 —— 入口边界必须建立 ambient scope
# ============================================================


class _ScopeSpy:
    """记录被调用那一刻的 ambient scope。

    断言看的是"业务代码运行时 contextvar 是什么"，而不是"某个函数被调用过"
    —— 后者用 mock 也能过，前者只有真的开了 scope 才成立。
    """

    def __init__(self, result: Any = None):
        self.result = result
        self.seen: Any = "NOT-CALLED"
        self.calls = 0

    async def __call__(self, *args: Any, **kwargs: Any) -> Any:
        self.seen = current_scope()
        self.calls += 1
        return self.result


def _fake_source() -> cf.SourceVideo:
    return cf.SourceVideo(
        resource={"id": SOURCE_ID, "filename": "clip.mp4"},
        file_path="sb://library/t1/ab/cd/abcdef.mp4",
        scope_id="310812366953241",
        folder_id=None,
        library_id=None,
    )


@pytest.mark.asyncio
async def test_extract_endpoint_opens_user_scope(monkeypatch: pytest.MonkeyPatch):
    """POST /covers/extract 必须在 USER scope 下读源视频。

    这是生产 404 的直接修复点。摘掉 router 里的 ``request_scope`` 包裹，
    ``seen`` 就会是 None，本例立刻红。
    """
    from app.api import distribution_router as dr

    spy = _ScopeSpy(result=_fake_source())
    monkeypatch.setattr(cf, "load_source_video", spy)

    manager = AsyncMock()
    monkeypatch.setattr(dr, "get_task_manager", lambda: manager)
    monkeypatch.setattr(dr, "start_workflow_routed", AsyncMock())

    from app.schemas.distribution_cover import CoverExtractRequest

    resp = await dr.extract_cover_frames(
        CoverExtractRequest(resource_id=SOURCE_ID, num_frames=3),
        {"id": USER_ID},
    )

    assert spy.calls == 1
    assert isinstance(spy.seen, Scope), (
        f"源视频是在 {spy.seen!r} 下读的 —— 端点没有建立 tenant scope，"
        f"选择点会 fail-closed，用户会看到假的 404"
    )
    assert spy.seen.user_id == USER_ID
    assert resp.task_id


@pytest.mark.asyncio
async def test_select_endpoint_opens_user_scope(monkeypatch: pytest.MonkeyPatch):
    """POST /covers/select 同款 —— 它读+写 resources，缺 scope 一样炸。

    用户没先撞上这条只是因为抽帧失败得更早，他根本走不到选帧。
    """
    from app.api import distribution_router as dr

    pair = cf.CoverPair(
        vertical_resource_id="1",
        horizontal_resource_id="2",
        source_frame_resource_id="3",
    )
    spy = _ScopeSpy(result=pair)
    monkeypatch.setattr(cf, "derive_cover_pair", spy)

    from app.schemas.distribution_cover import CoverSelectRequest

    resp = await dr.select_cover_frame(
        CoverSelectRequest(
            source_resource_id="338406805373877", timestamp_seconds=12.5
        ),
        {"id": USER_ID},
    )

    assert spy.calls == 1
    assert isinstance(spy.seen, Scope), f"选帧在 {spy.seen!r} 下跑 —— 缺 tenant scope"
    assert spy.seen.user_id == USER_ID
    assert resp.cover_vertical_resource_id == "1"


@pytest.mark.asyncio
async def test_workflow_step_opens_user_scope(monkeypatch: pytest.MonkeyPatch):
    """DBOS step 必须自建 scope —— HTTP 请求的 scope 不会跨到 workflow 任务里。

    contextvar 随响应结束就 reset 了；workflow 在另一个执行任务里跑。所以就算
    两个端点都修好了，这一步漏掉的话，抽帧仍然会在 workflow 侧全军覆没 —— 而
    且那次失败只会出现在任务中心，比同步 404 更难归因。
    """
    from app.workflows import cover_frames as wf

    payload = cf.CoverCandidates(
        source_resource_id=SOURCE_ID, duration_seconds=61.0, candidates=()
    )
    spy = _ScopeSpy(result=payload)
    monkeypatch.setattr(cf, "extract_cover_candidates", spy)

    # heartbeat 要 DB，换成空壳；本例只关心 scope 边界。
    from contextlib import asynccontextmanager

    @asynccontextmanager
    async def _no_heartbeat(**_kwargs: Any):
        yield

    import app.services.workflow_heartbeat as hb

    monkeypatch.setattr(hb, "async_heartbeat_loop", _no_heartbeat)

    await wf.extract_cover_frames_step.__wrapped__("wf-1", SOURCE_ID, USER_ID, 3)

    assert spy.calls == 1
    assert isinstance(spy.seen, Scope), (
        f"抽帧 step 在 {spy.seen!r} 下跑 —— workflow 没有继承 HTTP 的 scope，"
        f"必须自己开"
    )
    assert spy.seen.user_id == USER_ID


@pytest.mark.asyncio
async def test_workflow_step_scope_is_user_not_system(
    monkeypatch: pytest.MonkeyPatch,
):
    """候选帧归发起人，所以用 USER scope 而不是 SYSTEM。

    SYSTEM 也能让查询跑通，所以上一个测试单独看是可以用 SYSTEM 蒙混过关的 ——
    这一条把"跑通"和"以正确身份跑通"分开钉住。
    """
    from app.workflows import cover_frames as wf

    spy = _ScopeSpy(
        result=cf.CoverCandidates(
            source_resource_id=SOURCE_ID, duration_seconds=None, candidates=()
        )
    )
    monkeypatch.setattr(cf, "extract_cover_candidates", spy)

    from contextlib import asynccontextmanager

    @asynccontextmanager
    async def _no_heartbeat(**_kwargs: Any):
        yield

    import app.services.workflow_heartbeat as hb

    monkeypatch.setattr(hb, "async_heartbeat_loop", _no_heartbeat)

    await wf.extract_cover_frames_step.__wrapped__("wf-2", SOURCE_ID, USER_ID, 1)

    assert spy.seen is not SYSTEM, "候选帧不该以 SYSTEM 身份落库"
    assert isinstance(spy.seen, Scope) and spy.seen.user_id == USER_ID


# ============================================================
# B 组 —— 缺 scope 不得被降级成"资源不存在"
# ============================================================


class _RaisingRepo:
    """``get_resource_by_id`` 抛 UnscopedQueryError —— 即真实选择点的行为。"""

    async def get_resource_by_id(self, resource_id: str):
        raise UnscopedQueryError(
            "SELECT references scoped table(s) ['Resources(resources)'] but no "
            "scope is set."
        )

    async def get_first_resource_item(self, resource_id: str):
        return {"scope_id": "1"}


@pytest.mark.asyncio
async def test_load_source_video_does_not_translate_missing_scope_into_404():
    """缺 scope 必须穿透，**不能**变成 CoverFrameError(404)。

    这条正是生产那次误报的核心：404 是在讲用户的数据（"这个视频没了"），而真相
    是服务端没开 scope。把后者说成前者，用户会去删视频、换视频、重传视频 —— 全
    都无济于事，而我们的错误漏斗一片安静。
    """
    with pytest.raises(UnscopedQueryError):
        await cf.load_source_video(_RaisingRepo(), SOURCE_ID)


@pytest.mark.asyncio
async def test_repo_reraises_unscoped_query_error(monkeypatch: pytest.MonkeyPatch):
    """``get_resource_by_id`` 遇到 UnscopedQueryError 必须抛，不能返回 None。

    把仓库层那个 ``except UnscopedQueryError: raise`` 分支删掉（回到统一的
    ``except Exception: return None``），本例立刻红。
    """
    import app.repositories.resources_repository as rr

    class _Boom:
        async def __aenter__(self):
            raise UnscopedQueryError("no scope is set")

        async def __aexit__(self, *_a: Any) -> bool:
            return False

    monkeypatch.setattr(rr, "read_scope", lambda: _Boom())

    with pytest.raises(UnscopedQueryError):
        await rr.ResourcesRepository().get_resource_by_id(SOURCE_ID)


@pytest.mark.asyncio
async def test_repo_still_swallows_unrelated_errors(monkeypatch: pytest.MonkeyPatch):
    """只收窄了缺 scope 那一支，其余异常保持既有的 log + None。

    正向对照：证明上一条不是把 ``except Exception`` 整个删掉 —— 那会让一堆
    与本次故障无关的调用点跟着改行为。
    """
    import app.repositories.resources_repository as rr

    class _Boom:
        async def __aenter__(self):
            raise RuntimeError("connection reset")

        async def __aexit__(self, *_a: Any) -> bool:
            return False

    monkeypatch.setattr(rr, "read_scope", lambda: _Boom())

    assert await rr.ResourcesRepository().get_resource_by_id(SOURCE_ID) is None


@pytest.mark.asyncio
async def test_extract_endpoint_reports_missing_scope_as_500_not_404(
    monkeypatch: pytest.MonkeyPatch,
):
    """真漏了 scope 时端点回 500，而不是把它说成 404。

    状态码是前端唯一的分流依据：404 → "该视频已不可用，请换一个"（关于用户数
    据的断言），5xx → "服务端出错，请重试"（关于我们自己的断言）。说错了，用户
    就会去修一个根本没坏的东西。
    """
    from fastapi import HTTPException

    from app.api import distribution_router as dr

    async def _raise(*_a: Any, **_k: Any):
        raise UnscopedQueryError("no scope is set")

    monkeypatch.setattr(cf, "load_source_video", _raise)
    monkeypatch.setattr(dr, "get_task_manager", lambda: AsyncMock())
    monkeypatch.setattr(dr, "start_workflow_routed", AsyncMock())

    from app.schemas.distribution_cover import CoverExtractRequest

    with pytest.raises(HTTPException) as exc:
        await dr.extract_cover_frames(
            CoverExtractRequest(resource_id=SOURCE_ID), {"id": USER_ID}
        )
    assert exc.value.status_code == 500


@pytest.mark.asyncio
async def test_genuinely_missing_resource_is_still_404():
    """真的查不到就该是 404 —— B 组没有把"资源不存在"这条路也变成 500。

    两种情况必须继续分得开：数据答案 vs 服务端缺陷。
    """

    class _EmptyRepo:
        async def get_resource_by_id(
            self, resource_id: str
        ) -> Optional[Dict[str, Any]]:
            return None

        async def get_first_resource_item(self, resource_id: str):
            return None

    with pytest.raises(cf.CoverFrameError) as exc:
        await cf.load_source_video(_EmptyRepo(), "404404404")
    assert exc.value.status_code == 404
