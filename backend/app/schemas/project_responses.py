"""Response shapes ``/api/v1/projects`` ALREADY emits, declared as models.

Every model here mirrors a dict the repository/service layer builds today;
none of them is an aspiration. Where the wire is odd it is recorded, not fixed:

- ``projects_repository._row`` (strategy C) keys rows by DB column name,
  turns uuid → ``str`` and datetime → ISO ``str``, and leaves bigint ids
  **native int** (JSON number) — so ids here are ``int`` and timestamps are
  ``str``. The exceptions are real: ``_comment_row`` stringifies the three
  comment ids, the workflow-node / stage repositories stringify theirs.
- Numeric columns (``fps``) arrive as ``Decimal`` and are declared ``float``
  (same JSON value).
- A key that is only SOMETIMES present (``source_issue_identifier`` on the
  file list, ``email`` on the member list) is declared with a default and the
  route sets ``response_model_exclude_unset=True``, so an absent key stays
  absent instead of turning into ``null``.

Wire parity is pinned by ``tests/api/test_projects_wire.py``.
Spec: docs/superpowers/specs/2026-09-24-openapi-typed-frontend-design.md §5
"""

from __future__ import annotations

import uuid
from typing import Annotated, Any, Literal, Union

from pydantic import BaseModel, Field

from app.schemas.workflow import NodeStatus

# ── projects ────────────────────────────────────────────────────────────────


class ProjectRow(BaseModel):
    """One ``projects`` row as ``projects_repository._row`` returns it."""

    id: int
    name: str
    description: str | None
    owner_id: str
    project_type: str
    is_starred: bool
    visibility: str
    project_group: str | None
    workflow_id: str | None
    team_id: int | None
    announcement: str | None
    color_label: str | None
    current_canvas_id: int | None
    current_node_id: int | None
    topic_id: int | None
    archived_at: str | None
    autopilot_enabled: bool
    workflow_template_id: int | None
    workflow_method: str | None
    created_at: str
    updated_at: str


class ProjectDetail(ProjectRow):
    """``GET /projects/{id}``: the row + file count + the caller's role."""

    file_count: int
    effective_role: str | None


class ProjectMemberPreviewEntry(BaseModel):
    user_id: str
    username: str


class ProjectMembersPreview(BaseModel):
    count: int
    members: list[ProjectMemberPreviewEntry]


class ProjectFileActivity(BaseModel):
    """Latest activity = the newest file add (``_merge_activity``)."""

    kind: Literal["file"]
    actor: str
    at: str
    stalled: bool


class ProjectStageActivity(BaseModel):
    """Latest activity = the newest stage transition (``_merge_activity``)."""

    kind: Literal["stage"]
    actor: str
    label: str
    at: str | None
    stalled: bool


ProjectLatestActivity = Annotated[
    Union[ProjectFileActivity, ProjectStageActivity],
    Field(discriminator="kind"),
]


class ProjectWorkflowBadge(BaseModel):
    current_node_name: str | None
    workflow_total: int
    workflow_position: int | None
    agents_active: int


class ProjectListItem(ProjectRow):
    """``GET /projects`` card: the row + file count + card enrichment."""

    file_count: int
    # Always null since M2 PR-G1.5 retired the SOP stage cursor; the key is
    # still sent.
    current_stage: None
    members_preview: ProjectMembersPreview | None
    latest_activity: ProjectLatestActivity | None
    workflow_badge: ProjectWorkflowBadge | None


class ProjectMessage(BaseModel):
    """``{"success": true, "message": ...}`` — deletes carry no ``data``."""

    success: bool = True
    message: str


class ProjectStyleProfileRow(BaseModel):
    project_id: int
    style_md: str
    visual_style: dict[str, Any]
    reference_links: list[Any]
    updated_by: str | None
    created_at: str
    updated_at: str


# ── SOP stages (catalog is now the node-library dictionary) ────────────────


class ProjectStageCatalogEntry(BaseModel):
    id: str
    slug: str
    name: str
    sort_order: int
    tools_recommended: list[Any]
    created_at: str
    updated_at: str


class ProjectStageHistoryEntry(BaseModel):
    id: str
    project_id: str
    stage_id: str
    entered_at: str
    exited_at: str | None
    # Raw ``text()`` row: the repository does not stringify this uuid, the
    # JSON encoder does.
    transitioned_by: uuid.UUID | None
    stage_slug: str
    stage_name: str
    # Not a history column: the shared ``_serialize`` defaults a missing
    # ``tools_recommended`` to ``[]``, so every history row carries it.
    tools_recommended: list[Any]


# ── workflow nodes (raw repository rows, not the GET /workflow projection) ──


class WorkflowNodeMemberRow(BaseModel):
    id: str
    node_id: str
    user_id: str | None
    agent_id: str | None


class WorkflowNodeRow(BaseModel):
    """``project_stage_nodes_repository._node_row`` verbatim.

    Unlike ``NodeOut`` (the ``GET /workflow`` projection) this carries the
    member row ids, raw ``events`` / ``form_schema`` JSONB, and no
    ``deliverable_file_count``."""

    id: str
    project_id: str
    source_template_node_id: str | None
    legacy_stage_id: str | None
    name: str
    sort_order: int
    parallel_group: int | None
    episode_id: str | None
    status: NodeStatus
    owner_user_id: str | None
    owner_agent_id: str | None
    planned_start: str | None
    planned_due: str | None
    review_required: bool
    deliverable_required: bool
    deliverable_label: str | None
    skipped: bool
    folder_id: str | None
    completion_policy: str
    events: dict[str, Any]
    surface: str | None
    metadata: dict[str, Any]
    form_schema: list[Any]
    form_data: dict[str, Any]
    brief: str
    members: list[WorkflowNodeMemberRow]
    depends_on: list[str]


