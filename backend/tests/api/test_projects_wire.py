"""``/projects`` wire parity after the router gained response models (P2).

Every JSON route is driven over real HTTP against the real app. The layer
below the handler is stubbed to return rows built from the ORM mapper
(``sample_orm`` → the repository's own ``_row`` / ``_node_row`` / …), so each
row carries every column in its real native type. The body must equal what
FastAPI sent for the bare dict — see ``tests/api/wire_parity.py``.
"""

from __future__ import annotations

import importlib
import uuid
from typing import Any

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from sqlalchemy import inspect

from app.core.deps import AuthContext, get_auth
from app.core.scope_guards import (
    verify_project_read_access,
    verify_project_write_access,
)
from app.main import app
from app.models import (
    FileVersions,
    GeneratedMedia,
    Issues,
    ProjectCollections,
    ProjectFileComments,
    ProjectFiles,
    ProjectFolders,
    ProjectMembers,
    Projects,
    ProjectStageHistory,
    ProjectStageNodeMembers,
    ProjectStageNodes,
    ProjectStages,
    ProjectStyleProfile,
    Shares,
)
from app.repositories.issue_repository import IssueRepository
from app.repositories.issue_repository import _row as issue_row
from app.repositories.project_stage_nodes_repository import (
    ProjectStageNodesRepository,
)
from app.repositories.project_stages_repository import ProjectStagesRepository
from app.repositories.projects_repository import ProjectsRepository
from app.schemas import project_responses as pr
from app.schemas.workflow import AdvanceNodeRef, AdvancePreview
from app.services.library.projects_service import ProjectsService
from tests.api.wire_parity import (
    SAMPLE_BIGINT,
    SAMPLE_TS,
    assert_wire_unchanged,
    column_names,
    sample_orm,
    sample_row,
)

r = importlib.import_module("app.api.projects_router")
repo_mod = importlib.import_module("app.repositories.projects_repository")
nodes_mod = importlib.import_module("app.repositories.project_stage_nodes_repository")
stages_mod = importlib.import_module("app.repositories.project_stages_repository")
style_mod = importlib.import_module("app.repositories.project_style_profile_repository")
gm_repo_mod = importlib.import_module("app.repositories.generated_media_repository")

USER = "00000000-0000-0000-0000-000000000042"
PID = SAMPLE_BIGINT + 7


def _nullable_overrides(model: Any) -> dict[str, Any]:
    """Attribute-key overrides setting every nullable column to None."""
    return {
        prop.key: None
        for prop in inspect(model).column_attrs
        if prop.columns[0].nullable and not prop.columns[0].primary_key
    }


def _full(model: Any, *, nulls: bool = False, **overrides: Any) -> Any:
    values = _nullable_overrides(model) if nulls else {}
    values.update(overrides)
    return sample_orm(model, **values)


def project_row(*, nulls: bool = False, **overrides: Any) -> dict:
    values = {"id": PID, "owner_id": uuid.UUID(USER), **overrides}
    obj = _full(Projects, nulls=nulls, **values)
    return repo_mod._row(obj, repo_mod._PROJECTS_N2A)


def file_row(*, nulls: bool = False, **overrides: Any) -> dict:
    values = {"project_id": PID, **overrides}
    return repo_mod._row(
        _full(ProjectFiles, nulls=nulls, **values), repo_mod._FILES_N2A
    )


def node_row(*, nulls: bool = False, **overrides: Any) -> dict:
    values = {
        "project_id": PID,
        "form_schema": [{"key": "k", "label": "Label", "type": "text"}],
        # A real value: the column is CHECK-constrained (mig 380) and the
        # response models declare it as NodeStatus.
        "status": "in_review",
        **overrides,
    }
    obj = _full(ProjectStageNodes, nulls=nulls, **values)
    member = nodes_mod._member_row(sample_orm(ProjectStageNodeMembers))
    return nodes_mod._node_row(obj, [member], ["11", "12"])


def _envelope(data: Any) -> dict:
    return {"success": True, "data": data}


def _patch(monkeypatch, owner: Any, name: str, value: Any) -> None:
    """Replace ``owner.name`` with an async function returning ``value`` (or
    calling it, when callable)."""

    async def _fake(*args, **kwargs):
        return value(*args, **kwargs) if callable(value) else value

    monkeypatch.setattr(owner, name, _fake)


