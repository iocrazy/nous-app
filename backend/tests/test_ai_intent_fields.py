# backend/tests/test_ai_intent_fields.py
"""AI 意图从标签改显式字段（spec 2026-09-10-ai-intent-fields-design.md）。"""

import pytest
from pydantic import ValidationError


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
