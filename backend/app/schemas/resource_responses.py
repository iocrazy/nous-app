"""Response bodies of the ``/resources`` routers that are not bare rows.

The row shapes live in :mod:`app.schemas.resource_rows`; this module holds the
envelopes with extra sibling keys and the small computed results the handlers
build inline. Everything here mirrors a dict literal in a handler or service —
the wire-parity tests (``tests/api/test_resources_*_wire.py``) compare each
route's body with ``jsonable_encoder`` of that dict.
"""

from __future__ import annotations

from typing import Dict, List, Literal, Optional

from pydantic import BaseModel

from app.schemas.envelope import Envelope
from app.schemas.generated import GeneratedItem
from app.schemas.resource_rows import LyricsJson, ResourceRow
from app.schemas.wire import WireDatetime

# ── generic ───────────────────────────────────────────────────────────────


class ResourcesMessage(BaseModel):
    """``{"success": true, "message": ...}`` — the no-payload success body."""

    success: bool = True
    message: str


# ── crud ──────────────────────────────────────────────────────────────────


class ResourceBatchTranscodeResult(BaseModel):
    """``POST /resources/transcode/batch``. ``has_more`` = a full batch was
    queued, so re-invoking drains the rest."""

    success: bool = True
    queued: int
    total_found: int
    has_more: bool


class ResourceLyricsUpload(BaseModel):
    """``POST /resources/{id}/lyrics``: the parsed lyrics and the updated row."""

    lyrics_json: LyricsJson
    resource: ResourceRow


class ResourceSplitFrame(ResourceRow):
    """One grid cell persisted as a new resource, with its grid position."""

    row: int
    col: int
    index: int


class ResourceSplitResult(BaseModel):
    frames: List[ResourceSplitFrame]


# ── folders ───────────────────────────────────────────────────────────────


class FolderContentCount(BaseModel):
    """Resources and sub-folders under a folder, nested ones included."""

    resource_count: int
    subfolder_count: int


class FolderTrashResult(BaseModel):
    trashed_folders: int
    trashed_resources: int


class FolderRestoreResult(BaseModel):
    restored_folders: int
    restored_resources: int


class FolderDeleteResult(BaseModel):
    deleted_folders: int
    deleted_resources: int


class FolderDeleteEnvelope(Envelope[FolderDeleteResult]):
    """``DELETE /resources/folders/{id}`` also carries a ``message``."""

    message: str


# ── ai ────────────────────────────────────────────────────────────────────


class ResourceGenPrompts(BaseModel):
    """The four prompt columns after a translation wrote one side."""

    gen_prompt: Optional[str]
    gen_prompt_zh: Optional[str]
    gen_prompt_negative: Optional[str]
    gen_prompt_negative_zh: Optional[str]


class ResourceTaskDispatch(BaseModel):
    """A dispatched asset-AI workflow; ``task_id`` is the task_tracking id
    (= the DBOS workflow id)."""

    success: bool = True
    task_id: str


class ResourceAiDispatched(BaseModel):
    resource_id: str
    task_id: str


class ResourceAiSkipped(BaseModel):
    resource_id: str
    reason: str


class ResourceBatchAiResult(BaseModel):
    """``POST /resources/ai/batch``: per-resource outcome, input order."""

    success: bool = True
    dispatched: List[ResourceAiDispatched]
    skipped: List[ResourceAiSkipped]


# ── gallery ───────────────────────────────────────────────────────────────


class GalleryMembership(BaseModel):
    """Child image ids to hide + per-gallery child counts, for one scope."""

    child_image_ids: List[str]
    gallery_counts: Dict[str, int]


# ── upload / duplicates / permissions ─────────────────────────────────────


class ResourcePermissions(BaseModel):
    """The caller's effective role on an object and what it allows."""

    role: str
    capabilities: List[str]


class ResourceDuplicateCandidate(BaseModel):
    """The projection ``find_by_hash`` selects (no ``file_hash``: the batch
    endpoint's ``existing`` carries one, this does not)."""

    id: int
    filename: str
    file_type: Optional[str]
    mime_type: Optional[str]
    file_size_bytes: Optional[int]
    thumbnail_path: Optional[str]
    cover_image_path: Optional[str]
    created_at: str


class ResourceDuplicateCheck(BaseModel):
    """``GET /resources/check-duplicate`` — no ``success`` key."""

    duplicate: bool
    existing: Optional[ResourceDuplicateCandidate]


class ResourceLinkExisting(Envelope[ResourceRow]):
    """``POST /resources/link-existing``. ``already_linked`` is sent only when
    the placement already existed; the route uses
    ``response_model_exclude_unset`` so an absent key stays absent."""

    already_linked: bool = False


# ── search ────────────────────────────────────────────────────────────────


class ResourceSearchScope(BaseModel):
    type: str
    id: str


class ResourceSearchHit(BaseModel):
    """One @-reference picker result. A whitelist, not the row: storage paths
    and ``media_id`` are inputs to ``thumbnail_url``, never output."""

    id: str
    name: str
    kind: Literal["video", "image", "audio", "pdf", "doc"]
    mime: Optional[str]
    size: Optional[int]
    scope: ResourceSearchScope
    updated_at: WireDatetime
    thumbnail_url: Optional[str]
    transcript_status: Optional[str]
    summary_status: Optional[str]


class ResourceSearchCounts(BaseModel):
    """Tab badges over the whole visible set, zero-filled."""

    all: int
    video: int
    image: int
    audio: int
    pdf: int
    doc: int


class ResourceSearchResponse(BaseModel):
    """``GET /resources/search``. ``next_cursor`` is reserved (always null)."""

    results: List[ResourceSearchHit]
    counts: ResourceSearchCounts
    next_cursor: Optional[str]


# ── assets ────────────────────────────────────────────────────────────────


class ResourceSaveAsAssetResult(BaseModel):
    """``POST /resources/{id}/save-as-asset`` — the generation route's three
    keys plus the inbox row that was found or minted."""

    generation: GeneratedItem
    asset_id: str
    resource_id: str
    generated_id: str
