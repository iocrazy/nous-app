"""Task 7 (workspace IA redesign spec §5) — node CONFIG vs. workflow/episode
ARRANGEMENT permission tightening.

Two write surfaces, deliberately split (see
``app.services.workflow.node_authz`` for the full rationale):

- **Node config** (``PATCH /{project_id}/workflow/nodes/{node_id}`` when the
  body touches owner/members/schedule/brief) — manager OR the node's
  episode's ``owner_id`` (Task 6). 403 ``node_config_forbidden`` otherwise.
  Non-config-only PATCHes (``skipped`` / ``form_data`` / ``depends_on``) keep
  today's WRITE_ROLES (manager/editor) behavior UNCHANGED — pinned below.
- **Arrangement** (node add/delete; episode create/delete) — manager only,
  full stop, no episode-owner carve-out. 403 ``arrangement_forbidden``.
- **Advance** (``POST /{project_id}/advance`` and friends) is explicitly
  OUT OF SCOPE for this task — its existing WRITE_ROLES gate in
  ``advance_service.py`` must not regress. Pinned below too.

Test tier: router handlers are called DIRECTLY (not through ASGI), mirroring
the established pattern in ``tests/test_workflow_node_router.py`` and
``tests/test_workflow_deps.py`` for this exact router — the FastAPI
``Depends`` guards are bypassed by passing ``None`` positionally for the
guard parameter, and ``resolve_effective_role`` / repository getters are
monkeypatched at their lazy-import seams.
"""

from __future__ import annotations

import importlib
from typing import Any, Dict, Optional
from uuid import uuid4

import pytest
from fastapi import HTTPException

import app.core.workflow_roles as roles_mod
import app.services.workflow.node_mutations as nm
from app.schemas.workflow import NodeCreate, NodePatch
from app.services.workflow import node_authz

projects_router = importlib.import_module("app.api.projects_router")
episodes_router = importlib.import_module("app.api.episodes_router")

pytestmark = pytest.mark.unit

PROJECT_ID = "100"
NODE_ID = "900"
EPISODE_ID = "9001"
USER_ID = "00000000-0000-0000-0000-000000000001"
OTHER_UUID = str(uuid4())


class _Auth:
    def __init__(self, user_id: str = USER_ID):
        self.user_id = user_id


_AUTH = _Auth()


def _role(role: Optional[str]):
    async def _fake(user_id, *, project_id=None, team_id=None):
        return role

    return _fake


# --------------------------------------------------------------------------- #
# Unit: node_authz.can_edit_node_config
# --------------------------------------------------------------------------- #


@pytest.mark.asyncio
async def test_can_edit_node_config_manager_true_even_without_episode(monkeypatch):
    monkeypatch.setattr(roles_mod, "resolve_effective_role", _role("manager"))
    assert await node_authz.can_edit_node_config(PROJECT_ID, None, USER_ID) is True


@pytest.mark.asyncio
async def test_can_edit_node_config_episode_owner_true(monkeypatch):
    monkeypatch.setattr(roles_mod, "resolve_effective_role", _role("editor"))

    class _FakeEpisodeRepo:
        async def get_by_id(self, episode_id):
            assert episode_id == EPISODE_ID
            return {"id": episode_id, "owner_id": USER_ID}

    monkeypatch.setattr(
        "app.repositories.episode_repository.get_episode_repository",
        lambda: _FakeEpisodeRepo(),
    )

    assert (
        await node_authz.can_edit_node_config(PROJECT_ID, EPISODE_ID, USER_ID) is True
    )


@pytest.mark.asyncio
async def test_can_edit_node_config_non_owner_non_manager_false(monkeypatch):
    monkeypatch.setattr(roles_mod, "resolve_effective_role", _role("editor"))

    class _FakeEpisodeRepo:
        async def get_by_id(self, episode_id):
            return {"id": episode_id, "owner_id": OTHER_UUID}

    monkeypatch.setattr(
        "app.repositories.episode_repository.get_episode_repository",
        lambda: _FakeEpisodeRepo(),
    )

    assert (
        await node_authz.can_edit_node_config(PROJECT_ID, EPISODE_ID, USER_ID) is False
    )


