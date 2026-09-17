"""POST /tasks/{id}/retry: parse 真的被重新派发；无派发分支的类型 409，行不动。"""

from __future__ import annotations

import importlib
from unittest.mock import AsyncMock, MagicMock

import pytest
from fastapi import HTTPException

# 完整 URL —— 比 task_tracking.title 的 50 字符截断长，这正是重点。
FULL_URL = (
    "https://www.bilibili.com/video/BV1EZhEzLEp7/"
    "?spm_id_from=333.337.search-card.all.click&vd_source=79705ab0af488edbc0"
)


def _auth(user_id: str = "user-1") -> MagicMock:
    auth = MagicMock()
    auth.user_id = user_id
    return auth


def _tm():
    # api 包把 router 对象按模块名重新导出，所以 import 语句拿到的是 APIRouter。
    return importlib.import_module("app.api.task_manager_router")


@pytest.fixture(autouse=True)
def _clear_gate_cache():
    from app.core.cache import module_gate_cache

    module_gate_cache.clear()
    yield
    module_gate_cache.clear()


@pytest.fixture
def _parser_on(monkeypatch):
    """parse 重试要过 media-parser 开关；本文件关心的不是那道闸。"""
    tm = _tm()
    monkeypatch.setattr(tm, "_require_media_parser", AsyncMock())
    return tm


def _peek(task_type: str, dedup_key: str | None = None):
    return AsyncMock(
        return_value={
            "task_type": task_type,
            "dedup_key": dedup_key,
            "status": "failed",
        }
    )


# ---------------------------------------------------------------------------
# parse 真的被重新派发了
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_a_failed_parse_is_actually_re_dispatched(monkeypatch, _parser_on):
    """在 2026-09-15 之前这里什么都不会发生 —— 行被置回 QUEUED，然后没有下文。"""
    tm = _parser_on
    monkeypatch.setattr(tm, "_peek_task_row", _peek("parse", f"task:parse:{FULL_URL}"))

    tracker = MagicMock()
    tracker.retry_task = AsyncMock(
        return_value={
            "task_type": "parse",
            "flow_id": "flow-7",
            "metadata": {"retry_count": 1},
        }
    )
    monkeypatch.setattr(tm, "get_task_manager", lambda: tracker)

    started = AsyncMock()
    monkeypatch.setattr(
        "app.services.infra.dbos_orchestrator.start_workflow_routed", started
    )

    out = await tm.retry_task("parse-abc", _auth(), MagicMock())

    assert out["success"] is True
    started.assert_awaited_once()
    kwargs = started.await_args.kwargs
    assert kwargs["dbos_workflow_kwargs"]["url"] == FULL_URL
    # 派发用的 workflow id 必须和 retry_task 拿到的 re-key 目标是同一个，否则行
    # 指着 A、workflow 跑的是 B，生命周期 trigger 永远不会更新它。
    assert (
        kwargs["workflow_id"] == tracker.retry_task.await_args.kwargs["new_workflow_id"]
    )


@pytest.mark.asyncio
async def test_the_url_comes_from_dedup_key_not_the_truncated_title(
    monkeypatch, _parser_on
):
    """``title`` 是 ``f"Parse {url[:50]}"`` —— 拿它去重新解析等于提交一个被剪断的"""
    tm = _parser_on
    monkeypatch.setattr(tm, "_peek_task_row", _peek("parse", f"task:parse:{FULL_URL}"))

    tracker = MagicMock()
    tracker.retry_task = AsyncMock(
        return_value={"task_type": "parse", "title": f"Parse {FULL_URL[:50]}"}
    )
    monkeypatch.setattr(tm, "get_task_manager", lambda: tracker)

    started = AsyncMock()
    monkeypatch.setattr(
        "app.services.infra.dbos_orchestrator.start_workflow_routed", started
    )

    await tm.retry_task("parse-abc", _auth(), MagicMock())

    url = started.await_args.kwargs["dbos_workflow_kwargs"]["url"]
    assert url == FULL_URL
    assert len(url) > 50, "拿到的必须是完整 URL，不是标题里那截"


@pytest.mark.asyncio
async def test_the_platform_is_re_detected_not_defaulted(monkeypatch, _parser_on):
    """``parse_workflow`` 的 platform 默认是 ``douyin``，而把一个 B 站链接喂进抖音"""
    tm = _parser_on
    monkeypatch.setattr(tm, "_peek_task_row", _peek("parse", f"task:parse:{FULL_URL}"))

    tracker = MagicMock()
    tracker.retry_task = AsyncMock(return_value={"task_type": "parse"})
    monkeypatch.setattr(tm, "get_task_manager", lambda: tracker)

    started = AsyncMock()
    monkeypatch.setattr(
        "app.services.infra.dbos_orchestrator.start_workflow_routed", started
    )

    await tm.retry_task("parse-abc", _auth(), MagicMock())

    assert started.await_args.kwargs["dbos_workflow_kwargs"]["platform"] == "bilibili"


