"""The text leg's space normalisation removes only spaces next to CJK.

It was written for "31 岁" -> "31岁" (CJK titles carry no spaces) but
deleted EVERY space, so a Latin phrase like "Morning Routine 2024" became
``%MorningRoutine2024%`` and could never match its own title."""

from __future__ import annotations

from typing import Any, Dict, List

import pytest

from app.services.library.search_service import SearchService, normalize_query_spaces

pytestmark = pytest.mark.unit


@pytest.mark.parametrize(
    "raw, expected",
    [
        ("31 岁", "31岁"),
        ("日本 夜景", "日本夜景"),
        ("Morning Routine 2024", "Morning Routine 2024"),
        ("Krea2 特调", "Krea2特调"),
        ("東京　夜景", "東京夜景"),  # ideographic space
        ("日本  夜景 rain", "日本夜景rain"),
        ("", ""),
    ],
)
def test_normalize_query_spaces(raw: str, expected: str) -> None:
    assert normalize_query_spaces(raw) == expected


async def test_latin_phrase_reaches_the_rpc_with_its_spaces(monkeypatch) -> None:
    svc = SearchService()
    captured: List[Dict[str, Any]] = []

    async def _fake_text(**kwargs: Any) -> List[Dict[str, Any]]:
        captured.append(kwargs)
        return [{"id": 5, "platform_id": "px", "title": "Morning Routine 2024"}]

    monkeypatch.setattr(svc, "search_user_media_text", _fake_text)

    # limit=1 and one text row: the vector leg is skipped (full page), so only
    # the text RPC is exercised.
    resp = await svc.hybrid_search(query="Morning Routine 2024", user_id="u-1", limit=1)

    assert captured[0]["pattern"] == "%Morning Routine 2024%"
    assert resp.vector_leg == "skipped_full_page"