@pytest.mark.asyncio
async def test_can_edit_node_config_no_episode_no_manager_false(monkeypatch):
    monkeypatch.setattr(roles_mod, "resolve_effective_role", _role("editor"))
    assert await node_authz.can_edit_node_config(PROJECT_ID, None, USER_ID) is False


@pytest.mark.asyncio
async def test_can_edit_node_config_no_role_at_all_false(monkeypatch):
    monkeypatch.setattr(roles_mod, "resolve_effective_role", _role(None))
    assert await node_authz.can_edit_node_config(PROJECT_ID, None, USER_ID) is False


# --------------------------------------------------------------------------- #
# Unit: node_authz.require_arrangement_role
# --------------------------------------------------------------------------- #


@pytest.mark.asyncio
async def test_require_arrangement_role_manager_ok(monkeypatch):
    monkeypatch.setattr(roles_mod, "resolve_effective_role", _role("manager"))
    await node_authz.require_arrangement_role(PROJECT_ID, USER_ID)  # no raise


@pytest.mark.asyncio
async def test_require_arrangement_role_editor_forbidden(monkeypatch):
    """Editors could arrange nodes/episodes before this task; tightened to
    manager-only now."""
    monkeypatch.setattr(roles_mod, "resolve_effective_role", _role("editor"))
    with pytest.raises(HTTPException) as exc:
        await node_authz.require_arrangement_role(PROJECT_ID, USER_ID)
    assert exc.value.status_code == 403
    assert exc.value.detail == {"code": "arrangement_forbidden"}


# --------------------------------------------------------------------------- #
# Router: PATCH .../workflow/nodes/{node_id} — config fields
# --------------------------------------------------------------------------- #


def _fake_nodes_repo(monkeypatch, *, node: Optional[Dict[str, Any]], update_return=None):
    captured: Dict[str, Any] = {}

    class _FakeRepo:
        async def get_node(self, node_id, project_id=None):
            captured["get_node_called_with"] = (node_id, project_id)
            return node

        async def update_node(self, node_id, project_id, **kwargs):
            captured["update_node_called_with"] = (node_id, project_id, kwargs)
            if update_return is not None:
                return update_return
            return {"id": node_id, **kwargs}

    monkeypatch.setattr(
        "app.repositories.project_stage_nodes_repository."
        "get_project_stage_nodes_repository",
        lambda: _FakeRepo(),
    )
    return captured


@pytest.mark.asyncio
async def test_patch_node_manager_can_set_owner(monkeypatch):
    node = {"id": NODE_ID, "project_id": PROJECT_ID, "episode_id": EPISODE_ID}
    captured = _fake_nodes_repo(monkeypatch, node=node)
    monkeypatch.setattr(roles_mod, "resolve_effective_role", _role("manager"))

    res = await projects_router.patch_workflow_node(
        PROJECT_ID,
        NODE_ID,
        NodePatch(owner_user_id=OTHER_UUID),
        _AUTH,
        None,
    )
    assert res["success"] is True
    assert "update_node_called_with" in captured


@pytest.mark.asyncio
async def test_patch_node_episode_owner_can_set_owner(monkeypatch):
    """Task 6's episode owner may edit config fields even without a project
    role of manager/editor (per spec §5 the episode-owner carve-out doesn't
    need a project_members row)."""
    node = {"id": NODE_ID, "project_id": PROJECT_ID, "episode_id": EPISODE_ID}
    captured = _fake_nodes_repo(monkeypatch, node=node)
    monkeypatch.setattr(roles_mod, "resolve_effective_role", _role(None))

    class _FakeEpisodeRepo:
        async def get_by_id(self, episode_id):
            return {"id": episode_id, "owner_id": USER_ID}

    monkeypatch.setattr(
        "app.repositories.episode_repository.get_episode_repository",
        lambda: _FakeEpisodeRepo(),
    )

    res = await projects_router.patch_workflow_node(
        PROJECT_ID,
        NODE_ID,
        NodePatch(brief="New brief"),
        _AUTH,
        None,
    )
    assert res["success"] is True
    assert "update_node_called_with" in captured


