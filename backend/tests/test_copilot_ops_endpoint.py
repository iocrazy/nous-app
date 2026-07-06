"""Contract tests for the copilot free-text reconciler (Phase B P2, Task 7).

``POST /scenes/{scene_id}/copilot-ops`` turns a director's instruction into an
anchor-based element-op batch, dry-run-validated server-side. The load-bearing
guarantees (each a bug if broken):

1. Trust boundary — the endpoint NEVER returns un-validated LLM ops: it dry-runs
   ``apply_ops`` and retries ONCE with the OpError context before 422-ing.
2. Placeholder ids — model emits ``el_new_*``; the server rewrites them to real
   ``el_<hex>`` ids (consistently across anchors) before returning.
3. Stale read → proposal — ``read_version`` behind the scene's current version
   returns ``proposal: true`` with ``base_version`` = the CURRENT version.
4. Flag-dark — ``FEATURE_COPILOT_OPS`` off → plain 404 (existence hidden).
5. DB-governed provider config — ``resolve_script_provider_config(user_id)`` is
   awaited and its result threaded into ``ScriptAIService`` (a bare service()
   would fall back to the stale ENV key → 401 on prod).

The endpoint is called directly (mirrors test_scene_convert_dispatch.py):
ScriptAIService + the resolver are patched at their SOURCE modules, which the
endpoint imports lazily.
"""

from __future__ import annotations

import importlib
import json
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.core.deps import AuthContext
from app.schemas.script import CopilotOpsRequest

pytestmark = [pytest.mark.unit, pytest.mark.asyncio]

scenes_router = importlib.import_module("app.api.script_scenes_router")

_USER = "11111111-1111-1111-1111-111111111111"
_SCENE = "9000000000000000042"

# Sentinel resolver return — (provider_key, provider_config, model, agent_slug).
_RESOLVED = (
    "doubao",
    {"api_key": "k", "base_url": "u", "model": "m"},
    "m",
    "script_ai",
)


def _auth() -> AuthContext:
    return AuthContext(user_id=_USER, auth_type="jwt")


def _mock_scene(monkeypatch, *, elements, version):
    """Patch the repo so get_by_id returns a scene with the given content."""
    repo = MagicMock()
    repo.get_by_id = AsyncMock(
        return_value={"content_json": elements, "content_version": version}
    )
    monkeypatch.setattr(scenes_router, "get_script_scene_repository", lambda: repo)
    return repo


def _patch_service(ops_return):
    """Patch resolver + ScriptAIService at their source modules.

    ``ops_return`` is the dict (or list of dicts, one per attempt) that
    ``instruction_to_element_ops`` yields.
    """
    resolver = AsyncMock(return_value=_RESOLVED)
    instance = MagicMock()
    if isinstance(ops_return, list):
        instance.instruction_to_element_ops = AsyncMock(side_effect=ops_return)
    else:
        instance.instruction_to_element_ops = AsyncMock(return_value=ops_return)
    svc_cls = MagicMock(return_value=instance)
    return (
        patch(
            "app.services.ai.providers.ai_provider_helpers."
            "resolve_script_provider_config",
            resolver,
        ),
        patch(
            "app.services.storyboard.script.script_ai_service.ScriptAIService",
            svc_cls,
        ),
        resolver,
        svc_cls,
        instance,
    )


# ---------------------------------------------------------------------------
# 200 fresh — ops + base_version + summary; placeholder ids rewritten
# ---------------------------------------------------------------------------


async def test_fresh_returns_ops_with_placeholders_replaced(monkeypatch):
    monkeypatch.setattr(scenes_router.settings, "FEATURE_COPILOT_OPS", True)
    _mock_scene(
        monkeypatch,
        elements=[{"id": "el_a", "type": "action", "text": "He runs."}],
        version=3,
    )
    generated = {
        "ops": [
            {
                "op": "insert",
                "element_id": "el_new_1",
                "after_id": "el_a",
                "payload": {"type": "dialogue", "text": "Wait!"},
            }
        ],
        "summary": "Added a line of dialogue.",
    }
    resolver_p, svc_p, resolver, svc_cls, instance = _patch_service(generated)

    with resolver_p, svc_p:
        result = await scenes_router.copilot_ops(
            scene_id=_SCENE,
            auth=_auth(),
            body=CopilotOpsRequest(instruction="add a line", read_version=3),
        )

    assert result["success"] is True
    data = result["data"]
    assert data["base_version"] == 3
    assert data["summary"] == "Added a line of dialogue."
    assert "proposal" not in data  # read_version == current → fresh, not proposal

    op = data["ops"][0]
    # Placeholder rewritten to a real el_<8hex> id; anchor to an existing id kept.
    assert op["element_id"] != "el_new_1"
    assert op["element_id"].startswith("el_")
    assert not op["element_id"].startswith("el_new_")
    assert len(op["element_id"]) == 11  # "el_" + 8 hex
    assert op["after_id"] == "el_a"

    # Provider config pinned: resolver awaited + service built with its result.
    resolver.assert_awaited_once_with(_USER)
    svc_cls.assert_called_once_with(
        user_id=_USER,
        agent_slug="script_ai",
        provider_key="doubao",
        provider_config={"api_key": "k", "base_url": "u", "model": "m"},
    )
    instance.instruction_to_element_ops.assert_awaited_once()


