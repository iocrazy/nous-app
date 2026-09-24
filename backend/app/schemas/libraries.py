# app/schemas/libraries.py

"""
Library validation schemas.

Pydantic models for team library CRUD operations.
"""

from typing import Optional

from pydantic import BaseModel, Field

from app.schemas.envelope import Envelope


class LibraryCreate(BaseModel):
    """Request body for creating a library."""

    name: str = Field(..., min_length=1, max_length=200)
    scope_type: str = Field(default="team", pattern="^team$")
    scope_id: str = Field(..., description="Team ID")
    icon: Optional[str] = Field(None, max_length=50)
    color: Optional[str] = Field(None, max_length=20)


class LibraryUpdate(BaseModel):
    """Request body for updating a library."""

    name: Optional[str] = Field(None, min_length=1, max_length=200)
    icon: Optional[str] = Field(None, max_length=50)
    color: Optional[str] = Field(None, max_length=20)
    sort_order: Optional[int] = None
    visibility: Optional[str] = Field(None, pattern="^(inherited|restricted)$")


# --------------------------------------------------------------------------- #
# Responses (P7). Each mirrors ``libraries_repository._library_to_dict`` key
# for key: ``id`` stays a JSON number (BIGINT), ``created_by`` is already a
# str, timestamps are already ISO strings. Wire tests:
# ``tests/api/test_libraries_wire.py``.
# --------------------------------------------------------------------------- #


class LibraryRow(BaseModel):
    """One ``libraries`` row as the routes return it."""

    id: int
    name: str
    scope_type: str
    scope_id: str
    created_by: str
    visibility: str
    icon: Optional[str]
    color: Optional[str]
    sort_order: Optional[int]
    created_at: Optional[str]
    updated_at: Optional[str]


class LibraryResponse(Envelope[LibraryRow]):
    pass


class LibraryListResponse(Envelope[list[LibraryRow]]):
    pass


class LibraryDeleteResponse(BaseModel):
    """``DELETE /libraries/{id}``: no ``data`` key."""

    success: bool = True
    message: str