@pytest.mark.asyncio
async def test_patch_node_plain_member_cannot_set_owner_403_node_config_forbidden(
    monkeypatch,
):
    node = {"id": NODE_ID, "project_id": PROJECT_ID, "episode_id": EPISODE_ID}
    captured = _fake_nodes_repo(monkeypatch, node=node)
    monkeypatch.setattr(roles_mod, "resolve_effective_role", _role("editor"))

    class _FakeEpisodeRepo:
        async def get_by_id(self, episode_id):
            return {"id": episode_id, "owner_id": OTHER_UUID}

    monkeypatch.setattr(
        "app.repositories.episode_repository.get_episode_repository",
        lambda: _FakeEpisodeRepo(),
    )

    with pytest.raises(HTTPException) as exc:
        await projects_router.patch_workflow_node(
            PROJECT_ID,
            NODE_ID,
            NodePatch(owner_user_id=OTHER_UUID),
            _AUTH,
            None,
        )
    assert exc.value.status_code == 403
    assert exc.value.detail == {"code": "node_config_forbidden"}
    assert "update_node_called_with" not in captured


@pytest.mark.asyncio
async def test_patch_node_config_missing_node_404(monkeypatch):
    _fake_nodes_repo(monkeypatch, node=None)
    monkeypatch.setattr(roles_mod, "resolve_effective_role", _role("manager"))

    with pytest.raises(HTTPException) as exc:
        await projects_router.patch_workflow_node(
            PROJECT_ID,
            NODE_ID,
            NodePatch(brief="x"),
            _AUTH,
            None,
        )
    assert exc.value.status_code == 404


# --------------------------------------------------------------------------- #
# Router: PATCH .../workflow/nodes/{node_id} — non-config fields UNCHANGED
# --------------------------------------------------------------------------- #


@pytest.mark.asyncio
async def test_patch_node_skipped_only_editor_still_allowed_unchanged(monkeypatch):
    """Pin: today's WRITE_ROLES (manager/editor) behavior for the non-config
    'skipped' field must not regress — no episode/get_node lookup either
    (the fake repo below has no ``get_node`` method at all, so calling it
    would AttributeError and fail the test)."""
    captured: Dict[str, Any] = {}

    class _FakeRepoNoGetNode:
        async def update_node(self, node_id, project_id, **kwargs):
            captured["update_node_called_with"] = (node_id, project_id, kwargs)
            return {"id": node_id, **kwargs}

    monkeypatch.setattr(
        "app.repositories.project_stage_nodes_repository."
        "get_project_stage_nodes_repository",
        lambda: _FakeRepoNoGetNode(),
    )
    monkeypatch.setattr(roles_mod, "resolve_effective_role", _role("editor"))

    res = await projects_router.patch_workflow_node(
        PROJECT_ID,
        NODE_ID,
        NodePatch(skipped=True),
        _AUTH,
        None,
    )
    assert res["success"] is True
    assert captured["update_node_called_with"][2]["skipped"] is True


@pytest.mark.asyncio
async def test_patch_node_skipped_only_viewer_still_403_insufficient_role(monkeypatch):
    monkeypatch.setattr(roles_mod, "resolve_effective_role", _role("viewer"))

    with pytest.raises(HTTPException) as exc:
        await projects_router.patch_workflow_node(
            PROJECT_ID,
            NODE_ID,
            NodePatch(skipped=True),
            _AUTH,
            None,
        )
    assert exc.value.status_code == 403


# --------------------------------------------------------------------------- #
# Router: node add/delete — tightened to manager-only (arrangement_forbidden)
# --------------------------------------------------------------------------- #


