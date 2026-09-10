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
