"""Response shapes of the admin tags console (``/api/v1/admin/tags*``).

Every field mirrors what ``AdminTagsRepository`` already emits, so declaring
these models changes no byte on the wire:

- ids (``tags.id`` / ``group_id`` / ``scope_id`` / ``tag_groups.id``) are
  Snowflake BIGINTs the repository returns as native ints → JSON numbers.
  ``generate_snowflake_id()`` is 53-bit safe (mig 050), so a JS ``number``
  holds them exactly.
- ``created_at`` is turned into an ISO string by the repository itself
  (``_parity``), so it is declared ``str`` and passes through untouched.
- ``tags.user_id`` (uuid) is str()'d by the repository.

Row models list every column of the table; ``tests/api/admin/
test_admin_tags_wire.py`` pins them to the ORM column set, so a column added
to ``tags`` / ``tag_groups`` without a field here turns that test red instead
of being silently dropped from the response.
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel


class AdminTagGroupRow(BaseModel):
    """One ``tag_groups`` row, ``SELECT *`` shape."""

    id: int
    name: str
    sort_order: int | None
    created_at: str | None


class AdminTagGroupListItem(AdminTagGroupRow):
    tag_count: int


class AdminTagGroupListResponse(BaseModel):
    success: bool = True
    groups: list[AdminTagGroupListItem]
    total_tags: int
    uncategorized_count: int


class AdminTagGroupResponse(BaseModel):
    success: bool = True
    group: AdminTagGroupRow


class AdminTagRow(BaseModel):
    """One ``tags`` row, ``SELECT *`` shape."""

    id: int
    name: str
    # tags_type_check (mig 468): 'system' is gone, these two are all there is.
    type: Literal["user", "time"]
    slug: str | None
    enabled: bool
    color: str | None
    icon: str | None
    user_id: str | None
    created_at: str | None
    name_zh: str | None
    scope_id: int | None
    group_id: int | None
    sort_order: int | None
    # Not narrowed to a Literal: no CHECK constraint backs it.
    origin: str
    prompt_trigger: bool


class AdminTagListItem(AdminTagRow):
    group_name: str | None
    usage_count: int


class AdminTagListResponse(BaseModel):
    success: bool = True
    items: list[AdminTagListItem]
    total: int


class AdminTagResponse(BaseModel):
    success: bool = True
    tag: AdminTagRow


class AdminTagsOk(BaseModel):
    """Body of the writes that return nothing but success."""

    success: bool = True


class AdminTagBatchResult(BaseModel):
    success: bool = True
    message: str