@pytest.mark.asyncio
async def test_add_node_manager_ok(monkeypatch):
    monkeypatch.setattr(roles_mod, "resolve_effective_role", _role("manager"))

    async def _add(project_id, **kwargs):
        return {"id": "new", "name": "Voiceover", **kwargs}

    monkeypatch.setattr(nm, "add_project_node", _add)
    res = await projects_router.add_workflow_node(
        PROJECT_ID, NodeCreate(source_stage_id="55", sort_order=2), _AUTH, None
    )
    assert res["success"] is True


@pytest.mark.asyncio
async def test_add_node_editor_now_forbidden_arrangement(monkeypatch):
    """Editors could add nodes before this task; tightened to manager-only."""
    monkeypatch.setattr(roles_mod, "resolve_effective_role", _role("editor"))
    with pytest.raises(HTTPException) as exc:
        await projects_router.add_workflow_node(
            PROJECT_ID, NodeCreate(source_stage_id="55", sort_order=2), _AUTH, None
        )
    assert exc.value.status_code == 403
    assert exc.value.detail == {"code": "arrangement_forbidden"}


@pytest.mark.asyncio
async def test_delete_node_manager_ok(monkeypatch):
    monkeypatch.setattr(roles_mod, "resolve_effective_role", _role("manager"))

    async def _del(project_id, node_id):
        return True

    monkeypatch.setattr(nm, "delete_project_node", _del)
    res = await projects_router.delete_workflow_node(PROJECT_ID, NODE_ID, _AUTH, None)
    assert res == {"success": True, "data": {"deleted": True}}


@pytest.mark.asyncio
async def test_delete_node_plain_member_forbidden_arrangement(monkeypatch):
    monkeypatch.setattr(roles_mod, "resolve_effective_role", _role("editor"))
    with pytest.raises(HTTPException) as exc:
        await projects_router.delete_workflow_node(PROJECT_ID, NODE_ID, _AUTH, None)
    assert exc.value.status_code == 403
    assert exc.value.detail == {"code": "arrangement_forbidden"}


# --------------------------------------------------------------------------- #
# Router: PATCH /episodes/{episode_id} reorder (sort_order) — 修复轮1
# (Task 7 code review Critical): the workspace Episodes panel's Move up/down
# drives this exact endpoint via a pair of sort_order swaps, so it's a real
# ARRANGEMENT surface — tightened to manager-only, same as owner_id.
# --------------------------------------------------------------------------- #


def _fake_episode_repo_for_update(monkeypatch, *, episode, update_return=None):
    captured: Dict[str, Any] = {}

    class _FakeEpisodeRepo:
        async def get_by_id(self, episode_id):
            captured["get_by_id_called_with"] = episode_id
            return episode

        async def update(self, episode_id, data):
            captured["update_called_with"] = (episode_id, data)
            if update_return is not None:
                return update_return
            return {"id": episode_id, **data}

    monkeypatch.setattr(
        episodes_router, "get_episode_repository", lambda: _FakeEpisodeRepo()
    )
    return captured


@pytest.mark.asyncio
async def test_update_episode_title_only_no_role_check_unchanged(monkeypatch):
    """Pin: title-only PATCH must not consult resolve_effective_role at all
    (neither the owner_id nor the new sort_order gate) — pre-existing
    verify_episode_write_access behavior, untouched by 修复轮1."""
    from app.schemas.script import EpisodeUpdate

    episode = {"id": EPISODE_ID, "project_id": PROJECT_ID}
    captured = _fake_episode_repo_for_update(monkeypatch, episode=episode)

    def _fail_if_called(*a, **k):
        raise AssertionError("resolve_effective_role must not be called")

    monkeypatch.setattr(roles_mod, "resolve_effective_role", _fail_if_called)

    res = await episodes_router.update_episode(
        EPISODE_ID, _AUTH, EpisodeUpdate(title="Renamed"), None
    )
    assert res["success"] is True
    assert "get_by_id_called_with" not in captured
    _, update_data = captured["update_called_with"]
    assert update_data == {"title": "Renamed"}


