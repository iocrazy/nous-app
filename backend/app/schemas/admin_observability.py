"""Response models for the admin observability routes (P8 group C).

Covers ``/admin/storage/*`` (storage observability over the S3-migrated
library), ``/admin/boundary-audit*`` (SSRF block log viewer) and
``/admin/agent-metrics`` (in-process harness counters). Each model declares
exactly the keys the handler already built as a bare dict; the wire-parity
tests in ``tests/api/admin/test_admin_observability_wire.py`` pin that.

Ids the handlers already stringify (``media_id``, ``scope_id``,
``resource_id``) are declared ``str``; ``boundary_audit.id`` / ``user_id`` are
BIGINT columns returned as JSON numbers, and stay ``int``. Timestamps that the
handler or the storage-audit workflow already wrote as ISO strings are
declared ``str``.

Spec: docs/superpowers/specs/2026-09-24-openapi-typed-frontend-design.md §3.3
"""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel

StorageAssetKind = Literal["video", "cover", "thumbnail", "sprite", "hls"]

# ── /admin/storage ────────────────────────────────────────────────────────


class AdminStorageVideoTotals(BaseModel):
    count: int
    size_bytes: int


class AdminStorageLastScan(BaseModel):
    """Summary of the newest completed storage_audit run."""

    at: str | None
    scanned: int
    missing: int
    errors: int


class AdminStorageStats(BaseModel):
    videos: AdminStorageVideoTotals
    fs_residue: int
    orphans: int
    hls_ready: int
    # None until a storage_audit run has completed.
    broken: int | None
    last_scan: AdminStorageLastScan | None


class AdminStorageMediaStatusRow(BaseModel):
    media_id: str
    video_key: str | None
    video_size: int | None
    cover_ok: bool
    thumbnail_ok: bool
    hls_ok: bool
    storage_status: Literal["ok", "no_video", "fs_residue"]
    scope_id: str | None


class AdminStorageMediaStatusList(BaseModel):
    rows: list[AdminStorageMediaStatusRow]


class AdminStorageAsset(BaseModel):
    kind: StorageAssetKind
    key: str | None
    size_bytes: int | None
    present_in_db: bool


class AdminStorageMediaDetail(BaseModel):
    assets: list[AdminStorageAsset]
    scope_id: str | None


class AdminStorageVerifyResult(BaseModel):
    kind: StorageAssetKind
    key: str
    # True present / False confirmed missing / None the probe was uncertain.
    exists: bool | None


class AdminStorageMediaVerify(BaseModel):
    results: list[AdminStorageVerifyResult]


class AdminStorageDeepVerifyDispatch(BaseModel):
    workflow_id: str
    already_running: bool


class AdminStorageAuditMissing(BaseModel):
    key: str
    kind: str
    media_id: str | None
    resource_id: str | None


class AdminStorageAudit(BaseModel):
    """Newest storage_audit run. ``status`` is the task_tracking phase, or
    ``none`` when no scan has ever run; ``missing_truncated`` is absent in
    that ``none`` case (route uses ``response_model_exclude_unset``)."""

    status: str
    scanned: int
    errors: int
    scanned_at: str | None
    missing: list[AdminStorageAuditMissing]
    missing_truncated: bool = False


# ── /admin/boundary-audit ─────────────────────────────────────────────────


class AdminBoundaryAuditRow(BaseModel):
    id: int
    blocked_at: str | None
    layer: str
    reason: str
    raw_url: str | None
    resolved_ip: str | None
    user_id: int | None
    request_id: str | None
    metadata_json: dict[str, Any] | None


class AdminBoundaryAuditPage(BaseModel):
    items: list[AdminBoundaryAuditRow]
    total: int
    limit: int
    offset: int


class AdminBoundaryAuditSummary(BaseModel):
    by_layer: dict[str, int]
    by_reason: dict[str, int]
    total_7d: int


# ── /admin/agent-metrics ──────────────────────────────────────────────────


class AdminAgentBound(BaseModel):
    role: str
    version: str | None
    workflows: list[str]
    agents: list[str]
    providers: list[str]
    lane_capacity: dict[str, int]
    started_at: float
    last_seen_age_s: float


class AdminRootAbortEntry(BaseModel):
    is_aborted: bool
    children: list[str]
    child_count: int


class AdminModelHealthEntry(BaseModel):
    health: str
    reason: str | None
    cooldown_remaining_s: float


class AdminAgentMetrics(BaseModel):
    """Per-process harness snapshot. ``counters`` maps each canonical counter
    to its count; typo'd names collected since start sit under ``_unknown``
    as a nested ``{name: count}`` map."""

    counters: dict[str, int | dict[str, int]]
    bounds: dict[str, AdminAgentBound]
    root_aborts: dict[str, AdminRootAbortEntry]
    model_health: dict[str, AdminModelHealthEntry]
    hooks_registered: list[str]
    context_engines: list[str]