async def _fake_auth() -> AuthContext:
    return AuthContext(user_id=USER, auth_type="jwt")


async def _allow() -> None:
    return None


@pytest.fixture(autouse=True)
def _overrides():
    module_gate = r.router.dependencies[0].dependency
    app.dependency_overrides[get_auth] = _fake_auth
    app.dependency_overrides[verify_project_read_access] = _allow
    app.dependency_overrides[verify_project_write_access] = _allow
    app.dependency_overrides[module_gate] = _allow
    yield
    for dep in (
        get_auth,
        verify_project_read_access,
        verify_project_write_access,
        module_gate,
    ):
        app.dependency_overrides.pop(dep, None)


@pytest_asyncio.fixture
async def client() -> AsyncClient:
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        yield ac


BASE = f"/api/v1/projects/{PID}"


# ── row models declare every column (+ enrichment) ─────────────────────────


@pytest.mark.parametrize(
    "model, orm, extra",
    [
        (pr.ProjectRow, Projects, set()),
        (pr.ProjectDetail, Projects, {"file_count", "effective_role"}),
        (
            pr.ProjectListItem,
            Projects,
            {
                "file_count",
                "current_stage",
                "members_preview",
                "latest_activity",
                "workflow_badge",
            },
        ),
        (pr.ProjectFileRow, ProjectFiles, set()),
        (pr.ProjectFileListRow, ProjectFiles, {"source_issue_identifier"}),
        (pr.ProjectFileVersionRow, FileVersions, set()),
        (pr.ProjectFileCommentRow, ProjectFileComments, set()),
        (pr.ProjectFolderRow, ProjectFolders, set()),
        (pr.ProjectMemberRow, ProjectMembers, set()),
        (pr.ProjectMemberWithEmail, ProjectMembers, {"email"}),
        (pr.ProjectCollectionRow, ProjectCollections, set()),
        (pr.ProjectStyleProfileRow, ProjectStyleProfile, set()),
    ],
)
def test_row_model_declares_every_column(model, orm, extra) -> None:
    assert set(model.model_fields) == column_names(orm) | extra


def test_share_model_declares_every_column_but_the_password() -> None:
    """``shares.password`` is plain text; the model withholds it and says
    only whether one is set (the deliberate wire change of this PR)."""
    expected = (column_names(Shares) - {"password"}) | {"has_password"}
    assert set(pr.ProjectShareRow.model_fields) == expected


def test_node_row_model_declares_every_node_row_key() -> None:
    row = node_row()
    assert set(pr.WorkflowNodeRow.model_fields) == set(row)
    assert set(pr.WorkflowNodeMemberRow.model_fields) == set(row["members"][0])


def test_stage_models_declare_every_selected_column() -> None:
    catalog_cols = {
        "id",
        "slug",
        "name",
        "sort_order",
        "tools_recommended",
        "created_at",
        "updated_at",
    }
    assert catalog_cols <= column_names(ProjectStages)
    assert set(pr.ProjectStageCatalogEntry.model_fields) == catalog_cols
    assert set(pr.ProjectStageHistoryEntry.model_fields) == column_names(
        ProjectStageHistory
    ) | {"stage_slug", "stage_name", "tools_recommended"}


# ── projects ────────────────────────────────────────────────────────────────


def _card_enrichment_stubs(monkeypatch, pids: list[int]) -> None:
    a, b, _c = (str(p) for p in pids)
    _patch(
        monkeypatch,
        ProjectStagesRepository,
        "latest_activity_for_projects",
        {
            a: {
                "stage_name": "Review",
                "actor": "ann",
                "entered_at": SAMPLE_TS.isoformat(),
            }
        },
    )
    _patch(
        monkeypatch,
        ProjectStagesRepository,
        "latest_file_activity_for_projects",
        {b: {"kind": "file", "actor": "bob", "created_at": SAMPLE_TS.isoformat()}},
    )
    _patch(
        monkeypatch,
        ProjectsRepository,
        "get_project_members_preview",
        {a: {"count": 4, "members": [{"user_id": USER, "username": "ann"}]}},
    )
    _patch(
        monkeypatch,
        ProjectStageNodesRepository,
        "workflow_badges_for_projects",
        {
            a: {
                "current_node_name": "Script",
                "workflow_total": 9,
                "workflow_position": 2,
                "agents_active": 1,
            },
            b: {
                "current_node_name": None,
                "workflow_total": 3,
                "workflow_position": None,
                "agents_active": 0,
            },
        },
    )