class WorkflowReinstantiateSkipped(BaseModel):
    converted: Literal[False]
    reason: str


class WorkflowReinstantiateConverted(BaseModel):
    converted: Literal[True]
    episodes: int
    nodes: int
    template_id: str
    method: str | None


WorkflowReinstantiateResult = Union[
    WorkflowReinstantiateConverted, WorkflowReinstantiateSkipped
]


class SurfaceCompletionSyncResult(BaseModel):
    episodes: int
    failed: int


class StageBoardAssignee(BaseModel):
    user_id: str | None
    agent_id: str | None


class StageBoardIssueRef(BaseModel):
    id: str
    identifier: str | None
    title: str | None
    status: str | None
    assignee: StageBoardAssignee


class StageBoardIssue(StageBoardIssueRef):
    sub_issues: list[StageBoardIssueRef]


class StageBoardFile(BaseModel):
    id: str
    filename: str | None
    size: int | None
    created_at: str | None
    source_issue_identifier: str | None


class StageBoard(BaseModel):
    node: WorkflowNodeRow
    issue: StageBoardIssue | None
    files: list[StageBoardFile]


class WorkflowNodeDeleted(BaseModel):
    deleted: bool


# ── derived entities ────────────────────────────────────────────────────────


class ProjectCharacterEntity(BaseModel):
    name: str
    cue_count: int
    episode_ids: list[str]


class ProjectLocationEntity(BaseModel):
    name: str
    scene_count: int
    episode_ids: list[str]


class ProjectEntities(BaseModel):
    characters: list[ProjectCharacterEntity]
    locations: list[ProjectLocationEntity]


# ── files / versions / comments / shares / folders / members / collections ─


class ProjectFileRow(BaseModel):
    """One ``project_files`` row as ``projects_repository._row`` returns it."""

    id: int
    project_id: int
    filename: str
    file_type: str | None
    mime_type: str | None
    file_path: str | None
    file_size_bytes: int | None
    duration_seconds: int | None
    resolution: str | None
    fps: float | None
    video_codec: str | None
    audio_codec: str | None
    video_bitrate_kbps: int | None
    audio_bitrate_kbps: int | None
    audio_channels: int | None
    audio_sample_rate: int | None
    thumbnail_path: str | None
    cover_image_path: str | None
    uploaded_by: str | None
    notes: str | None
    is_trashed: bool
    trashed_at: str | None
    review_status: str | None
    current_version: int
    media_id: int | None
    folder_id: int | None
    source_issue_id: int | None
    created_at: str
    updated_at: str


class ProjectFileListRow(ProjectFileRow):
    """``GET /projects/{id}/files`` row.

    When any listed file carries a ``source_issue_id`` the service rewrites
    that key to a string and adds ``source_issue_identifier`` to EVERY row;
    otherwise neither happens (the key is absent, not null)."""

    source_issue_id: str | None
    source_issue_identifier: str | None = None


class ProjectFileVersionRow(BaseModel):
    id: int
    file_id: int
    version_number: int
    filename: str | None
    file_path: str | None
    file_size_bytes: int | None
    mime_type: str | None
    duration_seconds: int | None
    resolution: str | None
    fps: float | None
    video_codec: str | None
    audio_codec: str | None
    video_bitrate_kbps: int | None
    audio_bitrate_kbps: int | None
    audio_channels: int | None
    audio_sample_rate: int | None
    thumbnail_path: str | None
    cover_image_path: str | None
    uploaded_by: str | None
    notes: str | None
    created_at: str


class ProjectFileCommentRow(BaseModel):
    """``_comment_row``: the three ids ARE strings on this surface."""

    id: str
    file_id: str
    version_id: str | None
    author_id: str
    content: str
    timestamp_seconds: float | None
    drawing_data: dict[str, Any] | None
    created_at: str
    updated_at: str


class ProjectShareRow(BaseModel):
    """One ``shares`` row, without its ``password``.

    The column holds the password in plain text, and these two routes used to
    return it to every project reader. ``has_password`` replaces it, the same
    redaction ``shares_router._enrich_share`` already applies.
    """

    id: int
    share_type: str
    shared_by: str
    share_name: str
    share_code: str
    has_password: bool
    allow_download: bool
    view_count: int
    watermark: bool
    status: str
    expires_at: str | None
    max_views: int | None
    project_file_id: int | None
    version_id: int | None
    resource_id: int | None
    folder_id: int | None
    library_id: int | None
    team_id: int | None
    created_at: str


class ProjectFolderRow(BaseModel):
    id: int
    project_id: int
    name: str
    parent_id: int | None
    created_by: str | None
    created_at: str
    updated_at: str


class ProjectMemberRow(BaseModel):
    """``project_members`` has a composite PK and no ``id``."""

    user_id: str
    project_id: int
    role: str
    invited_by: str | None
    joined_at: str


class ProjectMemberWithEmail(ProjectMemberRow):
    """``email`` comes from the auth admin API; when that lookup fails the
    list is returned without the key at all."""

    email: str = ""


class ProjectCollectionRow(BaseModel):
    id: int
    project_id: int
    collection_code: str
    collection_name: str
    allowed_types: list[str] | None
    max_file_size_mb: int | None
    deadline: str | None
    is_active: bool | None
    created_by: str | None
    created_at: str
