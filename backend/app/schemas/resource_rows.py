"""Row shapes the resources repository hands to the ``/resources`` routers.

These declare what the routers already sent; nothing here changes the wire.
Every row dict of ``ResourcesRepository`` goes through ``_rest_parity``
(``app/repositories/resources_repository.py``), which is what fixes the value
types below:

- **Snowflake ids stay JSON numbers** (``int``). Unlike ``/generated-media``,
  nothing on this surface stringifies them — so a JS client reading ``id``
  without ``bigIntSafeFetch`` loses precision. Declared as they are, not as
  they should be.
- **uuid columns are strings** (``creator_id``, ``added_by``, ``uploaded_by``
  …): ``_rest_parity`` turns ``uuid.UUID`` into ``str``.
- **timestamps are ISO strings already** (``created_at`` …), so they are
  ``str``, not :data:`app.schemas.wire.WireDatetime`. The embedded
  ``resource`` of the list endpoints comes from Postgres ``row_to_json`` and
  is a string there too.
- enum-backed status columns are unwrapped to their bare string value.

Every field is required (nullable columns are ``X | None`` with no default):
each row carries every column. ``tests/api/test_resources_*_wire.py`` pin each
model's field set to its ORM model's column set, so a column added to the
table fails there until it is declared here.
"""

from __future__ import annotations

from typing import Any, Dict, Optional

from pydantic import BaseModel

# ``resources.lyrics_json``. Its only writer today is ``lrc_parser.parse_lrc``
# (``{"lrc", "lines": [{"text", "line_start_ms"}]}``), but the read side stays
# an open object on purpose: a malformed historical row must still read back,
# not turn GET /resources/{id} into a 500. Shape checks belong to the writer.
LyricsJson = Optional[Dict[str, Any]]


class ResourceRow(BaseModel):
    """One ``resources`` row (``SELECT *`` shape)."""

    id: int
    creator_id: str
    source_type: str
    filename: str
    current_version: int
    is_trashed: bool
    created_at: str
    updated_at: str
    transcript_status: str
    summary_status: str
    visual_analysis_status: str
    file_type: Optional[str]
    mime_type: Optional[str]
    file_path: Optional[str]
    file_size_bytes: Optional[int]
    duration_seconds: Optional[int]
    resolution: Optional[str]
    thumbnail_path: Optional[str]
    cover_image_path: Optional[str]
    trashed_at: Optional[str]
    media_id: Optional[int]
    file_hash: Optional[str]
    notes: Optional[str]
    gen_prompt: Optional[str]
    gen_prompt_zh: Optional[str]
    gen_prompt_negative: Optional[str]
    gen_prompt_negative_zh: Optional[str]
    slide_prompts: Optional[Dict[str, Any]]
    gen_params: Optional[Dict[str, Any]]
    summary_follow_up: Optional[Dict[str, Any]]
    gen_prompt_json: Optional[str]
    prompt_origin: Optional[str]
    url: Optional[str]
    rating: Optional[int]
    last_folder_id: Optional[int]
    last_library_id: Optional[int]
    last_scope_type: Optional[str]
    last_scope_id: Optional[str]
    aspect_bucket: Optional[str]
    audio_bitrate_kbps: Optional[int]
    lyrics_json: LyricsJson
    chorus_start_ms: Optional[int]


class LibraryResource(ResourceRow):
    """The embedded resource of ``GET /resources``: the row plus its gallery
    child count (``0`` for anything that is not a gallery)."""

    gallery_count: int


class ResourceItemRow(BaseModel):
    """One ``resource_items`` row — a resource's placement in a scope/folder."""

    id: int
    resource_id: int
    scope_id: int
    folder_id: Optional[int]
    library_id: Optional[int]
    added_by: Optional[str]
    created_at: str


class ResourceListItem(ResourceItemRow):
    """``GET /resources``: a placement with its resource embedded."""

    resource: LibraryResource


class ResourcePlacement(ResourceItemRow):
    """A placement with its resource embedded, no gallery count (the trash
    list and smart-folder results)."""

    resource: ResourceRow


class ResourceVersionRow(BaseModel):
    """One ``resource_versions`` row."""

    id: int
    resource_id: int
    version_number: int
    created_at: str
    filename: Optional[str]
    file_path: Optional[str]
    file_size_bytes: Optional[int]
    mime_type: Optional[str]
    duration_seconds: Optional[int]
    resolution: Optional[str]
    thumbnail_path: Optional[str]
    uploaded_by: Optional[str]
    notes: Optional[str]
    hls_path: Optional[str]
    transcode_status: Optional[str]
    transcode_at: Optional[str]
    file_hash: Optional[str]
    audio_bitrate_kbps: Optional[int]
    storage_status: str


class FolderRow(BaseModel):
    """One ``folders`` row (regular and smart folders share the table)."""

    id: int
    name: str
    scope_id: int
    created_by: str
    sort_order: int
    is_system: bool
    system_key: Optional[str]
    visibility: str
    is_trashed: bool
    created_at: str
    updated_at: str
    icon: Optional[str]
    color: Optional[str]
    trashed_at: Optional[str]
    parent_id: Optional[int]
    library_id: Optional[int]
    is_smart: Optional[bool]
    smart_rules: Optional[Dict[str, Any]]


class ResourceTagRow(BaseModel):
    """One ``resource_tags`` junction row."""

    resource_id: int
    tag_id: int
    created_at: str
    tagged_by: Optional[str]
    source: Optional[str]
    confidence: Optional[float]


class ResourceTagTagRow(BaseModel):
    """The raw ``tags`` row embedded under a resource tag.

    Not :class:`app.schemas.tags.TagResponse`: that is the tags API's derived
    shape (string id, ``media_count`` …); this is the table row as-is.
    """

    id: int
    name: str
    type: str
    slug: Optional[str]
    enabled: bool
    color: Optional[str]
    icon: Optional[str]
    user_id: Optional[str]
    created_at: Optional[str]
    name_zh: Optional[str]
    scope_id: Optional[int]
    group_id: Optional[int]
    sort_order: Optional[int]
    origin: str
    prompt_trigger: bool


class ResourceTagWithTag(ResourceTagRow):
    """``GET /resources/{id}/tags``: the junction row with its tag embedded."""

    tag: ResourceTagTagRow


class GalleryChild(BaseModel):
    """One child image of a gallery, in gallery order."""

    id: int
    filename: str
    thumbnail_path: Optional[str]
    position: int