@pytest.mark.asyncio
async def test_list_projects_wire_unchanged(monkeypatch, client) -> None:
    rows = [
        project_row(),
        project_row(id=PID + 1),
        project_row(nulls=True, id=PID + 2),
    ]
    pids = [row["id"] for row in rows]
    _patch(monkeypatch, ProjectsRepository, "get_user_projects", rows)
    _patch(monkeypatch, ProjectsRepository, "get_project_file_counts", {str(PID): 3})
    _card_enrichment_stubs(monkeypatch, pids)
    raw = await ProjectsService().get_projects_with_counts(USER)
    kinds = [
        item["latest_activity"] and item["latest_activity"]["kind"] for item in raw
    ]
    assert kinds == ["stage", "file", None]  # both union arms + null
    resp = await client.get("/api/v1/projects")
    assert_wire_unchanged(resp, _envelope(raw))
    assert "label" not in resp.json()["data"][1]["latest_activity"]


@pytest.mark.asyncio
async def test_list_projects_enrichment_failure_wire_unchanged(
    monkeypatch, client
) -> None:
    _patch(monkeypatch, ProjectsRepository, "get_user_projects", [project_row()])
    _patch(monkeypatch, ProjectsRepository, "get_project_file_counts", {})

    async def _boom(*args, **kwargs):
        raise RuntimeError("down")

    _card_enrichment_stubs(monkeypatch, [PID, PID + 1, PID + 2])
    monkeypatch.setattr(ProjectStagesRepository, "latest_activity_for_projects", _boom)
    raw = await ProjectsService().get_projects_with_counts(USER)
    resp = await client.get("/api/v1/projects")
    assert_wire_unchanged(resp, _envelope(raw))


@pytest.mark.asyncio
async def test_create_and_update_project_wire_unchanged(monkeypatch, client) -> None:
    row = project_row(team_id=None)
    _patch(monkeypatch, ProjectsRepository, "create_project", row)
    _patch(monkeypatch, ProjectsRepository, "get_project_by_id", row)
    _patch(monkeypatch, ProjectsRepository, "update_project", row)

    async def _noop(*args, **kwargs):
        return None

    import app.core.deps as deps
    import app.services.workflow.instantiation as inst

    monkeypatch.setattr(deps, "get_team_id_for_user", _noop)
    monkeypatch.setattr(inst, "maybe_instantiate_project_workflow", _noop)
    resp = await client.post("/api/v1/projects", json={"name": "Test Project"})
    assert_wire_unchanged(resp, _envelope(row))
    resp = await client.put(BASE, json={"name": "Renamed"})
    assert_wire_unchanged(resp, _envelope(row))


@pytest.mark.asyncio
@pytest.mark.parametrize("role", ["manager", None])
async def test_get_project_wire_unchanged(monkeypatch, client, role) -> None:
    import app.core.workflow_roles as roles

    row = project_row(nulls=role is None)
    _patch(monkeypatch, ProjectsRepository, "get_project_by_id", lambda *a: dict(row))
    _patch(monkeypatch, ProjectsRepository, "get_project_file_count", 5)
    _patch(monkeypatch, roles, "resolve_effective_role", role)
    resp = await client.get(BASE)
    assert_wire_unchanged(
        resp, _envelope({**row, "file_count": 5, "effective_role": role})
    )


@pytest.mark.asyncio
async def test_message_routes_wire_unchanged(monkeypatch, client) -> None:
    row = project_row()
    _patch(monkeypatch, ProjectsRepository, "get_project_by_id", row)
    _patch(monkeypatch, ProjectsRepository, "delete_project", True)
    _patch(monkeypatch, ProjectsRepository, "get_file_by_id", file_row())
    _patch(monkeypatch, ProjectsRepository, "delete_file", True)
    _patch(monkeypatch, ProjectsRepository, "get_folder", repo_row_folder())
    _patch(monkeypatch, ProjectsRepository, "reparent_folder_children", None)
    _patch(monkeypatch, ProjectsRepository, "delete_folder_record", True)
    _patch(monkeypatch, ProjectsRepository, "get_comment_by_id", {"author_id": USER})
    _patch(monkeypatch, ProjectsRepository, "delete_comment", True)
    _patch(monkeypatch, ProjectsRepository, "delete_member", True)
    _patch(monkeypatch, ProjectsRepository, "delete_collection", True)
    cases = {
        BASE: "Project deleted",
        f"{BASE}/files/5": "File deleted",
        f"{BASE}/folders/5": "Folder deleted",
        f"{BASE}/files/5/comments/9": "Comment deleted",
        f"{BASE}/members/{USER}": "Member removed",
        f"{BASE}/collections/5": "Collection deleted",
    }
    for url, message in cases.items():
        resp = await client.delete(url)
        assert_wire_unchanged(resp, {"success": True, "message": message})
    assert set(pr.ProjectMessage.model_fields) == {"success", "message"}


