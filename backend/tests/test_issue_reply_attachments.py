"""Task 5 (sub-plan 3): IssueMessagePost.attachments field + workflow threading.

Tests that:
1. IssueMessagePost accepts an attachments list.
2. Attachments default to None when not provided.
3. body validation (min_length=1) is unchanged.
4. Attachments serialise cleanly to dicts (the router calls model_dump()
   before handing them to the DBOS workflow as JSON-serialisable args).
"""

from __future__ import annotations

import pytest

from app.schemas.ai_library_chat import AttachmentRequest
from app.schemas.issue_message import IssueMessagePost


def test_issue_message_post_accepts_attachments():
    p = IssueMessagePost(
        body="hello",
        attachments=[
            AttachmentRequest(
                kind="image", url="personal/u1/temp/x.png", mime="image/png"
            )
        ],
    )
    assert p.attachments is not None
    assert len(p.attachments) == 1
    assert p.attachments[0].kind == "image"
    assert p.attachments[0].url == "personal/u1/temp/x.png"


def test_issue_message_post_attachments_default_none():
    p = IssueMessagePost(body="hello")
    # Either None or [] is acceptable per the field default.
    assert p.attachments is None or p.attachments == []


def test_issue_message_post_body_still_required():
    """Adding optional attachments must not relax body validation."""
    from pydantic import ValidationError

    with pytest.raises(ValidationError):
        IssueMessagePost(body="")  # min_length=1 → stripped → empty → error


def test_issue_message_post_attachments_serializable_as_dicts():
    """The router serialises via model_dump for DBOS input (must round-trip)."""
    p = IssueMessagePost(
        body="hello",
        attachments=[AttachmentRequest(kind="image", url="x.png", mime="image/png")],
    )
    raw = [a.model_dump() for a in p.attachments]
    assert raw == [
        {
            "kind": "image",
            "url": "x.png",
            "data_url": None,
            "alt_text": None,
            "mime": "image/png",
        }
    ]
