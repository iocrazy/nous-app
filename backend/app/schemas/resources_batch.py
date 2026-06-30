# app/schemas/resources_batch.py

"""
Batch duplicate-check schemas.

Used by POST /resources/check-duplicates for bulk import deduplication.
"""

from typing import Optional

from pydantic import BaseModel, Field


class CheckDuplicatesItem(BaseModel):
    """Single file entry in a batch duplicate-check request."""

    file_hash: str = Field(..., min_length=64, max_length=64)
    file_size: int = Field(..., gt=0)


class CheckDuplicatesRequest(BaseModel):
    """Batch duplicate-check request body.

    At most 200 items per request (PostgREST .in_ / URL-length cap).
    The endpoint returns HTTP 422 if this limit is exceeded.
    """

    items: list[CheckDuplicatesItem]


class CheckDuplicatesResultItem(BaseModel):
    """Duplicate-check result for one file."""

    file_hash: str
    duplicate: bool
    existing: Optional[dict] = None


class CheckDuplicatesResponse(BaseModel):
    """Batch duplicate-check response.

    Results list matches the input ``items`` list in order.
    """

    results: list[CheckDuplicatesResultItem]
