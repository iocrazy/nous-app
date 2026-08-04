"""Resolving a selection attachment to something an agent may edit (A5,
agent-layer spec §5.3).

A selection arrives as ``{scene_id, element_ids, summary_text}`` (see
``app/schemas/script_selection.py``). Turning that into an editable target is
TWO authorization questions, not one, and only the first has an existing
answer:

  1. **May this run touch that scene?** — A2's ``resolve_scene``. This module
     does not re-implement, wrap or second-guess it, and deliberately owns no
     SQL of its own: the ``script_scenes -> script_projects`` join exists in
     exactly one place and ``test_scope_resolver_single_choke_point.py``
     keeps it that way.
  2. **Are those element ids actually in that scene?** — answered HERE,
     because element ids are not rows and no resolver covers them. This is
     the check that turns "the model named some strings" into "the model
     named a passage": an id from another scene, from another tenant's
     script, or invented outright fails identically, and lands in the audit
     trail either way.

Question 2 is not merely hygiene. Without it, ``ApplyEdit`` would hand
``apply_element_ops`` an id it never verified; the op protocol would raise
``OpError('unknown_element')`` deep inside a write transaction, and the model
would see a generic failure instead of "that id isn't in this scene". Worse,
an id that HAPPENS to exist in the target scene but came from the model's
memory of a different one would be silently accepted as a legitimate target.
Validating against the resolved scene's own ``content_json`` closes both.

WHAT THIS MODULE DOES NOT DO: it never reads content by any path other than
the ``ResolvedScene`` handle A2 already fetched. There is therefore no way
for a caller to get element text out of here for a scene it was not
authorized for — the handle IS the authorization proof (see
``scoped_script_gateway``'s module docstring).
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any, Optional

from app.schemas.script_selection import (
    ParsedSelection,
    SelectionRejected,
    parse_selection,
)

from .agent_run_scope import AgentRunScope
from .scope_resolver import Denied, ResolvedScene, audit_resolution, resolve_scene

logger = logging.getLogger(__name__)

# How many unknown ids to name back to the model. Enough to fix a mistake in
# one more turn, few enough that a scattershot 50-id probe does not get a
# 50-line confirmation of which ids exist (the anti-enumeration reasoning in
# scope_resolver's docstring applies to element ids too — with the difference
# that these are all inside a scene the run is ALREADY authorized for, so the
# leak is bounded to content the caller may read anyway).
_MAX_REPORTED_UNKNOWN = 10


@dataclass(frozen=True)
class ResolvedSelection:
    """A selection whose scene is authorized and whose element ids are known
    to exist in it. Carries the resolved scene handle so callers do not
    re-resolve (and cannot accidentally resolve something else).

    ``elements`` is the FULL element dicts in scene order — not the caller's
    id order — because the passage's reading order is the scene's, and an
    agent reasoning about a selection that lists them out of order would
    rewrite them out of order."""

    scene: ResolvedScene
    element_ids: tuple[str, ...]
    elements: tuple[dict[str, Any], ...]
    summary_text: str

    @property
    def base_content_version(self) -> int:
        """The scene version this selection was resolved against — the value
        an edit must quote back as its precondition."""
        return self.scene.content_version


def _scene_elements(scene: ResolvedScene) -> list[dict[str, Any]]:
    """Element dicts off the resolved handle. Mirrors
    ``scoped_script_gateway._elements``' tolerance: a legacy row holding
    ``{}`` or a non-list reads as "no elements" rather than raising, so a
    malformed scene degrades one selection instead of a whole turn."""
    content = scene.content_json
    if not isinstance(content, list):
        return []
    return [el for el in content if isinstance(el, dict) and el.get("id")]


async def resolve_selection(
    raw: Any, scope: AgentRunScope
) -> Any:  # ResolvedSelection | SelectionRejected | Denied
    """Validate, authorize and bind a selection payload.

    Returns :class:`ResolvedSelection`, :class:`SelectionRejected` (malformed
    shape or unknown element ids) or A2's :class:`Denied` (the scene is not
    this run's to touch). Never raises — every caller is a tool handler, and
    the house rule there is that a failure comes back as a readable result.

    ``raw`` may be the dict a model produced or an already-parsed
    :class:`ParsedSelection` (the panel path, once A7 posts one).
    """
    parsed = raw if isinstance(raw, ParsedSelection) else parse_selection(raw)
    if isinstance(parsed, SelectionRejected):
        return parsed

    scene = await resolve_scene(parsed.scene_id, scope)
    if isinstance(scene, Denied):
        return scene

    by_id = {el["id"]: el for el in _scene_elements(scene)}
    unknown = [eid for eid in parsed.element_ids if eid not in by_id]
    if unknown:
        # Audited: a foreign/stale/invented element id is an access attempt
        # the resolver never saw, because it only ever saw the scene id.
        await audit_resolution(
            scope,
            "scene_element",
            f"{parsed.scene_id}:{','.join(unknown[:_MAX_REPORTED_UNKNOWN])}",
            granted=False,
            detail_code="element_not_in_scene",
        )
        logger.info(
            "[script_selection] rejected run=%s scene=%s unknown_elements=%s",
            scope.run_id,
            parsed.scene_id,
            unknown[:_MAX_REPORTED_UNKNOWN],
        )
        return SelectionRejected(
            "unknown_element",
            (
                "These element ids are not in that scene: "
                f"{', '.join(unknown[:_MAX_REPORTED_UNKNOWN])}. Re-read the "
                "scene with ReadScene and anchor to ids it actually contains."
            ),
        )

    selected = set(parsed.element_ids)
    ordered = [el for el in _scene_elements(scene) if el["id"] in selected]
    return ResolvedSelection(
        scene=scene,
        element_ids=tuple(el["id"] for el in ordered),
        elements=tuple(ordered),
        summary_text=parsed.summary_text,
    )


def selection_from_run_context(run_context: dict) -> Optional[Any]:
    """The selection the writer attached to this turn, if any.

    THE SEAM FOR A7, and the reason it is a lookup rather than a parameter:
    the panel does not exist yet, so nothing populates ``run_context
    ['selection']`` today. When A7 wires the floating panel, it posts a
    ``ScriptSelectionAttachment``, and the chat service puts the
    ``ParsedSelection`` (via ``selection_from_attachment``) on the run
    context — no tool signature changes, and the tools below already prefer
    it over model-supplied ids (which is the point: the writer's actual
    selection outranks the model's recollection of it).

    Returns a ``ParsedSelection``/dict for ``resolve_selection`` to consume,
    or ``None``."""
    return (run_context or {}).get("selection")


__all__ = [
    "ResolvedSelection",
    "resolve_selection",
    "selection_from_run_context",
]
