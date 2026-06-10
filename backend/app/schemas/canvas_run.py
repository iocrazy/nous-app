"""Pydantic schemas for canvas-mode prompt runs (Phase 2 Day 6).

The smart-canvas frontend POSTs one of these per prompt-node "Run".
Wire-shape is intentionally narrow — no skills, no tool calls, no
history. The smart canvas is a single-turn artistic generator; the
agent runner (with its skill/tool/history machinery) lives in the AI
Library chat path and is overkill here.
"""

from __future__ import annotations

from typing import Literal, Optional

from pydantic import BaseModel, Field


class CanvasPromptRunRequest(BaseModel):
    """Frontend → backend payload for one Run."""

    canvas_id: str = Field(..., description="Snowflake canvas id (string)")
    prompt_node_id: str = Field(..., description="Smart-mode prompt node id")
    body: str = Field(..., max_length=10_000)
    provider_slug: Optional[str] = Field(
        default=None,
        description="Provider override; null means service picks the default.",
    )
    agent_id: Optional[str] = Field(
        default=None,
        description=(
            "AI Library agent id (UUID) when the prompt should run under "
            "an agent's persona. Null = bare model call."
        ),
    )


class CanvasPromptRunResponse(BaseModel):
    """Backend → frontend reply.

    `ok=False` ALWAYS carries a non-null `error` and an empty `text`.
    The HTTP layer always returns 200 for normal runs (errors are
    in-band) — 5xx is reserved for the request being malformed or the
    backend itself being broken.
    """

    ok: bool
    text: str = ""
    error: Optional[str] = None
    response_kind: Literal["canvas_prompt_run"] = "canvas_prompt_run"


class CanvasPromptRunResult(BaseModel):
    """Internal service-layer return shape — mirrors the response wire
    shape but without the discriminator tag."""

    ok: bool
    text: str = ""
    error: Optional[str] = None
