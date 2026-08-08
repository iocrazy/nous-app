"""Lazy per-team seeding of the two built-in workflow templates.

``ensure_seed_templates(team_id)`` runs on the first ``GET /workflows`` for a
team and is a no-op once the team owns any template: it never mass-seeds
existing teams and never clobbers a team's own templates.

Two templates are seeded, both carrying all 11 node-bank nodes (mig 381) with
``duration_days`` NULL (schedules are filled per project, not in the template).
The production method (Live / AI / Hybrid) is NOT a template identity — it is a
per-node switch — so the two templates differ only by ``skip_default``:

    Short-form (default) — skips Voiceover, Color Grading, VFX
    Long-form            — skips VFX

Shooting is on in both (B6: the method switch is the only thing that ever
turns it off, at instantiation time — Canvas is retired as a standalone
node-bank slug, mig 412). ``review_required`` comes straight from the node
bank; ``deliverable_required`` mirrors it as the seed default (the ✓
acceptance nodes gate on a filed deliverable), overridable per template in
the editor.
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional, Protocol

from loguru import logger

# Skip matrices keyed by node-bank slug (spec §3 + mockup §01 second table).
_SHORT_FORM_SKIP: frozenset[str] = frozenset({"voiceover", "color-grading", "vfx"})
_LONG_FORM_SKIP: frozenset[str] = frozenset({"vfx"})

_SHORT_FORM_NAME = "Short-form"
_LONG_FORM_NAME = "Long-form"


class _SeederRepo(Protocol):
    """The slice of WorkflowTemplatesRepository the seeder depends on.

    Declared as a Protocol so the unit test can inject a fake without a DB."""

    async def list_templates(self, team_id: str) -> List[Dict[str, Any]]: ...

    async def list_stage_library(self) -> List[Dict[str, Any]]: ...

    async def create_template(
        self, team_id: str, name: str, created_by: Optional[str]
    ) -> Dict[str, Any]: ...

    async def update_template(
        self,
        template_id: str,
        team_id: str,
        *,
        name: Optional[str] = None,
        is_default: Optional[bool] = None,
        nodes: Optional[List[Dict[str, Any]]] = None,
    ) -> Optional[Dict[str, Any]]: ...


def _build_nodes(
    stage_library: List[Dict[str, Any]], skip_slugs: frozenset[str]
) -> List[Dict[str, Any]]:
    """Turn the node-bank rows into template-node payloads for one template.

    Sourced fields (name / sort_order / review_required / deliverable_label /
    source_stage_id) come from the node bank; owners/members are empty and
    duration is NULL by design; ``skip_default`` follows the per-template matrix.
    """
    nodes: List[Dict[str, Any]] = []
    for row in stage_library:
        slug = row.get("slug")
        review_required = bool(row.get("review_required"))
        nodes.append(
            {
                "name": row["name"],
                "sort_order": row["sort_order"],
                "parallel_group": None,  # M1 is a linear chain
                "default_owner_user_id": None,
                "default_owner_agent_id": None,
                "skip_default": slug in skip_slugs,
                "review_required": review_required,
                # Seed default: the ✓ acceptance nodes gate on a filed
                # deliverable. Overridable per template in the editor.
                "deliverable_required": review_required,
                "deliverable_label": row.get("deliverable_label"),
                "source_stage_id": (
                    str(row["id"]) if row.get("id") is not None else None
                ),
                "duration_days": None,
                "members": [],
            }
        )
    return nodes


async def ensure_seed_templates(
    team_id: str,
    *,
    repo: Optional[_SeederRepo] = None,
    created_by: Optional[str] = None,
) -> List[Dict[str, Any]]:
    """Idempotently seed Short-form + Long-form for a team.

    Returns the team's templates. No-op (returns the existing list) when the
    team already owns any template. Best-effort: seeding failures are swallowed
    with a warning so a first ``GET /workflows`` still returns a list rather
    than 500-ing on a seed hiccup.
    """
    if repo is None:
        from app.repositories.workflow_templates_repository import (
            get_workflow_templates_repository,
        )

        repo = get_workflow_templates_repository()

    existing = await repo.list_templates(team_id)
    if existing:
        return existing

    try:
        stage_library = await repo.list_stage_library()

        short = await repo.create_template(team_id, _SHORT_FORM_NAME, created_by)
        await repo.update_template(
            short["id"],
            team_id,
            is_default=True,
            nodes=_build_nodes(stage_library, _SHORT_FORM_SKIP),
        )

        long = await repo.create_template(team_id, _LONG_FORM_NAME, created_by)
        await repo.update_template(
            long["id"],
            team_id,
            is_default=False,
            nodes=_build_nodes(stage_library, _LONG_FORM_SKIP),
        )
    except Exception as exc:  # noqa: BLE001 — seeding must never break the read.
        logger.warning(f"ensure_seed_templates failed for team {team_id}: {exc}")

    return await repo.list_templates(team_id)
