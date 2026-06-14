"""ClassicMode node-type → provider_slug dispatch map (Phase 5a C2).

ClassicMode is the ComfyUI-style node canvas. Each classic node carries a
``type`` (image / prompt / llm / comfy / output — mirror of the frontend
``features/canvas-core/classic/registry.ts``). When a node "runs" it must
dispatch to an AI provider. This module maps a node type + its data payload
to the ``provider_slug`` string that ``CanvasRunService.run_prompt`` already
understands — it does NOT invent a parallel runner:

    llm   → text provider routing: the node's configured model slug
            (e.g. "anthropic/claude-sonnet-4-6") or None for the default
            model. Reuses the bare-model adapter path in run_prompt.
    comfy → "nous/<workflow_slug>", where <workflow_slug> is read from the
            node's data so distinct comfy graphs route to distinct
            nous-center workflows. Reuses the existing ``nous/`` route in
            run_prompt (which block-polls run_nous_workflow to terminal).

Literal/sink node types (image / prompt / output) hold data or collect
results — they do not dispatch to a provider, so they have no mapping.
Asking for their provider_slug, or for an unknown node type, raises
``ClassicDispatchError`` so the caller surfaces a clear failure instead of
silently routing to the wrong provider.

Execution stays SINGLE-SYNCHRONOUS: comfy runs through the same synchronous
run path (run_nous_workflow block-polls to a terminal state). No DBOS
workflow is created here — that is Phase 6.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping, Optional

# Classic node type keys — mirror frontend classic/registry.ts.
NODE_TYPE_LLM = "llm"
NODE_TYPE_COMFY = "comfy"
# Runnable OP node types (Phase 5a path B): these are NOT provider_slugs —
# they call an already-implemented storyboard service directly.
NODE_TYPE_IMAGE_GEN = "image_gen"
NODE_TYPE_VIDEO_GEN = "video_gen"
# Next task (Phase 5a path B cont.) will add:
#   NODE_TYPE_SPLIT = "split"  → op "split"  (StoryboardAIService.split_script).
#   split is held back: it needs local-file plumbing (the source media must be
#   on disk for the splitter), unlike image_gen/video_gen which call the
#   provider directly and return a URL.

# Op kind identifiers (the ``op`` field on a ClassicDispatch with kind="op").
OP_IMAGE_GEN = "image_gen"
OP_VIDEO_GEN = "video_gen"

# Data-payload keys we accept for the comfy workflow slug (snake + camel).
_COMFY_WORKFLOW_KEYS = ("workflow_slug", "workflowSlug")
# Data-payload keys we accept for an llm node's model / provider override.
_LLM_PROVIDER_KEYS = ("provider_slug", "providerSlug", "model")


class ClassicDispatchError(ValueError):
    """A classic node type cannot be dispatched to a provider.

    Carries a human-readable message the run endpoint can surface in-band
    (ok=False, error=...). Distinct from a silent wrong-route: we raise
    rather than guess a provider.
    """


@dataclass(frozen=True)
class ClassicDispatch:
    """Resolved route for a classic node.

    Two runnable kinds today:

      - ``kind="provider"`` — route through ``run_prompt`` with ``provider_slug``
        (the llm text adapter, or ``nous/<workflow>`` for comfy). ``provider_slug``
        may be None for an llm node with no override (= default model).
      - ``kind="op"``       — a direct, already-implemented service call that is
        NOT a provider_slug. ``op`` names it (e.g. ``"image_gen"`` /
        ``"video_gen"``). split will add one more op branch in
        ``resolve_classic_dispatch`` plus one more handler in
        ``CanvasRunService.run_classic_node`` — no other wiring changes.

    (Literal/sink "passive" node types — image / prompt / output — don't reach
    here: ``resolve_classic_dispatch`` raises ``ClassicDispatchError`` for them,
    same as today, so we never silently route them anywhere.)
    """

    kind: str  # "provider" | "op"
    provider_slug: Optional[str] = None  # set when kind == "provider"
    op: Optional[str] = None  # set when kind == "op"


def _node_data(node: Mapping[str, Any]) -> Mapping[str, Any]:
    """Pull the opaque data dict off a node payload.

    React Flow nodes nest their domain payload under ``data``; we also accept
    a flat node dict (data merged at the top level) so callers aren't forced
    into one shape.
    """
    data = node.get("data")
    if isinstance(data, Mapping):
        return data
    return node


def _first_str(data: Mapping[str, Any], keys: tuple[str, ...]) -> Optional[str]:
    for key in keys:
        value = data.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    return None


def resolve_provider_slug(
    node_type: Optional[str],
    node: Optional[Mapping[str, Any]] = None,
) -> Optional[str]:
    """Map a classic node type + its data payload to a ``provider_slug``.

    Returns the provider_slug string ``CanvasRunService.run_prompt`` expects:
      - ``llm``   → the node's configured model slug, or None (default model).
      - ``comfy`` → "nous/<workflow_slug>" read from the node data.

    Raises ``ClassicDispatchError`` when the node type is unknown, is a
    literal/sink type with no provider mapping, or is a comfy node missing
    its workflow_slug — so the caller never silently routes to a wrong
    provider. None is only ever returned for an llm node with no override
    (meaning "use the default model"); it is not the no-mapping signal.
    """
    data = _node_data(node or {})

    if node_type == NODE_TYPE_LLM:
        return _first_str(data, _LLM_PROVIDER_KEYS)

    if node_type == NODE_TYPE_COMFY:
        workflow_slug = _first_str(data, _COMFY_WORKFLOW_KEYS)
        if not workflow_slug:
            raise ClassicDispatchError(
                "comfy node is missing a workflow_slug in its data; "
                "cannot route to nous-center"
            )
        return f"nous/{workflow_slug}"

    if not node_type:
        raise ClassicDispatchError("classic node is missing a type")

    raise ClassicDispatchError(
        f"classic node type '{node_type}' has no provider mapping"
    )


def resolve_classic_dispatch(
    node_type: Optional[str],
    node: Optional[Mapping[str, Any]] = None,
) -> ClassicDispatch:
    """Resolve a classic node to its runnable route (provider vs op).

    This is the server-side route resolver that replaces the frontend
    dispatch-mirror. It branches:

      - ``image_gen`` → ``ClassicDispatch(kind="op", op="image_gen")`` — a direct
        ``StoryboardAIService.generate_image`` call, NOT a provider_slug.
      - ``video_gen`` → ``ClassicDispatch(kind="op", op="video_gen")`` — a direct
        ``StoryboardAIService.generate_video`` call (distinct video provider
        registry), NOT a provider_slug.
      - everything else (llm / comfy) → ``ClassicDispatch(kind="provider", ...)``
        delegating to ``resolve_provider_slug`` so those paths behave EXACTLY as
        before.

    Unknown / literal-sink / missing types still raise ``ClassicDispatchError``
    (via ``resolve_provider_slug``) — never a silent wrong route.

    NEXT TASK: add ``split`` here as one more ``op`` branch (kind="op",
    op="split") plus one handler in ``CanvasRunService.run_classic_node`` — held
    back for now because split needs local-file plumbing the URL-returning gen
    ops don't.
    """
    if node_type == NODE_TYPE_IMAGE_GEN:
        return ClassicDispatch(kind="op", op=OP_IMAGE_GEN)

    if node_type == NODE_TYPE_VIDEO_GEN:
        return ClassicDispatch(kind="op", op=OP_VIDEO_GEN)

    # Future op branch (split) slots in right here.

    provider_slug = resolve_provider_slug(node_type, node)
    return ClassicDispatch(kind="provider", provider_slug=provider_slug)
