# backend/tests/test_ai_intent_fields.py
"""AI 意图从标签改显式字段（spec 2026-09-10-ai-intent-fields-design.md）。"""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from loguru import logger as _loguru
from pydantic import ValidationError


@pytest.fixture
def caplog(caplog):
    handler_id = _loguru.add(caplog.handler, format="{message}", level="WARNING")
    yield caplog
    _loguru.remove(handler_id)


def test_fetch_request_accepts_intent_fields_with_string_coercion():
    from app.api.media_fetch_helpers import MediaFetchRequest

    req = MediaFetchRequest(
        url="https://v.douyin.com/x/",
        transcribe="1",
        summarize="false",
        analyze=True,
        rating="4",
    )
    assert (req.transcribe, req.summarize, req.analyze, req.rating) == (
        True,
        False,
        True,
        4,
    )


def test_fetch_request_defaults_and_blank_rating():
    from app.api.media_fetch_helpers import BatchFetchRequest, MediaFetchRequest

    single = MediaFetchRequest(url="https://v.douyin.com/x/", rating="")
    batch = BatchFetchRequest(urls=["https://v.douyin.com/x/"])
    for req in (single, batch):
        assert (req.transcribe, req.summarize, req.analyze, req.rating) == (
            False,
            False,
            False,
            None,
        )


def test_fetch_request_rejects_out_of_range_rating():
    from app.api.media_fetch_helpers import MediaFetchRequest

    with pytest.raises(ValidationError):
        MediaFetchRequest(url="https://v.douyin.com/x/", rating=6)


@pytest.mark.parametrize(
    "flags, expected",
    [
        ((False, False, False), []),
        ((True, False, False), ["Transcript"]),
        ((False, True, False), ["Summary"]),
        ((False, False, True), ["Analyze"]),
        ((True, True, True), ["Transcript", "Summary", "Analyze"]),
    ],
)
def test_intent_tag_names_mapping(flags, expected):
    from app.api.media_fetch_helpers import intent_tag_names

    t, s, a = flags
    assert intent_tag_names(transcribe=t, summarize=s, analyze=a) == expected


@pytest.mark.asyncio
async def test_resolve_intent_tag_ids_uses_system_lookup_and_skips_missing(caplog):
    from app.api import media_fetch_helpers as h

    repo = MagicMock()
    repo.get_system_tag_ids_by_names = AsyncMock(
        return_value={"Transcript": 11, "Analyze": 33}
    )
    repo.create_tag = AsyncMock()
    with patch.object(h, "get_tags_repository", return_value=repo):
        ids = await h.resolve_intent_tag_ids(
            transcribe=True, summarize=True, analyze=True
        )

    assert ids == ["11", "33"]
    repo.get_system_tag_ids_by_names.assert_awaited_once_with(
        ["Transcript", "Summary", "Analyze"]
    )
    repo.create_tag.assert_not_called()
    assert "Summary" in caplog.text


@pytest.mark.asyncio
async def test_resolve_intent_tag_ids_no_flags_no_query():
    from app.api import media_fetch_helpers as h

    repo = MagicMock()
    repo.get_system_tag_ids_by_names = AsyncMock()
    with patch.object(h, "get_tags_repository", return_value=repo):
        assert (
            await h.resolve_intent_tag_ids(
                transcribe=False, summarize=False, analyze=False
            )
            == []
        )
    repo.get_system_tag_ids_by_names.assert_not_called()


@pytest.mark.asyncio
async def test_set_resource_rating_writes_via_repository():
    from app.workflows import parse as parse_mod

    repo = MagicMock()
    repo.update_resource = AsyncMock(return_value={"id": 7, "rating": 4})
    with patch(
        "app.repositories.resources_repository.ResourcesRepository", return_value=repo
    ):
        assert await parse_mod.set_resource_rating("7", 4) is True
    repo.update_resource.assert_awaited_once_with("7", {"rating": 4})


@pytest.mark.asyncio
async def test_set_resource_rating_swallows_failure():
    from app.workflows import parse as parse_mod

    repo = MagicMock()
    repo.update_resource = AsyncMock(side_effect=RuntimeError("db down"))
    with patch(
        "app.repositories.resources_repository.ResourcesRepository", return_value=repo
    ):
        assert await parse_mod.set_resource_rating("7", 4) is False


@pytest.mark.asyncio
async def test_set_resource_rating_reports_missing_row_as_not_written(caplog):
    """update_resource 对不存在 / scope 看不见的行返回 {} 而不 raise——
    那是「没写进去」，不能报 True，也不能静默。"""
    from app.workflows import parse as parse_mod

    repo = MagicMock()
    repo.update_resource = AsyncMock(return_value={})
    with patch(
        "app.repositories.resources_repository.ResourcesRepository", return_value=repo
    ):
        assert await parse_mod.set_resource_rating("7", 4) is False
    assert "set_resource_rating" in caplog.text


