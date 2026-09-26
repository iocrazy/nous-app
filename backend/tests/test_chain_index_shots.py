"""``download_helpers.maybe_chain_index_shots`` — policy- / tag-gated shot
indexing on the download chain (spec 2026-09-26 §3.2), and its call site in
``download.chain_followups_step``."""

from __future__ import annotations

import inspect
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from app.services.library.shot_policy import DispatchDecision, ShotsPolicy
from app.tasks import download_helpers as mod

_POLICY = ShotsPolicy("local_only", "off", 5, 200)


def _wire(
    monkeypatch,
    *,
    resource,
    tags=frozenset(),
    dispatch=True,
    reason="provider_local",
    dispatch_error=None,
):
    decisions = []
    dispatched = []

    async def _decide(which, *, force=False):
        decisions.append((which, force))
        return DispatchDecision(
            dispatch=dispatch,
            reason=reason,
            policy=_POLICY,
            provider_local=True,
            space_id=42 if dispatch else None,
        )

    async def _dispatch(**kw):
        dispatched.append(kw)
        if dispatch_error is not None:
            raise dispatch_error
        return "wf-1"

    monkeypatch.setattr(
        "app.repositories.resources_repository.ResourcesRepository",
        lambda: SimpleNamespace(
            get_resource_by_platform_id=AsyncMock(return_value=resource)
        ),
    )
    monkeypatch.setattr(mod, "read_resource_tag_slugs", AsyncMock(return_value=tags))
    monkeypatch.setattr("app.services.library.shot_policy.dispatch_decision", _decide)
    monkeypatch.setattr(
        "app.services.library.shot_dispatch.dispatch_index_shots", _dispatch
    )
    return decisions, dispatched


_VIDEO = {"id": 9_007_199_254_740_993, "mime_type": "video/mp4", "filename": "a.mp4"}


@pytest.mark.asyncio
async def test_dispatches_a_video_under_the_flow_when_the_policy_allows(monkeypatch):
    decisions, dispatched = _wire(monkeypatch, resource=_VIDEO)
    await mod.maybe_chain_index_shots(
        "p1", "u-1", flow_id="flow-1", video_title="Night street"
    )
    assert decisions == [("auto_index", False)]
    assert dispatched == [
        {
            "user_id": "u-1",
            "resource_id": "9007199254740993",
            "title": "Night street",
            "flow_id": "flow-1",
        }
    ]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "resource",
    [
        None,
        {"id": 1, "mime_type": "image/jpeg"},
        {"id": 1, "mime_type": "", "file_type": "audio"},
    ],
)
async def test_skips_non_videos_before_reading_the_policy(monkeypatch, resource):
    decisions, dispatched = _wire(monkeypatch, resource=resource)
    await mod.maybe_chain_index_shots("p1", "u-1")
    assert decisions == [] and dispatched == []


@pytest.mark.asyncio
async def test_file_type_video_counts_without_a_mime(monkeypatch):
    _, dispatched = _wire(
        monkeypatch, resource={"id": 2, "mime_type": None, "file_type": "video"}
    )
    await mod.maybe_chain_index_shots("p1", "u-1")
    assert len(dispatched) == 1


@pytest.mark.asyncio
async def test_policy_refusal_dispatches_nothing(monkeypatch):
    _, dispatched = _wire(
        monkeypatch, resource=_VIDEO, dispatch=False, reason="provider_not_local"
    )
    await mod.maybe_chain_index_shots("p1", "u-1")
    assert dispatched == []


@pytest.mark.asyncio
async def test_the_shots_tag_forces_the_decision(monkeypatch):
    decisions, _ = _wire(monkeypatch, resource=_VIDEO, tags=frozenset({"shots"}))
    await mod.maybe_chain_index_shots("p1", "u-1")
    assert decisions == [("auto_index", True)]


@pytest.mark.asyncio
async def test_a_dispatch_failure_never_raises(monkeypatch):
    _wire(monkeypatch, resource=_VIDEO, dispatch_error=RuntimeError("dbos down"))
    await mod.maybe_chain_index_shots("p1", "u-1")  # best-effort, like its siblings


def test_chain_followups_awaits_it_inside_the_user_scope():
    from app.workflows import download

    src = inspect.getsource(download.chain_followups_step)
    scope_at = src.index("async with request_scope(Scope(user_id=user_id)):")
    call_at = src.index("await maybe_chain_index_shots(")
    assert scope_at < call_at
    assert "flow_id=flow_id" in src[call_at : call_at + 200]
