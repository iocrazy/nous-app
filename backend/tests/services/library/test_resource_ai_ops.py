"""``resource_ai_ops.caption_resource_for_caller`` — the one genuinely new
function on the P2-3 path, pinned branch by branch (no DB, no network).

It is the synchronous half of ``caption_asset_workflow``: resolve the row →
resolve which image to look at → resolve the caption agent → materialize →
``CaptionService.caption``. Everything it can answer is a DIFFERENT user
action, and the whole point of the typed exceptions is that the four outcomes
never collapse into one another:

* row invisible / gone → ``CaptionSourceUnavailable`` (nothing to do: not
  the caller's file)
* album / no downloaded cover / unsupported type → ``CaptionSourceUnavailable``
  carrying the GATE's own words (caption per slide, re-download, pick another
  file — three unrelated fixes)
* the agent is paused → ``CaptionAgentPaused`` (resume it, or raise its budget)
* it ran and produced nothing → ``CaptionAgentFailed`` (check the model does
  vision)
* the provider is down → propagates RAW (retry / fix credentials)

The paused branch is the subtle one: ``CaptionService.caption`` swallows
``AgentPausedError`` into a plain ``None``, so at this layer "paused" and
"produced nothing" arrive as the SAME value and are only told apart by reading
``ai_agents.paused_reason`` back. A test that only checked "empty → some 5xx"
would pass with that distinction deleted.

Every collaborator is monkeypatched at its SOURCE module, because
``caption_resource_for_caller`` imports them function-locally.
"""

from __future__ import annotations

from contextlib import asynccontextmanager
from pathlib import Path

import pytest

import app.repositories.agent_repository as agent_repo_mod
import app.repositories.resources_repository as resources_repo_mod
import app.services.ai.caption as caption_pkg
import app.services.ai.caption_source as caption_source_mod
import app.services.ai.providers.ai_provider_helpers as helpers_mod
import app.services.library.media_storage as media_storage_mod
from app.services.library.resource_ai_ops import (
    CaptionAgentFailed,
    CaptionAgentPaused,
    CaptionSourceUnavailable,
    caption_resource_for_caller,
)

USER = "11111111-1111-1111-1111-111111111111"
RESOURCE = "727145299382534146"

# The shape ``resources_repository`` really returns for an uploaded image —
# mime_type is what ``caption_kind`` discriminates on, NOT file_type (see that
# module's docstring: file_type holds raw platform codes for downloads).
IMAGE_ROW = {
    "id": RESOURCE,
    "filename": "sang-yao-sheet.png",
    "mime_type": "image/png",
    "file_type": "image",
    "file_path": "global/resources/upload/sang-yao-sheet.png",
    "media_id": None,
}


class _ResolvedConfig:
    """Mirrors ``ResolvedAIConfig``'s read surface (the five fields this path
    touches). A dict would not: the production code uses attribute access."""

    provider_key = "qwen"
    provider_config = {"model": "qwen-vl-max", "api_key": "k"}
    model = "qwen-vl-max"
    agent_slug = "caption"
    fallback_models = ["doubao-pro"]


class _FakeRepo:
    """``ResourcesRepository`` stand-in. ``get_resource_by_id_for_caller``
    returns None for BOTH "no such row" and "not visible to this caller" —
    callers must not be able to tell them apart (no existence leak)."""

    def __init__(self, row=None):
        self.row = row
        self.calls: list[tuple[str, str]] = []

    async def get_resource_by_id_for_caller(self, resource_id, user_id):
        self.calls.append((resource_id, user_id))
        return self.row


class _FakeCaptionService:
    instances: list["_FakeCaptionService"] = []

    result: object = {"en": "a rooftop at dusk", "zh": "黄昏的屋顶"}
    raises: BaseException | None = None

    def __init__(self, provider_key="", provider_config=None, agent_slug="caption"):
        self.provider_key = provider_key
        self.provider_config = provider_config
        self.agent_slug = agent_slug
        self.calls: list[dict] = []
        _FakeCaptionService.instances.append(self)

    async def caption(self, **kw):
        self.calls.append(kw)
        if type(self).raises is not None:
            raise type(self).raises
        return type(self).result