@pytest.mark.asyncio
async def test_update_episode_sort_order_manager_ok(monkeypatch):
    from app.schemas.script import EpisodeUpdate

    episode = {"id": EPISODE_ID, "project_id": PROJECT_ID}
    captured = _fake_episode_repo_for_update(monkeypatch, episode=episode)
    monkeypatch.setattr(roles_mod, "resolve_effective_role", _role("manager"))

    res = await episodes_router.update_episode(
        EPISODE_ID, _AUTH, EpisodeUpdate(sort_order=2), None
    )
    assert res["success"] is True
    _, update_data = captured["update_called_with"]
    assert update_data["sort_order"] == 2


@pytest.mark.asyncio
async def test_update_episode_sort_order_plain_member_403_arrangement_forbidden(
    monkeypatch,
):
    from app.schemas.script import EpisodeUpdate

    episode = {"id": EPISODE_ID, "project_id": PROJECT_ID}
    captured = _fake_episode_repo_for_update(monkeypatch, episode=episode)
    monkeypatch.setattr(roles_mod, "resolve_effective_role", _role("editor"))

    with pytest.raises(HTTPException) as exc:
        await episodes_router.update_episode(
            EPISODE_ID, _AUTH, EpisodeUpdate(sort_order=2), None
        )
    assert exc.value.status_code == 403
    assert exc.value.detail == {"code": "arrangement_forbidden"}
    assert "update_called_with" not in captured


@pytest.mark.asyncio
async def test_update_episode_owner_id_and_sort_order_together_resolves_role_once(
    monkeypatch,
):
    """When a single request touches BOTH manager-gated fields,
    resolve_effective_role must run exactly once, not once per field."""
    from app.schemas.script import EpisodeUpdate

    episode = {"id": EPISODE_ID, "project_id": PROJECT_ID}

    class _FakeEpisodeRepo:
        async def get_by_id(self, episode_id):
            return episode

        async def update(self, episode_id, data):
            return {"id": episode_id, **data}

    monkeypatch.setattr(
        episodes_router, "get_episode_repository", lambda: _FakeEpisodeRepo()
    )

    call_count = {"n": 0}

    async def _counting_role(user_id, *, project_id=None, team_id=None):
        call_count["n"] += 1
        return "manager"

    monkeypatch.setattr(roles_mod, "resolve_effective_role", _counting_role)

    res = await episodes_router.update_episode(
        EPISODE_ID,
        _AUTH,
        EpisodeUpdate(sort_order=2, owner_id=OTHER_UUID),
        None,
    )
    assert res["success"] is True
    assert call_count["n"] == 1


# --------------------------------------------------------------------------- #
# Router: episode create/delete — tightened to manager-only
# --------------------------------------------------------------------------- #


@pytest.mark.asyncio
async def test_create_episode_manager_ok(monkeypatch):
    from app.schemas.script import EpisodeCreate

    monkeypatch.setattr(roles_mod, "resolve_effective_role", _role("manager"))

    class _FakeEpisodeRepo:
        async def create(self, data):
            return {"id": "9999", **data}

    # episodes_router imports get_episode_repository at module top level (not
    # a lazy per-call import like projects_router's node repos), so the name
    # bound in the router's own namespace must be patched, not the source.
    monkeypatch.setattr(
        episodes_router, "get_episode_repository", lambda: _FakeEpisodeRepo()
    )

    async def _noop_instantiate(*args, **kwargs):
        return None

    monkeypatch.setattr(
        "app.services.workflow.instantiation.maybe_instantiate_episode_workflow",
        _noop_instantiate,
    )

    res = await episodes_router.create_episode(
        PROJECT_ID, _AUTH, EpisodeCreate(title="Ep 2"), None
    )
    assert res["success"] is True