# ── style profile / stages ─────────────────────────────────────────────────


def style_row() -> dict:
    row = sample_row(
        ProjectStyleProfile, only=[c.name for c in style_mod._PROFILE_COLS]
    )
    row["reference_links"] = ["https://example.com/ref", {"url": "x"}]  # jsonb array
    return style_mod._serialize(row)


@pytest.mark.asyncio
async def test_style_profile_wire_unchanged(monkeypatch, client) -> None:
    row = style_row()

    class _Repo:
        profile: dict | None = row

        async def get(self, project_id):
            return self.profile

        async def upsert(self, project_id, **fields):
            return row

    repo = _Repo()
    monkeypatch.setattr(style_mod, "get_project_style_profile_repository", lambda: repo)
    assert_wire_unchanged(await client.get(f"{BASE}/style-profile"), _envelope(row))
    resp = await client.put(f"{BASE}/style-profile", json={"style_md": "noir"})
    assert_wire_unchanged(resp, _envelope(row))
    repo.profile = None
    assert_wire_unchanged(await client.get(f"{BASE}/style-profile"), _envelope(None))


@pytest.mark.asyncio
async def test_stage_catalog_and_history_wire_unchanged(monkeypatch, client) -> None:
    catalog_cols = list(pr.ProjectStageCatalogEntry.model_fields)
    stage = sample_row(ProjectStages, only=catalog_cols)
    stage["tools_recommended"] = ["files", "canvas"]  # jsonb array
    catalog = [stages_mod._serialize(stage)]
    history_raw = sample_row(ProjectStageHistory)
    history_raw.update(stage_slug="review", stage_name="Review")
    open_raw = {**history_raw, "exited_at": None, "transitioned_by": None}
    history = [stages_mod._serialize(history_raw), stages_mod._serialize(open_raw)]
    assert isinstance(history[0]["transitioned_by"], uuid.UUID)  # not stringified

    class _Repo:
        async def list_catalog(self):
            return catalog

        async def history(self, project_id):
            return history

    monkeypatch.setattr(stages_mod, "get_project_stages_repository", lambda: _Repo())
    resp = await client.get("/api/v1/projects/stages/catalog")
    assert_wire_unchanged(resp, _envelope(catalog))
    resp = await client.get(f"{BASE}/stage_history")
    assert_wire_unchanged(resp, _envelope(history))


# ── workflow ────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_attach_workflow_wire_unchanged(monkeypatch, client) -> None:
    import app.repositories.workflow_templates_repository as tpl
    import app.services.workflow.instantiation as inst

    nodes = [node_row(), node_row(nulls=True, id=SAMPLE_BIGINT + 99)]
    _patch(
        monkeypatch, ProjectsRepository, "get_project_by_id", project_row(team_id=42)
    )
    _patch(monkeypatch, ProjectStageNodesRepository, "list_nodes", [])
    _patch(monkeypatch, tpl.WorkflowTemplatesRepository, "get_template_team_id", "42")
    _patch(monkeypatch, inst, "instantiate_project_workflow", nodes)
    resp = await client.post(f"{BASE}/workflow", json={"template_id": "1"})
    assert_wire_unchanged(resp, _envelope(nodes))


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "result",
    [
        {"converted": False, "reason": "no_legacy_chain"},
        {
            "converted": True,
            "episodes": 2,
            "nodes": 18,
            "template_id": str(SAMPLE_BIGINT),
            "method": None,
        },
    ],
)
async def test_reinstantiate_wire_unchanged(monkeypatch, client, result) -> None:
    import app.services.workflow.instantiation as inst

    _patch(monkeypatch, inst, "reinstantiate_project_per_episode", result)
    resp = await client.post(f"{BASE}/workflow/reinstantiate-per-episode")
    assert_wire_unchanged(resp, _envelope(result))