async def _rating_write_seen_scope(enforced: bool):
    """跑一次 set_resource_rating，返回 (结果, update_resource 执行时的环境 scope)。"""
    from app.db import scope as scope_mod
    from app.workflows import parse as parse_mod

    seen: dict = {}

    async def _update(resource_id, data):
        seen["scope"] = scope_mod.current_scope()
        return {"id": 7, "rating": 4}

    repo = MagicMock()
    repo.update_resource = AsyncMock(side_effect=_update)
    with (
        patch(
            "app.repositories.resources_repository.ResourcesRepository",
            return_value=repo,
        ),
        patch(
            "app.db.scope.is_enforced",
            side_effect=lambda table: enforced and table == "resources",
        ),
    ):
        ok = await parse_mod.set_resource_rating("7", 4)
    repo.update_resource.assert_awaited_once_with("7", {"rating": 4})
    return ok, seen["scope"]


@pytest.mark.asyncio
async def test_set_resource_rating_writes_under_system_scope_when_enforced():
    """生产开着 SCOPE_ENFORCE_RESOURCES，而 DBOS 步骤没有请求作用域——
    不包 system_request_scope 时 choke point 抛 UnscopedQueryError，评级被静默拦下。"""
    from app.db.scope import SYSTEM

    ok, seen_scope = await _rating_write_seen_scope(enforced=True)
    assert ok is True
    assert seen_scope is SYSTEM


@pytest.mark.asyncio
async def test_set_resource_rating_stays_legacy_when_not_enforced():
    """flag 关闭时不开 SYSTEM 作用域，行为与改动前逐字节一致。"""
    from app.db.scope import SYSTEM

    ok, seen_scope = await _rating_write_seen_scope(enforced=False)
    assert ok is True
    assert seen_scope is not SYSTEM
    assert seen_scope is None


def test_single_fetch_path_is_wired_for_intents_and_rating():
    """源码钉：单链路必须把意图 id 并入 effective_tag_ids，并把 rating 转发给 parse_workflow。
    parse_workflow 被 @DBOS.workflow() 包着，inspect 未必能取到原函数源码，所以直接读文件。"""
    import inspect
    from pathlib import Path

    from app.api import media_fetch_helpers as h
    from app.workflows import parse as parse_mod

    src = inspect.getsource(h.handle_media_fetch_dispatch)
    assert "await resolve_intent_tag_ids(" in src
    assert '"rating": request.rating' in src

    wf_text = Path(parse_mod.__file__).read_text(encoding="utf-8")
    assert "rating: Optional[int] = None," in wf_text
    assert (
        "set_rating_step(resource_id=str(resource_id), rating=int(rating))" in wf_text
    )


@pytest.mark.asyncio
async def test_batch_attach_after_save_attaches_tags_and_rating(caplog):
    from app.api import media_batch_router as b

    res_repo = MagicMock()
    res_repo.get_resource_by_platform_id = AsyncMock(side_effect=[None, {"id": 99}])
    res_repo.update_resource = AsyncMock(return_value={"id": 99, "rating": 5})
    tags_repo = MagicMock()
    tags_repo.bulk_add_tags_to_resource = AsyncMock()
    with (
        patch(
            "app.repositories.resources_repository.ResourcesRepository",
            return_value=res_repo,
        ),
        patch.object(b, "get_tags_repository", return_value=tags_repo),
        patch.object(b, "resolve_and_attach_tags", new=AsyncMock()) as attach_names,
        patch("asyncio.sleep", new=AsyncMock()),
    ):
        ok = await b.attach_after_save("pid1", ["11", "33"], ["cats"], "u1", rating=5)

    assert ok is True
    tags_repo.bulk_add_tags_to_resource.assert_awaited_once_with(
        "99", ["11", "33"], source="manual"
    )
    attach_names.assert_awaited_once_with("99", ["cats"], "u1")
    res_repo.update_resource.assert_awaited_once_with("99", {"rating": 5})
    assert not [r for r in caplog.records if r.levelname in ("WARNING", "ERROR")]


@pytest.mark.asyncio
async def test_batch_attach_after_save_gives_up_after_attempts():
    from app.api import media_batch_router as b

    res_repo = MagicMock()
    res_repo.get_resource_by_platform_id = AsyncMock(return_value=None)
    with (
        patch(
            "app.repositories.resources_repository.ResourcesRepository",
            return_value=res_repo,
        ),
        patch("asyncio.sleep", new=AsyncMock()),
    ):
        ok = await b.attach_after_save(
            "pid1", ["11"], None, "u1", rating=None, attempts=3
        )
    assert ok is False
    assert res_repo.get_resource_by_platform_id.await_count == 3


