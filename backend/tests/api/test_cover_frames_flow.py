# backend/tests/api/test_cover_frames_flow.py

"""封面抽帧也要有流程卡 —— ``task_flows`` 根 + 行上的 ``flow_id``。

任务中心的"N/N steps"卡是前端按 ``task_tracking.flow_id`` 客户端分组出来的
（``frontend/components/TaskCenter/flowGrouping.ts``）。URL 解析链一直有 flow
根，手动触发的任务没有，于是同一个任务中心里一半是流程卡、一半是孤立行，用户
看到的是不一致的两种东西。

抽帧只有一步，但一步也要挂 flow：单步 flow 渲染成 1/1，是正常形态；缺 flow_id
才是那个"渲不出卡"的例外。

``create_flow`` 是 best-effort（失败返回 None）——分组是呈现层的事，绝不能因为
建 flow 失败就不派工，所以这里同时钉住降级路径。
"""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest

from app.services.distribution import cover_frames as cf

pytestmark = pytest.mark.unit

USER_ID = "8e1584e3-9c29-4a5b-90fe-125b74259f7f"
SOURCE_ID = "314463745430475"
FLOW_ID = "flow-uuid-cover"


def _fake_source() -> cf.SourceVideo:
    return cf.SourceVideo(
        resource={"id": SOURCE_ID, "filename": "clip.mp4"},
        file_path="sb://library/t1/ab/cd/abcdef.mp4",
        scope_id="310812366953241",
        folder_id=None,
        library_id=None,
    )


def _patch_endpoint(monkeypatch: pytest.MonkeyPatch, *, flow_id: str | None):
    """把端点的三个边界换掉：读源视频 / task manager / workflow 派工。
    返回记录了 ``create`` kwargs 的 manager。"""
    from app.api import distribution_router as dr

    monkeypatch.setattr(cf, "load_source_video", AsyncMock(return_value=_fake_source()))

    created: list[dict[str, Any]] = []

    async def _create(**kwargs):
        created.append(kwargs)
        return "task-row-id"

    manager = MagicMock()
    manager.create = AsyncMock(side_effect=_create)
    manager.create_flow = AsyncMock(return_value=flow_id)
    monkeypatch.setattr(dr, "get_task_manager", lambda: manager)
    monkeypatch.setattr(dr, "start_workflow_routed", AsyncMock())

    return manager, created


@pytest.mark.asyncio
async def test_extract_creates_a_flow_and_hangs_the_row_on_it(
    monkeypatch: pytest.MonkeyPatch,
):
    from app.api import distribution_router as dr
    from app.schemas.distribution_cover import CoverExtractRequest

    manager, created = _patch_endpoint(monkeypatch, flow_id=FLOW_ID)

    resp = await dr.extract_cover_frames(
        CoverExtractRequest(resource_id=SOURCE_ID, num_frames=3),
        {"id": USER_ID},
    )

    manager.create_flow.assert_awaited_once()
    flow_kwargs = manager.create_flow.await_args.kwargs
    assert flow_kwargs["user_id"] == USER_ID
    assert flow_kwargs["name"] == "Cover frames: clip.mp4"

    assert len(created) == 1, "抽帧没有建 task_tracking 行"
    assert (
        created[0]["flow_id"] == FLOW_ID
    ), "行上没有 flow_id —— flowGrouping 分不了组，任务中心只会渲一条孤立行"
    assert resp.task_id


@pytest.mark.asyncio
async def test_flow_creation_failure_still_dispatches(
    monkeypatch: pytest.MonkeyPatch,
):
    """create_flow 返回 None（best-effort 失败）时，抽帧照常派工，只是回到
    未分组的旧呈现。分组失败不该变成抽帧失败。"""
    from app.api import distribution_router as dr
    from app.schemas.distribution_cover import CoverExtractRequest

    _manager, created = _patch_endpoint(monkeypatch, flow_id=None)

    resp = await dr.extract_cover_frames(
        CoverExtractRequest(resource_id=SOURCE_ID, num_frames=3),
        {"id": USER_ID},
    )

    assert resp.task_id
    assert len(created) == 1
    assert created[0]["flow_id"] is None
