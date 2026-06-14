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

from typing import Any, Mapping, Optional

# Classic node type keys — mirror frontend classic/registry.ts.
NODE_TYPE_LLM = "llm"
NODE_TYPE_COMFY = "comfy"

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
