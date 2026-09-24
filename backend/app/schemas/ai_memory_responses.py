"""Response models for the ``/ai/memory`` write routes (OpenAPI P5).

The Honcho memory layer was switched off in production on 2026-09-22
(``honcho_memory_enabled=false``, ``memory.l2_provider=none``). The routes
stay because ``MemoryPanel`` still calls them; with the service down, the
card write and a single delete answer 502 / 404, forget-all answers
``{"deleted": 0}`` and the prefs toggle still works (it only touches
``user_settings``). These models declare the 200 bodies as they are.
"""

from __future__ import annotations

from typing import List

from pydantic import BaseModel


class AiMemoryPrefsResponse(BaseModel):
    """``PUT /ai/memory/prefs`` — the effective switches after the write."""

    learn_enabled: bool
    inject_enabled: bool


class AiMemoryCardResponse(BaseModel):
    """``PUT /ai/memory/card`` — the stripped, non-empty lines saved."""

    saved: bool
    lines: List[str]


class AiMemoryObservationDeleteResponse(BaseModel):
    """``DELETE /ai/memory/observations/{conclusion_id}``."""

    deleted: str


class AiMemoryForgetResponse(BaseModel):
    """``DELETE /ai/memory`` — number of observations deleted."""

    deleted: int
