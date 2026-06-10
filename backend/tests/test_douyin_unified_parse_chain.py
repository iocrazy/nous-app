"""Tests for the unified Douyin parse chain (ABogus → DrissionPage).

Background — production P1 found 2026-06-10 ("black screen, audio only"):
initial parse and download-time re-parse had FORKED chains. The re-parse
fork was LightHTTP → DrissionPage (no ABogus), and LightHTTP is dead
(douyin share-page anti-bot → permanent NO_ROUTER_DATA), so any transient
HEAD-check failure cascaded into: cleared video_download_urls → failed
re-parse → yt-dlp fallback → HEVC video browsers can't decode.

These tests pin the single chain everybody must use now:
  1. ABogus first (cookie-signed HTTP, covers videos AND image notes)
  2. DrissionPage second (slow browser, last resort)
  3. no LightHTTP anywhere
"""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest

from app.services.media.parsers.douyin_parse import parse_chain

DETAIL = {"aweme_id": "7641214325696253220"}
PARSED = {"platform_id": "7641214325696253220", "title": "t"}

ABOGUS_PARSE = (
    "app.services.media.parsers.douyin_parse.abogus_parser.ABogusDouyinParser.parse"
)
DRISSION_FETCH = (
    "app.services.media.parsers.douyin_parse.drissionpage_parser"
    ".DrissionPageParser.fetch_one_video"
)
FORMATTER = (
    "app.services.media.parsers.douyin_parse.formatter"
    ".DouyinFormatter.parse_aweme_detail"
)
PICK_UA = "app.services.media.parsers.douyin_parse.ua_pool.pick_ua"


def _flags(**overrides) -> dict[str, bool]:
    flags = {"abogus": True, "drissionpage": True}
    flags.update(overrides)
    return flags


@pytest.fixture(autouse=True)
def _stub_flags(monkeypatch):
    """Default: both methods enabled, no DB read."""
    monkeypatch.setattr(
        parse_chain, "get_douyin_method_flags", AsyncMock(return_value=_flags())
    )


@pytest.fixture(autouse=True)
def _stub_ua():
    with patch(PICK_UA, return_value="test-ua"):
        yield


async def _run_chain(**kwargs):
    return await parse_chain.fetch_douyin_detail(
        "https://v.douyin.com/abc123/", **kwargs
    )


@pytest.mark.asyncio
async def test_abogus_succeeds_first_drissionpage_never_called():
    with (
        patch(ABOGUS_PARSE, new=AsyncMock(return_value=DETAIL)) as abogus,
        patch(DRISSION_FETCH, new=AsyncMock()) as drission,
        patch(FORMATTER, new=AsyncMock(return_value=PARSED)),
    ):
        result = await _run_chain(user_id="u1")

    assert result is not None
    aweme_detail, parsed_data, method = result
    assert aweme_detail == DETAIL
    assert parsed_data == PARSED
    assert method == "abogus"
    abogus.assert_awaited_once()
    drission.assert_not_awaited()


@pytest.mark.asyncio
async def test_abogus_empty_falls_back_to_drissionpage():
    with (
        patch(ABOGUS_PARSE, new=AsyncMock(return_value=None)),
        patch(DRISSION_FETCH, new=AsyncMock(return_value=DETAIL)) as drission,
        patch(FORMATTER, new=AsyncMock(return_value=PARSED)),
    ):
        result = await _run_chain()

    assert result is not None
    assert result[2] == "drissionpage"
    drission.assert_awaited_once()


@pytest.mark.asyncio
async def test_abogus_raising_falls_back_to_drissionpage():
    with (
        patch(ABOGUS_PARSE, new=AsyncMock(side_effect=RuntimeError("boom"))),
        patch(DRISSION_FETCH, new=AsyncMock(return_value=DETAIL)),
        patch(FORMATTER, new=AsyncMock(return_value=PARSED)),
    ):
        result = await _run_chain()

    assert result is not None
    assert result[2] == "drissionpage"


@pytest.mark.asyncio
async def test_formatter_empty_after_abogus_falls_through():
    """ABogus may return a detail the formatter can't shape (e.g. gated
    content) — the chain must still try the next method instead of
    surfacing a half-parse."""
    formatter = AsyncMock(side_effect=[None, PARSED])
    with (
        patch(ABOGUS_PARSE, new=AsyncMock(return_value=DETAIL)),
        patch(DRISSION_FETCH, new=AsyncMock(return_value=DETAIL)),
        patch(FORMATTER, new=formatter),
    ):
        result = await _run_chain()

    assert result is not None
    assert result[2] == "drissionpage"
    assert formatter.await_count == 2


