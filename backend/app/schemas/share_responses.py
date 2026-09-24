"""Response shapes of ``/api/v1/shares`` (``app/api/shares_router.py``).

Every model mirrors the dict the route already built, key for key (spec
2026-09-24-openapi-typed-frontend-design.md §5; wire tests in
``tests/api/test_shares_wire.py``). The router turns rows into
PostgREST-shaped dicts itself (``_row_to_dict``): datetimes are ISO strings
already, UUIDs are strings, BIGINT ids stay JSON numbers.

No model here declares ``shares.password`` or ``shares.password_hash``
(since mig 504 the first is a random lock and the second a bcrypt hash).
Owner rows carry ``has_password`` instead, and the visitor payload carries
neither.
"""

from __future__ import annotations

from pydantic import BaseModel

from app.schemas.envelope import Envelope


class ShareRow(BaseModel):
    """One ``shares`` row as its owner sees it (``_enrich_share``)."""

    id: int
    share_type: str
    shared_by: str
    share_name: str
    share_code: str
    allow_download: bool
    view_count: int
    watermark: bool
    status: str
    created_at: str
    expires_at: str | None
    max_views: int | None
    project_file_id: int | None
    version_id: int | None
    resource_id: int | None
    folder_id: int | None
    library_id: int | None
    team_id: int | None
    share_url: str
    has_password: bool


class ShareListResponse(Envelope[list[ShareRow]]):
    count: int


class ShareStatusToggleResponse(BaseModel):
    """``DELETE /shares/{id}`` flips active ↔ inactive; no ``data`` key."""

    success: bool = True
    message: str
    status: str


class ShareMessageResponse(BaseModel):
    success: bool = True
    message: str


class ShareVisitorView(BaseModel):
    """What a visitor gets from ``POST /shares/code/{code}``.

    The six resource keys (``mime_type`` … ``media_id``) are only present
    when the share points at a resource that still exists; the route is
    declared ``response_model_exclude_unset`` so an absent key stays absent.

    ``access_token`` is the share grant (``app/api/share_access.py``): pass
    it as ``share_token`` to the media file routes and the comment routes.
    For a password-protected share it is the only thing they accept.
    """

    id: int
    share_type: str
    share_name: str
    share_code: str
    allow_download: bool
    watermark: bool
    view_count: int
    resource_id: int | None
    project_file_id: int | None
    folder_id: int | None
    version_id: int | None
    created_at: str
    access_token: str
    mime_type: str | None = None
    file_type: str | None = None
    filename: str | None = None
    cover_image_path: str | None = None
    thumbnail_path: str | None = None
    media_id: str | None = None


class ShareComment(BaseModel):
    """A review comment as a share visitor sees it."""

    id: int
    content: str
    timecode: float | None
    frame_number: int | None
    status: str
    author_id: str
    parent_id: int | None
    created_at: str


class ShareCommentRow(BaseModel):
    """The full ``review_comments`` row returned to the author who posted it."""

    id: int
    resource_id: int
    author_id: str
    content: str
    status: str
    created_at: str
    updated_at: str
    timecode: float | None
    frame_number: int | None
    parent_id: int | None
    version_id: int | None