class _FakeAgentRepo:
    paused_reason = None
    slugs: list[str] = []

    async def get_by_slug(self, slug):
        _FakeAgentRepo.slugs.append(slug)
        return {"slug": slug, "paused_reason": _FakeAgentRepo.paused_reason}


@pytest.fixture
def wired(monkeypatch):
    """Wire every collaborator; hand the test back the fake repo so it can
    swap the row. Defaults describe the HAPPY path."""
    _FakeCaptionService.instances = []
    _FakeCaptionService.result = {"en": "a rooftop at dusk", "zh": "黄昏的屋顶"}
    _FakeCaptionService.raises = None
    _FakeAgentRepo.paused_reason = None
    _FakeAgentRepo.slugs = []

    repo = _FakeRepo(dict(IMAGE_ROW))
    monkeypatch.setattr(resources_repo_mod, "ResourcesRepository", lambda: repo)

    async def _source(resource):
        return resource.get("file_path")

    async def _gate(resource):
        return "Only image and video resources can be reverse-engineered"

    monkeypatch.setattr(caption_source_mod, "resolve_caption_source", _source)
    monkeypatch.setattr(caption_source_mod, "caption_gate_reason", _gate)

    async def _resolve(user_id, task, default_slug):
        return _ResolvedConfig()

    monkeypatch.setattr(helpers_mod, "resolve_task_ai_config", _resolve)

    @asynccontextmanager
    async def _materialize(path):
        yield Path("/tmp/materialized/") / str(path).rsplit("/", 1)[-1]

    monkeypatch.setattr(media_storage_mod, "materialize", _materialize)
    monkeypatch.setattr(caption_pkg, "CaptionService", _FakeCaptionService)
    monkeypatch.setattr(agent_repo_mod, "get_agent_repository", _FakeAgentRepo)
    return repo


# ── happy path ─────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_returns_both_sides_and_threads_the_resolved_config(wired):
    out = await caption_resource_for_caller(RESOURCE, USER)
    assert out == {"en": "a rooftop at dusk", "zh": "黄昏的屋顶"}
    assert wired.calls == [(RESOURCE, USER)], "read PER CALLER, not unscoped"

    svc = _FakeCaptionService.instances[0]
    assert svc.provider_key == "qwen" and svc.agent_slug == "caption"
    assert svc.provider_config == {"model": "qwen-vl-max", "api_key": "k"}
    call = svc.calls[0]
    assert call["fallback_models"] == ["doubao-pro"], (
        "the fallback pool is the whole reason resolve_task_ai_config is used "
        "instead of the 4-field tuple shim — dropping it silently disables "
        "failover (spec 2026-08-12-batch-fallback-rollout §1-F1)"
    )
    assert call["user_id"] == USER and call["resource_id"] == RESOURCE
    assert call["file_path"].endswith("sang-yao-sheet.png")


@pytest.mark.asyncio
async def test_a_single_side_is_enough(wired):
    _FakeCaptionService.result = {"en": "  a rooftop at dusk  ", "zh": "   "}
    out = await caption_resource_for_caller(RESOURCE, USER)
    assert out == {"en": "a rooftop at dusk"}, (
        "whitespace-only is not a prompt — writing it would blank the column "
        "the run was supposed to fill"
    )


@pytest.mark.asyncio
async def test_the_file_is_materialized_before_the_agent_reads_it(wired):
    """The stored value may be an ``sb://`` key, not a local path. Handing it
    to the image encoder unmaterialized is the object-store failure mode."""
    await caption_resource_for_caller(RESOURCE, USER)
    assert (
        _FakeCaptionService.instances[0]
        .calls[0]["file_path"]
        .startswith("/tmp/materialized/")
    )


# ── branch 1: the row is not visible ───────────────────────────────────────


@pytest.mark.asyncio
async def test_invisible_row_is_caption_source_unavailable(wired):
    wired.row = None
    with pytest.raises(CaptionSourceUnavailable):
        await caption_resource_for_caller(RESOURCE, USER)
    assert not _FakeCaptionService.instances, "no provider call for a missing row"


# ── branch 2: nothing to look at ───────────────────────────────────────────