@pytest.mark.asyncio
async def test_batch_attach_after_save_warns_when_rating_not_written(caplog):
    """update_resource 对 scope 看不见的行返回 {} 而不 raise——rating 没写进去不能静默。"""
    from app.api import media_batch_router as b

    res_repo = MagicMock()
    res_repo.get_resource_by_platform_id = AsyncMock(return_value={"id": 99})
    res_repo.update_resource = AsyncMock(return_value={})
    with patch(
        "app.repositories.resources_repository.ResourcesRepository",
        return_value=res_repo,
    ):
        ok = await b.attach_after_save("pid1", None, None, "u1", rating=2)
    assert ok is False
    assert "rating not written" in caplog.text


def _attach_repos(*, bulk_add: AsyncMock, update: AsyncMock):
    res_repo = MagicMock()
    res_repo.get_resource_by_platform_id = AsyncMock(return_value={"id": 99})
    res_repo.update_resource = update
    tags_repo = MagicMock()
    tags_repo.bulk_add_tags_to_resource = bulk_add
    return res_repo, tags_repo


@pytest.mark.asyncio
async def test_batch_attach_after_save_contains_tag_failure(caplog):
    """F1：Starlette 顺序跑后台任务且无守卫——这里抛错会让同批后续 URL 的
    process_video 全部跳过（点数已扣）。标签写失败必须容纳并记 ERROR，评级照写。"""
    from app.api import media_batch_router as b

    res_repo, tags_repo = _attach_repos(
        bulk_add=AsyncMock(side_effect=RuntimeError("tags down")),
        update=AsyncMock(return_value={"id": 99, "rating": 4}),
    )
    with (
        patch(
            "app.repositories.resources_repository.ResourcesRepository",
            return_value=res_repo,
        ),
        patch.object(b, "get_tags_repository", return_value=tags_repo),
    ):
        ok = await b.attach_after_save("pid1", ["11"], None, "u1", rating=4)

    assert ok is False
    res_repo.update_resource.assert_awaited_once_with("99", {"rating": 4})
    errors = [r for r in caplog.records if r.levelname == "ERROR"]
    assert errors and "tags down" in errors[0].getMessage()


@pytest.mark.asyncio
async def test_batch_attach_after_save_contains_rating_failure(caplog):
    from app.api import media_batch_router as b

    res_repo, tags_repo = _attach_repos(
        bulk_add=AsyncMock(return_value=[{"tag_id": 11}]),
        update=AsyncMock(side_effect=RuntimeError("db down")),
    )
    with (
        patch(
            "app.repositories.resources_repository.ResourcesRepository",
            return_value=res_repo,
        ),
        patch.object(b, "get_tags_repository", return_value=tags_repo),
    ):
        ok = await b.attach_after_save("pid1", ["11"], None, "u1", rating=4)

    assert ok is False
    tags_repo.bulk_add_tags_to_resource.assert_awaited_once()
    assert any(r.levelname == "ERROR" for r in caplog.records)


@pytest.mark.asyncio
async def test_batch_attach_after_save_contains_lookup_failure(caplog):
    """资源查询本身抛错也不许冒出去——记 ERROR、按预算继续轮询，最终返回 False。"""
    from app.api import media_batch_router as b

    res_repo = MagicMock()
    res_repo.get_resource_by_platform_id = AsyncMock(side_effect=RuntimeError("boom"))
    with (
        patch(
            "app.repositories.resources_repository.ResourcesRepository",
            return_value=res_repo,
        ),
        patch("asyncio.sleep", new=AsyncMock()),
    ):
        ok = await b.attach_after_save(
            "pid1", ["11"], None, "u1", rating=None, attempts=2
        )

    assert ok is False
    assert any(r.levelname == "ERROR" for r in caplog.records)


def test_batch_route_is_wired_for_intents():
    import inspect

    from app.api import media_batch_router as b

    src = inspect.getsource(b.fetch_videos_batch)
    assert "await resolve_intent_tag_ids(" in src
    assert "attach_after_save," in src
    assert "rating=request.rating" in src


