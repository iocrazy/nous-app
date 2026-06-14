"""Pydantic schemas for canvas-mode prompt runs (Phase 2 Day 6).

The smart-canvas frontend POSTs one of these per prompt-node "Run".
Wire-shape is intentionally narrow — no skills, no tool calls, no
history. The smart canvas is a single-turn artistic generator; the
agent runner (with its skill/tool/history machinery) lives in the AI
Library chat path and is overkill here.
"""

from __future__ import annotations

from typing import Any, Dict, Literal, Optional

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
    result: Optional[Dict[str, Any]] = Field(
        default=None,
        description=(
            "Structured op output for non-text nodes (e.g. image_gen carries "
            "{image_url, width, height, ...}). None for plain text runs."
        ),
    )
    response_kind: Literal["canvas_prompt_run"] = "canvas_prompt_run"


class CanvasPromptRunResult(BaseModel):
    """Internal service-layer return shape — mirrors the response wire
    shape but without the discriminator tag.

    ``result`` carries structured op output (e.g. ``{"image_url": ...}`` for
    an image_gen node). It stays None for the plain text adapter / nous paths;
    those put their content in ``text``.
    """

    ok: bool
    text: str = ""
    error: Optional[str] = None
    result: Optional[Dict[str, Any]] = None


# ---------------------------------------------------------------------------
# ClassicMode node run (Phase 5a path B)
# ---------------------------------------------------------------------------


class ClassicNodePayload(BaseModel):
    """One ClassicMode node: type + opaque data + id.

    Mirrors the frontend ``features/canvas-core/classic/registry.ts`` node
    shape closely enough to dispatch server-side. ``data`` is opaque on the
    wire — the dispatch layer reads provider/workflow/op params out of it.
    """

    id: str = Field(..., description="Classic node id")
    type: str = Field(..., description="Classic node type (llm/comfy/image_gen/...)")
    data: Dict[str, Any] = Field(default_factory=dict)


class ClassicNodeRunRequest(BaseModel):
    """Frontend → backend payload for running one ClassicMode node.

    Replaces the old frontend dispatch-mirror: the cascade no longer guesses
    a provider_slug client-side; it hands the node here and the server resolves
    the route (provider vs op vs reject).
    """

    canvas_id: str = Field(..., description="Snowflake canvas id (string)")
    node: ClassicNodePayload
    body: str = Field(
        default="",
        max_length=10_000,
        description=(
            "Upstream/aggregated text fed into the node (e.g. the prompt for "
            "an llm node). image_gen prefers its own data.prompt but falls "
            "back to this."
        ),
    )
    agent_id: Optional[str] = Field(
        default=None,
        description="AI Library agent id (UUID) for persona injection. Null = bare.",
    )


class ClassicNodeRunResponse(BaseModel):
    """Backend → frontend reply for a ClassicMode node run.

    Same in-band contract as CanvasPromptRunResponse: ``ok=False`` always
    carries a non-null ``error``; HTTP stays 200 for normal runs. ``result``
    carries structured output (image_url for image_gen).
    """

    ok: bool
    text: str = ""
    error: Optional[str] = None
    result: Optional[Dict[str, Any]] = None
    response_kind: Literal["classic_node_run"] = "classic_node_run"