@pytest.mark.asyncio
async def test_no_source_raises_with_the_gates_own_words(wired, monkeypatch):
    async def _none(resource):
        return None

    async def _album_reason(resource):
        return "Albums are reverse-engineered one slide at a time"

    monkeypatch.setattr(caption_source_mod, "resolve_caption_source", _none)
    monkeypatch.setattr(caption_source_mod, "caption_gate_reason", _album_reason)

    with pytest.raises(CaptionSourceUnavailable) as ei:
        await caption_resource_for_caller(RESOURCE, USER)
    assert "one slide at a time" in str(ei.value), (
        "caption_gate_reason says something DIFFERENT per branch (album / "
        "unsupported / video with no cover); a generic message here throws "
        "away the only actionable part"
    )
    assert not _FakeCaptionService.instances


@pytest.mark.asyncio
async def test_no_source_and_no_gate_reason_still_says_something(wired, monkeypatch):
    async def _none(resource):
        return None

    monkeypatch.setattr(caption_source_mod, "resolve_caption_source", _none)
    monkeypatch.setattr(caption_source_mod, "caption_gate_reason", _none)

    with pytest.raises(CaptionSourceUnavailable) as ei:
        await caption_resource_for_caller(RESOURCE, USER)
    assert str(ei.value), "an empty reason would surface as a blank error body"


# ── branch 3: the agent ran and produced nothing ───────────────────────────


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "result", [None, {}, {"en": "", "zh": None}, {"prompt_json": {"a": 1}}]
)
async def test_unusable_result_is_caption_agent_failed(wired, result):
    _FakeCaptionService.result = result
    with pytest.raises(CaptionAgentFailed):
        await caption_resource_for_caller(RESOURCE, USER)


# ── branch 4: the agent is paused (an empty result in disguise) ────────────


@pytest.mark.asyncio
async def test_paused_agent_is_its_own_exception_not_a_vision_problem(wired):
    _FakeCaptionService.result = None
    _FakeAgentRepo.paused_reason = "budget"
    with pytest.raises(CaptionAgentPaused) as ei:
        await caption_resource_for_caller(RESOURCE, USER)
    assert "budget" in str(ei.value), "the reason ('budget'/'manual') is the action"
    assert _FakeAgentRepo.slugs == ["caption"], "asked about the RESOLVED agent"


@pytest.mark.asyncio
async def test_the_agent_row_is_only_consulted_on_the_empty_path(wired):
    """A successful caption must not pay for an extra agent lookup."""
    await caption_resource_for_caller(RESOURCE, USER)
    assert _FakeAgentRepo.slugs == []


@pytest.mark.asyncio
async def test_an_agent_lookup_failure_is_not_read_as_a_pause(wired, monkeypatch):
    """``get_by_slug`` logs and returns None on error. Treating that as "paused"
    would invent a pause nobody set and send the user to resume an agent that
    is already running."""

    class _BrokenAgentRepo:
        async def get_by_slug(self, slug):
            return None

    monkeypatch.setattr(agent_repo_mod, "get_agent_repository", _BrokenAgentRepo)
    _FakeCaptionService.result = None
    with pytest.raises(CaptionAgentFailed):
        await caption_resource_for_caller(RESOURCE, USER)


# ── branch 5: provider failures propagate raw ──────────────────────────────


@pytest.mark.asyncio
@pytest.mark.parametrize("exc_name", ["AllModelsFailed", "LLMCallError"])
async def test_provider_failures_propagate_unwrapped(wired, exc_name):
    """The ops layer deliberately does NOT normalize these: the resources
    router hands them to the typed provider surface, the assets service turns
    them into a 503 with the provider's own message. Wrapping them here would
    take that choice away from both callers."""
    from app.services.ai.llm.llm_fallback_chain import AllModelsFailed
    from app.services.ai.llm.llm_retry_middleware import LLMCallError

    exc_type = {"AllModelsFailed": AllModelsFailed, "LLMCallError": LLMCallError}[
        exc_name
    ]
    _FakeCaptionService.raises = exc_type("qwen-vl-max: 401 unauthorized")
    with pytest.raises(exc_type) as ei:
        await caption_resource_for_caller(RESOURCE, USER)
    assert "401 unauthorized" in str(ei.value)