@pytest.mark.asyncio
async def test_surface_sync_wire_unchanged(monkeypatch, client) -> None:
    import app.services.workflow.surface_completion as sc

    _patch(
        monkeypatch, sc, "sync_project_surface_completion", {"episodes": 3, "failed": 1}
    )
    resp = await client.post(f"{BASE}/workflow/surface-completion/sync")
    assert_wire_unchanged(resp, _envelope({"episodes": 3, "failed": 1}))


@pytest.mark.asyncio
@pytest.mark.parametrize("with_mirror", [True, False])
async def test_stage_board_wire_unchanged(monkeypatch, client, with_mirror) -> None:
    node = node_row()
    mirror = issue_row(sample_orm(Issues))
    child = issue_row(_full(Issues, nulls=True))
    files = [file_row(), file_row(nulls=True)]
    ident = {str(files[0]["source_issue_id"]): "MH-7"}
    _patch(monkeypatch, ProjectStageNodesRepository, "get_node", node)
    _patch(monkeypatch, ProjectStageNodesRepository, "list_folder_files", files)
    _patch(
        monkeypatch, IssueRepository, "list_by_origin", [mirror] if with_mirror else []
    )
    _patch(monkeypatch, IssueRepository, "list_children", [child])
    _patch(monkeypatch, IssueRepository, "map_identifiers", ident)

    def ref(row: dict) -> dict:
        return {
            "id": str(row["id"]),
            "identifier": row.get("identifier"),
            "title": row.get("title"),
            "status": row.get("status"),
            "assignee": {
                "user_id": row.get("assignee_user_id") and str(row["assignee_user_id"]),
                "agent_id": row.get("assignee_agent_id")
                and str(row["assignee_agent_id"]),
            },
        }

    issue = {**ref(mirror), "sub_issues": [ref(child)]} if with_mirror else None
    expected_files = [
        {
            "id": str(f["id"]),
            "filename": f.get("filename"),
            "size": f.get("file_size_bytes"),
            "created_at": f.get("created_at"),
            "source_issue_identifier": (
                ident.get(str(f["source_issue_id"]))
                if f.get("source_issue_id")
                else None
            ),
        }
        for f in files
    ]
    resp = await client.get(f"{BASE}/workflow/nodes/{node['id']}/board")
    assert_wire_unchanged(
        resp, _envelope({"node": node, "issue": issue, "files": expected_files})
    )


@pytest.mark.asyncio
async def test_node_mutations_wire_unchanged(monkeypatch, client) -> None:
    import app.core.workflow_roles as roles
    import app.services.workflow.node_authz as authz
    import app.services.workflow.node_mutations as mutations

    node = node_row(nulls=True)
    _patch(monkeypatch, roles, "resolve_effective_role", "manager")
    _patch(monkeypatch, ProjectStageNodesRepository, "update_node", node)
    _patch(monkeypatch, authz, "require_arrangement_role", None)
    _patch(monkeypatch, mutations, "add_project_node", node)
    _patch(monkeypatch, mutations, "delete_project_node", True)

    resp = await client.patch(f"{BASE}/workflow/nodes/5", json={"skipped": True})
    assert_wire_unchanged(resp, _envelope(node))
    resp = await client.post(
        f"{BASE}/workflow/nodes", json={"name": "X", "sort_order": 1}
    )
    assert_wire_unchanged(resp, _envelope(node))
    resp = await client.delete(f"{BASE}/workflow/nodes/5")
    assert_wire_unchanged(resp, _envelope({"deleted": True}))


@pytest.mark.asyncio
async def test_start_early_wire_unchanged(monkeypatch, client) -> None:
    import app.core.workflow_roles as roles
    import app.services.workflow.advance_service as adv
    import app.services.workflow.node_start as start

    node = node_row(status="pending", skipped=False)
    started = node_row(status="in_progress", skipped=False)
    _patch(monkeypatch, ProjectStageNodesRepository, "get_node", node)
    _patch(monkeypatch, ProjectStageNodesRepository, "list_nodes_by_episode", [node])
    _patch(monkeypatch, roles, "resolve_effective_role", "manager")
    _patch(monkeypatch, start, "_open_mirror_issue", None)
    _patch(monkeypatch, start, "start_node_now", started)
    _patch(monkeypatch, r, "_require_project_episode", {"id": node["episode_id"]})
    monkeypatch.setattr(adv, "_unmet_dependency_names", lambda *a, **k: [])
    resp = await client.post(
        f"{BASE}/workflow/nodes/{node['id']}/start-early",
        params={"episode_id": node["episode_id"]},
    )
    assert_wire_unchanged(resp, _envelope(started))


