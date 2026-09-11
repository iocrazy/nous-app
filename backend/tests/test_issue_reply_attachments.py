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
    # S4 PR #361 added optional resource_id/name/scope fields to
    # AttachmentRequest for the @-reference path; P5 added asset_id/loadout_id
    # for the asset-reference path. All default to None when an image-kind
    # attachment is built.
    #
    # ⚠️ The two asset keys exist on THIS wire without being honoured on it:
    # the issue reply path shares `AttachmentRequest` but does not run the
    # asset resolver (P5 ruling H — chat only this round). An issue reply that
    # sent `kind='asset_ref'` would fall through to the binary path and come
    # back as a typed "unsupported attachment kind" failure, not a silent drop.
    #
    # 三期 3a Task 4 added ref_kind / ref_id / version / title for
    # `kind='output_ref'`. Those four ARE honoured on this wire — the issue
    # message endpoint resolves them against the deliverables registry and
    # refuses an unresolvable citation with a typed 400 — which is why they sit
    # beside the asset keys rather than under the caveat above.
    assert raw == [
        {
            "kind": "image",
            "url": "x.png",
            "data_url": None,
            "alt_text": None,
            "mime": "image/png",
            "resource_id": None,
            "name": None,
            "scope": None,
            "asset_id": None,
            "loadout_id": None,
            "ref_kind": None,
            "ref_id": None,
            "version": None,
            "title": None,
        }
    ]