async def _run_batch_route(b, *, intent_ids_mock: AsyncMock, **request_fields):
    """直接调 fetch_videos_batch（绕过 Depends），抓取链桩成一条成功解析，
    返回 (响应, 已登记的 attach_after_save 后台任务列表)。"""
    from fastapi import BackgroundTasks

    from app.api.media_fetch_helpers import BatchFetchRequest

    request = BatchFetchRequest(urls=["https://v.douyin.com/abc/"], **request_fields)
    background = BackgroundTasks()
    parsed = {"platform_id": "pid1", "title": "Test Video"}
    with (
        patch.object(b, "resolve_intent_tag_ids", new=intent_ids_mock),
        patch.object(b, "resolve_team_id", new=AsyncMock(return_value=None)),
        patch.object(b, "PointsService"),
        patch.object(
            b,
            "fetch_douyin_detail",
            new=AsyncMock(return_value=({}, parsed, "abogus")),
        ),
    ):
        resp = await b.fetch_videos_batch(
            request, background, MagicMock(user_id="u1"), None, MagicMock()
        )
    attach = [t for t in background.tasks if t.func is b.attach_after_save]
    return resp, attach


@pytest.mark.asyncio
async def test_batch_route_merges_intent_ids_and_forwards_rating():
    from app.api import media_batch_router as b

    intent = AsyncMock(return_value=["5", "11"])
    resp, attach = await _run_batch_route(
        b, intent_ids_mock=intent, tag_ids=["5"], transcribe=True, rating=3
    )

    assert resp["submitted"] == 1
    intent.assert_awaited_once_with(transcribe=True, summarize=False, analyze=False)
    assert len(attach) == 1
    assert attach[0].args == ("pid1", ["5", "11"], None, "u1")
    assert attach[0].kwargs == {"rating": 3}


@pytest.mark.asyncio
async def test_batch_route_survives_intent_resolution_failure(caplog):
    """裁定 R2：意图查询抛错不许 500 整个批次——记 WARNING、按无意图 id 继续。"""
    from app.api import media_batch_router as b

    intent = AsyncMock(side_effect=RuntimeError("db down"))
    resp, attach = await _run_batch_route(
        b, intent_ids_mock=intent, tag_ids=["5"], analyze=True, rating=1
    )

    assert resp["submitted"] == 1
    assert len(attach) == 1
    assert attach[0].args == ("pid1", ["5"], None, "u1")
    assert attach[0].kwargs == {"rating": 1}
    assert "[Fetch/Batch] intent tag resolution failed (non-fatal): db down" in (
        caplog.text
    )


async def _run_celery_batch_route(
    b, *, names_mock: AsyncMock, **request_fields
) -> MagicMock:
    """use_celery 分支：桩掉逐 URL 入队，返回 enqueue_parse_for_user 的 mock。"""
    from fastapi import BackgroundTasks

    from app.api.media_fetch_helpers import BatchFetchRequest

    request = BatchFetchRequest(
        urls=["https://v.douyin.com/a/", "https://v.douyin.com/b/"],
        use_celery=True,
        **request_fields,
    )
    enqueue = MagicMock(side_effect=["wf1", "wf2"])
    with (
        patch.object(b, "resolve_intent_tag_ids", new=AsyncMock(return_value=["11"])),
        patch.object(b, "resolve_tag_names_to_ids", new=names_mock),
        patch.object(b, "resolve_team_id", new=AsyncMock(return_value=None)),
        patch.object(b, "PointsService"),
        patch("app.workflows.parse.enqueue_parse_for_user", new=enqueue),
    ):
        resp = await b.fetch_videos_batch(
            request, BackgroundTasks(), MagicMock(user_id="u1"), None, MagicMock()
        )
    assert resp["workflow_ids"] == ["wf1", "wf2"]
    return enqueue


@pytest.mark.asyncio
async def test_celery_batch_forwards_merged_tag_ids_and_rating():
    """裁定 R21：use_celery 批量是唯一能到 AI 链的批量分支，必须把
    tag_ids（用户 id + 意图 id + 标签名解析出的 id）与 rating 带进 parse_workflow。"""
    from app.api import media_batch_router as b

    names = AsyncMock(return_value=["5", "42"])
    enqueue = await _run_celery_batch_route(
        b, names_mock=names, tag_ids=["5"], tags=["cats"], transcribe=True, rating=2
    )

    names.assert_awaited_once_with(["cats"], "u1")
    assert enqueue.call_count == 2
    for call in enqueue.call_args_list:
        kwargs = call.kwargs["kwargs"]
        assert kwargs["tag_ids"] == ["5", "11", "42"]
        assert kwargs["rating"] == 2


@pytest.mark.asyncio
async def test_celery_batch_survives_tag_name_resolution_failure(caplog):
    from app.api import media_batch_router as b

    names = AsyncMock(side_effect=RuntimeError("db down"))
    enqueue = await _run_celery_batch_route(
        b, names_mock=names, tags=["cats"], analyze=True
    )

    for call in enqueue.call_args_list:
        assert call.kwargs["kwargs"]["tag_ids"] == ["11"]
        assert call.kwargs["kwargs"]["rating"] is None
    assert "tag-name resolution failed (non-fatal): db down" in caplog.text