@pytest.mark.asyncio
async def test_create_episode_plain_member_forbidden_arrangement(monkeypatch):
    from app.schemas.script import EpisodeCreate

    monkeypatch.setattr(roles_mod, "resolve_effective_role", _role("editor"))

    with pytest.raises(HTTPException) as exc:
        await episodes_router.create_episode(
            PROJECT_ID, _AUTH, EpisodeCreate(title="Ep 2"), None
        )
    assert exc.value.status_code == 403
    assert exc.value.detail == {"code": "arrangement_forbidden"}


@pytest.mark.asyncio
async def test_delete_episode_manager_ok(monkeypatch):
    monkeypatch.setattr(roles_mod, "resolve_effective_role", _role("manager"))

    class _FakeEpisodeRepo:
        async def get_by_id(self, episode_id):
            return {"id": episode_id, "project_id": PROJECT_ID}

        async def delete(self, episode_id):
            return True

    monkeypatch.setattr(
        episodes_router, "get_episode_repository", lambda: _FakeEpisodeRepo()
    )

    res = await episodes_router.delete_episode(EPISODE_ID, _AUTH, None)
    assert res == {"success": True}


@pytest.mark.asyncio
async def test_delete_episode_plain_member_forbidden_arrangement(monkeypatch):
    monkeypatch.setattr(roles_mod, "resolve_effective_role", _role("editor"))

    class _FakeEpisodeRepo:
        async def get_by_id(self, episode_id):
            return {"id": episode_id, "project_id": PROJECT_ID}

        async def delete(self, episode_id):
            raise AssertionError("delete must not be called when forbidden")

    monkeypatch.setattr(
        episodes_router, "get_episode_repository", lambda: _FakeEpisodeRepo()
    )

    with pytest.raises(HTTPException) as exc:
        await episodes_router.delete_episode(EPISODE_ID, _AUTH, None)
    assert exc.value.status_code == 403
    assert exc.value.detail == {"code": "arrangement_forbidden"}


@pytest.mark.asyncio
async def test_delete_episode_missing_404(monkeypatch):
    class _FakeEpisodeRepo:
        async def get_by_id(self, episode_id):
            return None

    monkeypatch.setattr(
        episodes_router, "get_episode_repository", lambda: _FakeEpisodeRepo()
    )

    with pytest.raises(HTTPException) as exc:
        await episodes_router.delete_episode(EPISODE_ID, _AUTH, None)
    assert exc.value.status_code == 404


# --------------------------------------------------------------------------- #
# CAUTION pin: advance role gate is OUT OF SCOPE and must be unchanged —
# a member (editor) still passes the WRITE_ROLES gate in advance_service.py,
# untouched by this task's manager-only arrangement tightening.
# --------------------------------------------------------------------------- #


@pytest.mark.asyncio
async def test_advance_gate_unchanged_editor_not_blocked_by_role(monkeypatch):
    import app.services.workflow.advance_service as advance_service
    from app.schemas.workflow import BLOCK_NOT_MANAGER_OR_EDITOR

    monkeypatch.setattr(advance_service, "resolve_effective_role", _role("editor"))

    class _FakeNodesRepo:
        async def list_nodes_by_episode(self, project_id, episode_id):
            return []

    class _FakeEpisodesRepo:
        async def get_by_id(self, episode_id):
            return {"current_node_id": None}

        async def set_current_node_id(self, episode_id, node_id):
            return None

    monkeypatch.setattr(
        "app.repositories.project_stage_nodes_repository."
        "get_project_stage_nodes_repository",
        lambda: _FakeNodesRepo(),
    )
    monkeypatch.setattr(
        "app.repositories.episode_repository.get_episode_repository",
        lambda: _FakeEpisodesRepo(),
    )

    preview = await advance_service.compute_advance_preview(
        PROJECT_ID, USER_ID, "forward", episode_id=EPISODE_ID
    )

    # Whatever the outcome (empty node list => BLOCK_NO_NEXT most likely),
    # it must NOT be the role-based block — that's the regression this pins.
    assert preview.blocked_reason != BLOCK_NOT_MANAGER_OR_EDITOR