@pytest.mark.asyncio
async def test_advance_wire_unchanged(monkeypatch, client) -> None:
    import app.services.workflow.advance_service as adv

    preview = AdvancePreview(
        direction="forward",
        will_advance=True,
        closing=[AdvanceNodeRef(node_id="1", name="Script", due_date="2026-09-24")],
        creating=[AdvanceNodeRef(node_id="2", name="Storyboard")],
        warnings=["w"],
    )
    _patch(monkeypatch, r, "_require_project_episode", {"id": "3"})
    _patch(monkeypatch, adv, "execute_advance", preview)
    resp = await client.post(f"{BASE}/advance", params={"episode_id": "3"})
    assert_wire_unchanged(resp, _envelope(preview.model_dump()))


# ── entities / renders ──────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_entities_wire_unchanged(monkeypatch, client) -> None:
    from app.repositories.script_scene_repository import ScriptSceneRepository
    from app.services.library.project_entities import derive_project_entities

    rows = [
        {
            "episode_id": SAMPLE_BIGINT,
            "location_text": "INT. HOUSE",
            "content_json": [{"type": "character", "text": "ANN"}],
        },
        {"episode_id": None, "location_text": "", "content_json": "[]"},
    ]
    _patch(monkeypatch, ScriptSceneRepository, "list_scene_rows_for_project", rows)
    resp = await client.get(f"{BASE}/entities")
    assert_wire_unchanged(resp, _envelope(derive_project_entities(rows)))


@pytest.mark.asyncio
async def test_renders_wire_unchanged(monkeypatch, client) -> None:
    projected = [col.key for col in gm_repo_mod._GM_COLS]
    row = sample_row(GeneratedMedia, only=projected)
    row["review_state"] = "unreviewed"
    page = {"items": [gm_repo_mod._normalize(row)], "next_cursor": "abc"}
    _patch(monkeypatch, r.GeneratedMediaRepository, "list_for_project", page)
    resp = await client.get(f"{BASE}/renders")
    assert_wire_unchanged(resp, _envelope(page))


# ── files ───────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_list_files_with_source_issues_wire_unchanged(
    monkeypatch, client
) -> None:
    files = [file_row(), file_row(nulls=True, id=SAMPLE_BIGINT + 50)]
    _patch(monkeypatch, ProjectsRepository, "get_project_by_id", project_row())
    _patch(monkeypatch, ProjectsRepository, "get_project_files", files)
    _patch(
        monkeypatch,
        IssueRepository,
        "map_identifiers",
        {str(files[0]["source_issue_id"]): "MH-3"},
    )
    raw = await ProjectsService().get_project_files(str(PID), include_trashed=True)
    assert isinstance(raw[0]["source_issue_id"], str)
    resp = await client.get(f"{BASE}/files", params={"include_trashed": "true"})
    assert_wire_unchanged(resp, _envelope(raw))


@pytest.mark.asyncio
async def test_list_files_without_source_issues_keeps_key_absent(
    monkeypatch, client
) -> None:
    files = [file_row(folder_id=None, source_issue_id=None)]
    _patch(monkeypatch, ProjectsRepository, "get_project_by_id", project_row())
    _patch(monkeypatch, ProjectsRepository, "get_project_files", files)
    raw = await ProjectsService().get_project_files(str(PID))
    resp = await client.get(f"{BASE}/files")
    assert_wire_unchanged(resp, _envelope(raw))
    assert "source_issue_identifier" not in resp.json()["data"][0]