@pytest.mark.asyncio
async def test_all_methods_fail_returns_none():
    with (
        patch(ABOGUS_PARSE, new=AsyncMock(return_value=None)),
        patch(DRISSION_FETCH, new=AsyncMock(return_value=None)),
        patch(FORMATTER, new=AsyncMock()),
    ):
        assert await _run_chain() is None


@pytest.mark.asyncio
async def test_abogus_flag_disabled_skips_abogus(monkeypatch):
    monkeypatch.setattr(
        parse_chain,
        "get_douyin_method_flags",
        AsyncMock(return_value=_flags(abogus=False)),
    )
    with (
        patch(ABOGUS_PARSE, new=AsyncMock()) as abogus,
        patch(DRISSION_FETCH, new=AsyncMock(return_value=DETAIL)),
        patch(FORMATTER, new=AsyncMock(return_value=PARSED)),
    ):
        result = await _run_chain()

    assert result is not None
    assert result[2] == "drissionpage"
    abogus.assert_not_awaited()


@pytest.mark.asyncio
async def test_all_flags_disabled_returns_none(monkeypatch):
    monkeypatch.setattr(
        parse_chain,
        "get_douyin_method_flags",
        AsyncMock(return_value=_flags(abogus=False, drissionpage=False)),
    )
    assert await _run_chain() is None


def test_coerce_flag_handles_jsonb_types():
    """system_settings.value is jsonb — asyncpg/postgrest hand back native
    Python types, NOT always str (see reference_system_settings_jsonb_typed)."""
    assert parse_chain._coerce_flag(True) is True
    assert parse_chain._coerce_flag(False) is False
    assert parse_chain._coerce_flag("true") is True
    assert parse_chain._coerce_flag("True") is True
    assert parse_chain._coerce_flag("false") is False
    assert parse_chain._coerce_flag(None) is False


@pytest.mark.asyncio
async def test_flags_default_all_on_when_db_read_fails(monkeypatch):
    monkeypatch.setattr(
        parse_chain,
        "_read_method_flag_rows",
        AsyncMock(side_effect=RuntimeError("db down")),
    )
    flags = await parse_chain.get_douyin_method_flags()
    assert flags == {"abogus": True, "drissionpage": True}


@pytest.mark.asyncio
async def test_no_lighthttp_flag_key():
    """The dead LightHTTP tier must not resurface via admin flags."""
    assert "douyin_lighthttp_enabled" not in parse_chain._METHOD_FLAG_KEYS
    assert "lighthttp" not in parse_chain._METHOD_FLAG_KEYS.values()


@pytest.mark.asyncio
async def test_reparse_douyin_retries_with_bare_platform_id():
    """Download-time re-parse: when the stored short URL no longer
    resolves, retry the chain with the bare aweme_id (ABogus resolves
    digit IDs directly — this replaces the old LightHTTP-directID hack)."""
    calls: list[str] = []

    async def fake_chain(url_or_id, **kwargs):
        calls.append(url_or_id)
        if url_or_id == "999":
            return DETAIL, PARSED, "abogus"
        return None

    with patch.object(parse_chain, "fetch_douyin_detail", side_effect=fake_chain):
        result = await parse_chain.reparse_douyin(
            "999", original_url="https://v.douyin.com/dead/"
        )

    assert result == (PARSED, "abogus")
    assert calls == ["https://v.douyin.com/dead/", "999"]


@pytest.mark.asyncio
async def test_reparse_douyin_returns_none_when_all_sources_fail():
    with patch.object(
        parse_chain, "fetch_douyin_detail", new=AsyncMock(return_value=None)
    ):
        assert (
            await parse_chain.reparse_douyin(
                "999", original_url="https://v.douyin.com/dead/"
            )
            is None
        )


def test_fetch_and_parse_returns_method_and_raises_on_failure():
    """parse_helpers.fetch_and_parse is the workflow-facing sync wrapper:
    returns (aweme_detail, parsed_data, method) and raises when the whole
    chain comes back empty."""
    from app.services.media.parsers.parse_helpers import fetch_and_parse

    with patch(
        "app.services.media.parsers.douyin_parse.parse_chain.fetch_douyin_detail",
        new=AsyncMock(return_value=(DETAIL, dict(PARSED), "abogus")),
    ):
        aweme_detail, parsed_data, method = fetch_and_parse(
            "https://v.douyin.com/abc123/",
            True,
            True,
            categories="cat1",
        )

    assert aweme_detail == DETAIL
    assert method == "abogus"
    assert parsed_data["_pending_categories"] == "cat1"

    with patch(
        "app.services.media.parsers.douyin_parse.parse_chain.fetch_douyin_detail",
        new=AsyncMock(return_value=None),
    ):
        with pytest.raises(RuntimeError):
            fetch_and_parse("https://v.douyin.com/abc123/", True, True)
