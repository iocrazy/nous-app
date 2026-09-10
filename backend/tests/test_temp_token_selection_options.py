# backend/tests/test_temp_token_selection_options.py
"""快捷指令选择结果携带评级 / 转录 / 总结 / 解析（spec 2026-09-10）。"""

import json
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi import HTTPException

TOKEN_DATA = {"user_id": "u1", "scopes": ["tags:read", "tags:write"], "selection": []}


def _redis(ttl: int = 120):
    r = MagicMock()
    r.ttl = AsyncMock(return_value=ttl)
    r.setex = AsyncMock()
    return r


@pytest.mark.asyncio
async def test_save_selection_persists_options_alongside_tags():
    from app.api.temp_token_router import SelectionRequest, save_selection

    redis = _redis()
    with (
        patch(
            "app.api.temp_token_router._get_token_data",
            new=AsyncMock(return_value=dict(TOKEN_DATA)),
        ),
        patch(
            "app.api.temp_token_router.get_async_redis",
            new=AsyncMock(return_value=redis),
        ),
    ):
        await save_selection(
            "tok",
            SelectionRequest(tags=["cats"], rating=3, transcribe=True, analyze=True),
        )

    _key, _ttl, raw = redis.setex.await_args.args
    stored = json.loads(raw)
    assert stored["selection"] == ["cats"]
    assert stored["options"] == {
        "rating": 3,
        "transcribe": True,
        "summarize": False,
        "analyze": True,
    }


@pytest.mark.asyncio
async def test_get_selection_json_returns_options_and_legacy_defaults():
    from app.api.temp_token_router import get_selection

    legacy = {**TOKEN_DATA, "selection": ["a", "b"]}  # 旧令牌：没有 options 键
    with patch(
        "app.api.temp_token_router._get_token_data", new=AsyncMock(return_value=legacy)
    ):
        resp = await get_selection("tok", format="json", field=None)
    assert resp.model_dump() == {
        "tags": ["a", "b"],
        "rating": None,
        "transcribe": False,
        "summarize": False,
        "analyze": False,
    }


@pytest.mark.asyncio
async def test_get_selection_text_default_is_unchanged_tag_csv():
    from app.api.temp_token_router import get_selection

    data = {
        **TOKEN_DATA,
        "selection": ["a", "b"],
        "options": {"rating": 5, "transcribe": True},
    }
    with patch(
        "app.api.temp_token_router._get_token_data", new=AsyncMock(return_value=data)
    ):
        resp = await get_selection("tok", format="text", field=None)
    assert resp.body == b"a,b"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "field, expected",
    [("rating", b"5"), ("transcribe", b"1"), ("summarize", b"0"), ("analyze", b"0")],
)
async def test_get_selection_text_field_returns_single_value(field, expected):
    from app.api.temp_token_router import get_selection

    data = {
        **TOKEN_DATA,
        "selection": ["a"],
        "options": {"rating": 5, "transcribe": True},
    }
    with patch(
        "app.api.temp_token_router._get_token_data", new=AsyncMock(return_value=data)
    ):
        resp = await get_selection("tok", format="text", field=field)
    assert resp.body == expected


@pytest.mark.asyncio
async def test_get_selection_text_rating_none_reads_as_zero():
    from app.api.temp_token_router import get_selection

    with patch(
        "app.api.temp_token_router._get_token_data",
        new=AsyncMock(return_value=dict(TOKEN_DATA)),
    ):
        resp = await get_selection("tok", format="text", field="rating")
    assert resp.body == b"0"


@pytest.mark.asyncio
async def test_get_selection_rejects_unknown_field():
    from app.api.temp_token_router import get_selection

    with patch(
        "app.api.temp_token_router._get_token_data",
        new=AsyncMock(return_value=dict(TOKEN_DATA)),
    ):
        with pytest.raises(HTTPException) as exc:
            await get_selection("tok", format="text", field="mood")
    assert exc.value.status_code == 400


@pytest.mark.asyncio
async def test_get_selection_text_csv_never_parses_options():
    """裁定 R22：format=text 的 CSV 老路径不解析 options——坏掉的 options 也不影响它。"""
    from app.api.temp_token_router import get_selection

    data = {**TOKEN_DATA, "selection": ["a", "b"], "options": {"rating": 99}}
    with patch(
        "app.api.temp_token_router._get_token_data", new=AsyncMock(return_value=data)
    ):
        resp = await get_selection("tok", format="text", field=None)
    assert resp.body == b"a,b"
