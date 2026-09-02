"""``capabilities_for_model`` — one model name → one provider's capabilities.

The lookup the asset bundle uses, pinned against the SAME seams
``generation-capabilities`` is pinned against: the catalog repository (with its
``actual_provider`` leak tripwire modelled honestly) and the user's Settings
platform-model gate. The service-level bundle tests stub this function whole, so
this file is the only place that proves a hidden model is actually refused.

Three failures it exists to make loud:

* a model the picker HIDES coming back with capabilities — the bundle would
  then trim for a provider the user cannot choose;
* a model that IS visible but whose protocol does not resolve coming back as
  ``None`` — "you may not use this" and "this provider takes no references" are
  different answers and must not share one;
* a chat row answering at all — an llm has no reference ceiling to report.
"""

from __future__ import annotations

import importlib
from types import SimpleNamespace

import pytest

from app.services.ai.provider_protocols.base import ProviderCapabilities
from app.services.generation.model_capabilities import (
    capabilities_for_model,
    visible_generation_rows,
)

USER = "11111111-1111-1111-1111-111111111111"

_ROWS = [
    {"name": "codex-local-image", "type": "image", "actual_provider": "codex-local"},
    {"name": "ark-t2i", "type": "image", "actual_provider": "ark"},
    {"name": "made-up-provider", "type": "image", "actual_provider": "nope"},
    {"name": "some-chat-model", "type": "llm", "actual_provider": "deepseek"},
]


def _catalog(monkeypatch, rows=_ROWS) -> None:
    """Stub the catalog repository, honouring its REAL projection.

    ``list_enabled`` pops ``actual_provider`` unless the caller opts in (the
    2026-08-14 leak tripwire). A stub that always returned the field would let
    a lookup that forgot ``include_actual_provider=True`` pass here and resolve
    every model to ``none()`` in production — the exact prod incident of
    2026-08-30.
    """
    from app.repositories import mediahub_model_repository as repo_mod

    async def _list_enabled(
        type_filter=None, viewer_user_id=None, include_actual_provider=False
    ):
        out = []
        for r in rows:
            row = dict(r)
            provider = row.pop("actual_provider", None)
            row["is_local"] = provider in ("codex-local", "jimeng-local")
            if include_actual_provider:
                row["actual_provider"] = provider
            out.append(row)
        return out

    repo = SimpleNamespace(list_enabled=_list_enabled)
    monkeypatch.setattr(repo_mod, "get_mediahub_model_repository", lambda: repo)


def _gate(monkeypatch, *, allowed: bool = True, disabled: frozenset = frozenset()):
    async def _fake_gate(user_id):
        return allowed, disabled

    monkeypatch.setattr(
        "app.services.ai.platform_model_visibility.platform_model_gate", _fake_gate
    )


@pytest.mark.asyncio
async def test_a_visible_model_answers_its_protocols_capabilities(monkeypatch):
    _catalog(monkeypatch)
    _gate(monkeypatch)

    caps = await capabilities_for_model("codex-local-image", USER)

    assert caps is not None and caps.max_refs == 9


@pytest.mark.asyncio
async def test_a_text_to_image_only_provider_answers_zero_not_none(monkeypatch):
    """ark declares ``max_refs=0``. That is an ANSWER — the bundle drops every
    reference with ``provider_no_refs`` — not a missing model."""
    _catalog(monkeypatch)
    _gate(monkeypatch)

    caps = await capabilities_for_model("ark-t2i", USER)

    assert caps is not None and caps.max_refs == 0


@pytest.mark.asyncio
async def test_a_visible_model_with_no_protocol_is_the_restrictive_default(monkeypatch):
    _catalog(monkeypatch)
    _gate(monkeypatch)

    caps = await capabilities_for_model("made-up-provider", USER)

    assert caps is ProviderCapabilities.none()


@pytest.mark.asyncio
async def test_an_unknown_name_is_none(monkeypatch):
    _catalog(monkeypatch)
    _gate(monkeypatch)

    assert await capabilities_for_model("no-such-model", USER) is None


@pytest.mark.asyncio
async def test_a_blank_name_is_none_without_touching_the_catalog(monkeypatch):
    _gate(monkeypatch)
    # No catalog stub at all: a lookup that queried anyway would blow up here
    # rather than answering an empty string.
    assert await capabilities_for_model("", USER) is None


@pytest.mark.asyncio
async def test_a_model_the_settings_gate_hides_is_none(monkeypatch):
    """The binding one. A blacklisted model must be refused, not bundled — the
    picker does not offer it, so a bundle for it is a trim nobody can explain.
    """
    _catalog(monkeypatch)
    _gate(monkeypatch, disabled=frozenset({"codex-local-image"}))

    assert await capabilities_for_model("codex-local-image", USER) is None


@pytest.mark.asyncio
async def test_a_chat_row_never_answers(monkeypatch):
    _catalog(monkeypatch)
    _gate(monkeypatch)

    assert await capabilities_for_model("some-chat-model", USER) is None


@pytest.mark.asyncio
async def test_visibility_is_the_same_row_set_the_picker_reads(monkeypatch):
    """``visible_generation_rows`` is what ``canvases_router`` delegates to, so
    the picker and the bundle cannot disagree about which models exist."""
    _catalog(monkeypatch)
    _gate(monkeypatch, disabled=frozenset({"ark-t2i"}))

    names = {r["name"] for r in await visible_generation_rows(USER)}

    assert names == {"codex-local-image", "made-up-provider"}


@pytest.mark.asyncio
async def test_the_router_helper_delegates_rather_than_re_deriving(monkeypatch):
    """The whole reason the predicate moved: two hand-rolled copies of
    "enabled + Settings + image/video" drift silently.

    The gate is stubbed with a NON-EMPTY blacklist on purpose. With an empty
    one, both sides answer the full catalog and a router that re-derived the
    rows while forgetting the Settings filter entirely would still compare
    equal — the assertion would be true for a reason that has nothing to do
    with delegation. One disabled model makes the two answers differ unless
    the router really is calling the shared predicate.
    """
    # ``from app.api import canvases_router`` hands back the APIRouter OBJECT
    # (``app/api/__init__.py`` re-exports it), not the module — the same trap
    # ``tests/test_canvas_asset_refs_routes.py`` documents.
    canvases_router = importlib.import_module("app.api.canvases_router")

    _catalog(monkeypatch)
    _gate(monkeypatch, disabled=frozenset({"ark-t2i"}))

    from_router = await canvases_router._visible_generation_rows(USER)
    from_service = await visible_generation_rows(USER)

    assert from_router == from_service
    # The blacklist really bit — otherwise the equality above compares two
    # unfiltered lists and proves nothing about the Settings half.
    assert "ark-t2i" not in {r["name"] for r in from_service}
    assert "codex-local-image" in {r["name"] for r in from_service}