@pytest.mark.asyncio
async def test_single_file_routes_wire_unchanged(monkeypatch, client) -> None:
    row = file_row()
    bare = file_row(nulls=True)
    _patch(monkeypatch, ProjectsRepository, "get_project_by_id", project_row())
    _patch(monkeypatch, ProjectsRepository, "get_file_by_id", row)
    _patch(monkeypatch, ProjectsRepository, "update_file", bare)
    _patch(monkeypatch, ProjectsRepository, "update_review_status", row)
    _patch(monkeypatch, ProjectsService, "upload_file", row)
    _patch(monkeypatch, ProjectsService, "link_media", bare)

    fid = row["id"]
    resp = await client.get(f"{BASE}/files/{fid}")
    assert_wire_unchanged(resp, _envelope(row))
    for url, body in [
        (f"{BASE}/files/{fid}", {"notes": "n"}),
        (f"{BASE}/files/{fid}/restore", None),
        (f"{BASE}/files/{fid}/move", {"folder_id": None}),
    ]:
        assert_wire_unchanged(await client.put(url, json=body), _envelope(bare))
    resp = await client.put(
        f"{BASE}/files/{fid}/review-status", json={"review_status": "approved"}
    )
    assert_wire_unchanged(resp, _envelope(row))
    resp = await client.post(
        f"{BASE}/files/upload", files={"file": ("a.png", b"img", "image/png")}
    )
    assert_wire_unchanged(resp, _envelope(row))
    resp = await client.post(f"{BASE}/files/link-media", json={"media_id": "1"})
    assert_wire_unchanged(resp, _envelope(bare))


@pytest.mark.asyncio
async def test_versions_wire_unchanged(monkeypatch, client) -> None:
    versions = [
        repo_mod._row(sample_orm(FileVersions), repo_mod._VERSIONS_N2A),
        repo_mod._row(_full(FileVersions, nulls=True), repo_mod._VERSIONS_N2A),
    ]
    _patch(monkeypatch, ProjectsRepository, "get_file_by_id", file_row())
    _patch(monkeypatch, ProjectsRepository, "get_file_versions", versions)
    _patch(monkeypatch, ProjectsService, "upload_new_version", versions[0])
    resp = await client.get(f"{BASE}/files/5/versions")
    assert_wire_unchanged(resp, _envelope(versions))
    resp = await client.post(
        f"{BASE}/files/5/versions", files={"file": ("a.png", b"img", "image/png")}
    )
    assert_wire_unchanged(resp, _envelope(versions[0]))


@pytest.mark.asyncio
async def test_comments_wire_unchanged(monkeypatch, client) -> None:
    comments = [
        repo_mod._comment_row(sample_orm(ProjectFileComments)),
        repo_mod._comment_row(_full(ProjectFileComments, nulls=True)),
    ]
    assert isinstance(comments[0]["id"], str)  # this surface stringifies ids
    _patch(monkeypatch, ProjectsRepository, "get_file_by_id", file_row())
    _patch(monkeypatch, ProjectsRepository, "get_comments_for_file", comments)
    _patch(monkeypatch, ProjectsRepository, "create_comment", comments[0])
    resp = await client.get(f"{BASE}/files/5/comments")
    assert_wire_unchanged(resp, _envelope(comments))
    resp = await client.post(f"{BASE}/files/5/comments", json={"content": "Nice"})
    assert_wire_unchanged(resp, _envelope(comments[0]))


@pytest.mark.asyncio
async def test_shares_wire_redacts_password(monkeypatch, client) -> None:
    shares = [
        repo_mod._row(sample_orm(Shares), repo_mod._SHARES_N2A),
        repo_mod._row(_full(Shares, nulls=True), repo_mod._SHARES_N2A),
    ]
    _patch(monkeypatch, ProjectsRepository, "get_shares_by_project", shares)
    _patch(
        monkeypatch,
        ProjectsRepository,
        "get_file_in_project",
        {"id": SAMPLE_BIGINT, "filename": "a.mp4"},
    )
    _patch(monkeypatch, ProjectsRepository, "create_share", shares[0])
    assert shares[0]["password"] is not None and shares[1]["password"] is None
    redacted = [
        {**{k: v for k, v in row.items() if k != "password"}, "has_password": has}
        for row, has in zip(shares, (True, False))
    ]
    listed = await client.get(f"{BASE}/shares")
    assert_wire_unchanged(listed, _envelope(redacted))
    assert all("password" not in row for row in listed.json()["data"])
    resp = await client.post(f"{BASE}/shares", json={"file_id": "5"})
    assert_wire_unchanged(resp, _envelope(redacted[0]))
    assert "password" not in resp.json()["data"]