@pytest.mark.asyncio
async def test_the_retry_stays_in_the_original_pipeline_group(monkeypatch, _parser_on):
    """flow_id 带过去，任务中心的流程卡才还是一条链，而不是旁边冒出一条新的。"""
    tm = _parser_on
    monkeypatch.setattr(tm, "_peek_task_row", _peek("parse", f"task:parse:{FULL_URL}"))

    tracker = MagicMock()
    tracker.retry_task = AsyncMock(
        return_value={"task_type": "parse", "flow_id": "flow-7"}
    )
    monkeypatch.setattr(tm, "get_task_manager", lambda: tracker)

    started = AsyncMock()
    monkeypatch.setattr(
        "app.services.infra.dbos_orchestrator.start_workflow_routed", started
    )

    await tm.retry_task("parse-abc", _auth(), MagicMock())

    assert started.await_args.kwargs["dbos_workflow_kwargs"]["flow_id"] == "flow-7"


@pytest.mark.asyncio
async def test_a_parse_row_with_no_url_does_not_dispatch_a_blank_parse(
    monkeypatch, _parser_on
):
    """legacy 行可能没有 dedup_key。那时候正确的做法是什么都不派发并记日志 ——"""
    tm = _parser_on
    monkeypatch.setattr(tm, "_peek_task_row", _peek("parse", None))

    tracker = MagicMock()
    tracker.retry_task = AsyncMock(return_value={"task_type": "parse"})
    monkeypatch.setattr(tm, "get_task_manager", lambda: tracker)

    started = AsyncMock()
    monkeypatch.setattr(
        "app.services.infra.dbos_orchestrator.start_workflow_routed", started
    )

    out = await tm.retry_task("parse-legacy", _auth(), MagicMock())

    assert out["success"] is True
    started.assert_not_awaited()


@pytest.mark.asyncio
async def test_parse_retry_obeys_the_media_parser_switch(monkeypatch):
    """parse 重试就是一次新的解析派发，和 /media/fetch 受同一个开关管。"""
    from unittest.mock import patch

    tm = _tm()
    monkeypatch.setattr(tm, "_peek_task_row", _peek("parse", f"task:parse:{FULL_URL}"))
    tracker = MagicMock()
    tracker.retry_task = AsyncMock(return_value={"task_type": "parse"})
    monkeypatch.setattr(tm, "get_task_manager", lambda: tracker)

    with patch(
        "app.services.modules.registry._read_raw",
        new=AsyncMock(return_value={"enabled": False, "visible": False}),
    ):
        with pytest.raises(HTTPException) as exc:
            await tm.retry_task("parse-abc", _auth(), MagicMock())

    assert exc.value.status_code == 503
    tracker.retry_task.assert_not_awaited()


# ---------------------------------------------------------------------------
# 没有派发分支的类型必须被明确拒绝
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_a_type_with_no_branch_is_refused_and_the_row_is_left_alone(monkeypatch):
    """**本文件最重要的一条。**"""
    tm = _tm()
    monkeypatch.setattr(tm, "_peek_task_row", _peek("agent"))
    tracker = MagicMock()
    tracker.retry_task = AsyncMock()
    monkeypatch.setattr(tm, "get_task_manager", lambda: tracker)

    with pytest.raises(HTTPException) as exc:
        await tm.retry_task("agent-run-1", _auth(), MagicMock())

    assert exc.value.status_code == 409
    assert exc.value.detail["code"] == "retry_not_supported"
    # 行一个字都没动 —— 否则这个「拒绝」自己就成了那个 bug。
    tracker.retry_task.assert_not_awaited()


@pytest.mark.asyncio
async def test_a_missing_task_is_a_404_before_anything_is_touched(monkeypatch):
    tm = _tm()
    monkeypatch.setattr(tm, "_peek_task_row", AsyncMock(return_value=None))
    tracker = MagicMock()
    tracker.retry_task = AsyncMock()
    monkeypatch.setattr(tm, "get_task_manager", lambda: tracker)

    with pytest.raises(HTTPException) as exc:
        await tm.retry_task("nope", _auth(), MagicMock())

    assert exc.value.status_code == 404
    tracker.retry_task.assert_not_awaited()


def test_every_retryable_type_has_a_dispatch_branch():
    """``RETRYABLE_TASK_TYPES`` 是契约，不是注释。"""
    import inspect

    tm = _tm()
    src = inspect.getsource(tm.retry_task)
    for task_type in tm.RETRYABLE_TASK_TYPES:
        assert f'task_type == "{task_type}"' in src, (
            f"{task_type!r} 在 RETRYABLE_TASK_TYPES 里，但 retry_task 没有它的派发"
            f"分支 —— 重试它会静默变成一次 no-op"
        )