# ---------------------------------------------------------------------------
# 200 proposal — read_version behind current → proposal:true, base_version=current
# ---------------------------------------------------------------------------


async def test_stale_read_version_returns_proposal(monkeypatch):
    monkeypatch.setattr(scenes_router.settings, "FEATURE_COPILOT_OPS", True)
    _mock_scene(
        monkeypatch,
        elements=[{"id": "el_a", "type": "action", "text": "He runs."}],
        version=5,
    )
    generated = {
        "ops": [
            {"op": "update", "element_id": "el_a", "payload": {"text": "He sprints."}}
        ],
        "summary": "Tightened the action.",
    }
    resolver_p, svc_p, *_ = _patch_service(generated)

    with resolver_p, svc_p:
        result = await scenes_router.copilot_ops(
            scene_id=_SCENE,
            auth=_auth(),
            body=CopilotOpsRequest(instruction="make it punchier", read_version=2),
        )

    assert result["success"] is True
    data = result["data"]
    assert data["proposal"] is True
    assert data["base_version"] == 5  # regenerated against CURRENT version


# ---------------------------------------------------------------------------
# 422 — dry-run failure retried once then rejected with the OpError code
# ---------------------------------------------------------------------------


async def test_dry_run_failure_retries_once_then_422(monkeypatch):
    monkeypatch.setattr(scenes_router.settings, "FEATURE_COPILOT_OPS", True)
    _mock_scene(
        monkeypatch,
        elements=[{"id": "el_a", "type": "action", "text": "He runs."}],
        version=1,
    )
    # Every generation references a non-existent anchor → apply_ops raises
    # missing_anchor both times.
    bad = {
        "ops": [
            {
                "op": "insert",
                "element_id": "el_new_1",
                "before_id": "el_ghost",
                "payload": {"type": "action", "text": "Nope."},
            }
        ],
        "summary": "should not surface",
    }
    resolver_p, svc_p, _resolver, _svc_cls, instance = _patch_service([bad, bad])

    with resolver_p, svc_p:
        result = await scenes_router.copilot_ops(
            scene_id=_SCENE,
            auth=_auth(),
            body=CopilotOpsRequest(instruction="do the thing", read_version=1),
        )

    assert result.status_code == 422
    payload = json.loads(result.body)
    assert payload["success"] is False
    assert payload["code"] == "missing_anchor"

    # Generated twice: initial attempt + one retry seeded with the error context.
    assert instance.instruction_to_element_ops.await_count == 2
    retry_kwargs = instance.instruction_to_element_ops.await_args_list[1].kwargs
    assert "missing_anchor" in (retry_kwargs.get("error_context") or "")


# ---------------------------------------------------------------------------
# Flag off → plain 404 (existence hidden), no LLM call
# ---------------------------------------------------------------------------


async def test_flag_off_returns_404(monkeypatch):
    monkeypatch.setattr(scenes_router.settings, "FEATURE_COPILOT_OPS", False)
    repo = _mock_scene(monkeypatch, elements=[], version=1)
    resolver_p, svc_p, resolver, svc_cls, _ = _patch_service({"ops": [], "summary": ""})

    from fastapi import HTTPException

    with resolver_p, svc_p:
        with pytest.raises(HTTPException) as exc_info:
            await scenes_router.copilot_ops(
                scene_id=_SCENE,
                auth=_auth(),
                body=CopilotOpsRequest(instruction="anything", read_version=1),
            )

    assert exc_info.value.status_code == 404
    repo.get_by_id.assert_not_awaited()
    resolver.assert_not_awaited()
    svc_cls.assert_not_called()


async def test_scene_missing_returns_404(monkeypatch):
    monkeypatch.setattr(scenes_router.settings, "FEATURE_COPILOT_OPS", True)
    repo = MagicMock()
    repo.get_by_id = AsyncMock(return_value=None)
    monkeypatch.setattr(scenes_router, "get_script_scene_repository", lambda: repo)
    resolver_p, svc_p, *_ = _patch_service({"ops": [], "summary": ""})

    from fastapi import HTTPException

    with resolver_p, svc_p:
        with pytest.raises(HTTPException) as exc_info:
            await scenes_router.copilot_ops(
                scene_id=_SCENE,
                auth=_auth(),
                body=CopilotOpsRequest(instruction="anything", read_version=1),
            )

    assert exc_info.value.status_code == 404


# ---------------------------------------------------------------------------
# Guard wiring — the route declares verify_scene_access
# ---------------------------------------------------------------------------


def test_copilot_route_declares_scene_guard():
    from app.core.scope_guards import verify_scene_access

    def _flat(dependant):
        for dep in dependant.dependencies:
            yield dep.call
            yield from _flat(dep)

    route = next(
        r
        for r in scenes_router.router.routes
        if getattr(r, "path", "") == "/scenes/{scene_id}/copilot-ops"
    )
    assert verify_scene_access in set(_flat(route.dependant))


# ---------------------------------------------------------------------------
# Instruction length validation (Pydantic constr 1..2000)
# ---------------------------------------------------------------------------


def test_instruction_too_long_rejected():
    from pydantic import ValidationError

    with pytest.raises(ValidationError):
        CopilotOpsRequest(instruction="x" * 2001, read_version=1)


def test_instruction_empty_rejected():
    from pydantic import ValidationError

    with pytest.raises(ValidationError):
        CopilotOpsRequest(instruction="", read_version=1)