def repo_row_folder(*, nulls: bool = False) -> dict:
    obj = _full(ProjectFolders, nulls=nulls, project_id=PID)
    return repo_mod._row(obj, repo_mod._FOLDERS_N2A)


@pytest.mark.asyncio
async def test_folders_wire_unchanged(monkeypatch, client) -> None:
    folders = [repo_row_folder(), repo_row_folder(nulls=True)]
    _patch(monkeypatch, ProjectsRepository, "get_folders", folders)
    _patch(monkeypatch, ProjectsRepository, "create_folder", folders[1])
    _patch(monkeypatch, ProjectsRepository, "update_folder", folders[0])
    assert_wire_unchanged(await client.get(f"{BASE}/folders"), _envelope(folders))
    resp = await client.post(f"{BASE}/folders", json={"name": "Cuts"})
    assert_wire_unchanged(resp, _envelope(folders[1]))
    resp = await client.put(f"{BASE}/folders/5", json={"name": "Final"})
    assert_wire_unchanged(resp, _envelope(folders[0]))


# ── members / collections ──────────────────────────────────────────────────


def member_row(*, nulls: bool = False) -> dict:
    obj = _full(ProjectMembers, nulls=nulls, user_id=uuid.UUID(USER), project_id=PID)
    return repo_mod._row(obj, repo_mod._MEMBERS_N2A)


class _Users:
    def __init__(self, fail: bool) -> None:
        self.fail = fail

    async def list_users(self):
        if self.fail:
            raise RuntimeError("auth admin down")
        return [type("U", (), {"id": uuid.UUID(USER), "email": "ann@example.com"})]


@pytest.mark.asyncio
@pytest.mark.parametrize("auth_down", [False, True])
async def test_list_members_wire_unchanged(monkeypatch, client, auth_down) -> None:
    client_stub = type("C", (), {})()
    client_stub.auth = type("A", (), {})()
    client_stub.auth.admin = _Users(fail=auth_down)
    _patch(monkeypatch, ProjectsRepository, "_get_client", client_stub)
    _patch(
        monkeypatch,
        ProjectsRepository,
        "get_members",
        lambda *a: [member_row(), member_row(nulls=True)],
    )
    raw = await ProjectsService().list_members(str(PID))
    assert ("email" in raw[0]) is (not auth_down)
    resp = await client.get(f"{BASE}/members")
    assert_wire_unchanged(resp, _envelope(raw))


@pytest.mark.asyncio
async def test_member_writes_wire_unchanged(monkeypatch, client) -> None:
    _patch(monkeypatch, ProjectsRepository, "create_member", lambda *a: member_row())
    _patch(monkeypatch, ProjectsRepository, "get_user_email", "ann@example.com")
    _patch(monkeypatch, ProjectsRepository, "update_member", member_row(nulls=True))
    resp = await client.post(f"{BASE}/members", json={"user_id": USER})
    assert_wire_unchanged(resp, _envelope({**member_row(), "email": "ann@example.com"}))
    resp = await client.put(f"{BASE}/members/{USER}", json={"role": "editor"})
    assert_wire_unchanged(resp, _envelope(member_row(nulls=True)))


@pytest.mark.asyncio
async def test_collections_wire_unchanged(monkeypatch, client) -> None:
    rows = [
        repo_mod._row(sample_orm(ProjectCollections), repo_mod._COLLECTIONS_N2A),
        repo_mod._row(_full(ProjectCollections, nulls=True), repo_mod._COLLECTIONS_N2A),
    ]
    _patch(monkeypatch, ProjectsRepository, "get_collections", rows)
    _patch(monkeypatch, ProjectsRepository, "create_collection", rows[1])
    assert_wire_unchanged(await client.get(f"{BASE}/collections"), _envelope(rows))
    resp = await client.post(f"{BASE}/collections", json={"collection_name": "Inbox"})
    assert_wire_unchanged(resp, _envelope(rows[1]))


# ── binary route ────────────────────────────────────────────────────────────


def test_download_is_declared_binary_not_json() -> None:
    op = app.openapi()["paths"][
        "/api/v1/projects/{project_id}/files/{file_id}/download"
    ]
    content = op["get"]["responses"]["200"]["content"]
    assert set(content) == {"*/*"}
    assert content["*/*"]["schema"] == {"type": "string", "format": "binary"}
