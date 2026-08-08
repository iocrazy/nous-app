"""Lazy per-team seeding of the two built-in workflow templates.

Service-layer unit tests with a fake repo (no DB): an empty team gets exactly
two templates (Short-form default + Long-form), re-running is a no-op, and the
skip matrix / duration NULL / is_default flags match the spec.
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional

import pytest

from app.services.workflow.template_seeder import ensure_seed_templates

# ── node bank fixture (the 10 seeded nodes, phase-not-null) ──────────────────
_STAGE_LIBRARY: List[Dict[str, Any]] = [
    {
        "id": 1,
        "slug": "script",
        "name": "Script",
        "sort_order": 10,
        "review_required": True,
        "deliverable_label": "Final script",
    },
    {
        "id": 2,
        "slug": "storyboard",
        "name": "Storyboard",
        "sort_order": 20,
        "review_required": True,
        "deliverable_label": "Shot list + boards",
    },
    {
        "id": 3,
        "slug": "voiceover",
        "name": "Voiceover",
        "sort_order": 30,
        "review_required": False,
        "deliverable_label": "VO track",
    },
    {
        "id": 5,
        "slug": "shooting",
        "name": "Shooting",
        "sort_order": 50,
        "review_required": False,
        "deliverable_label": "Raw footage package",
    },
    {
        "id": 6,
        "slug": "editing",
        "name": "Editing",
        "sort_order": 60,
        "review_required": True,
        "deliverable_label": "A/B copy",
    },
    {
        "id": 7,
        "slug": "color-grading",
        "name": "Color Grading",
        "sort_order": 70,
        "review_required": False,
        "deliverable_label": "Graded copy",
    },
    {
        "id": 8,
        "slug": "vfx",
        "name": "VFX",
        "sort_order": 80,
        "review_required": False,
        "deliverable_label": "VFX shots",
    },
    {
        "id": 9,
        "slug": "post-delivery",
        "name": "Post Delivery",
        "sort_order": 90,
        "review_required": True,
        "deliverable_label": "Final cut upload",
    },
    {
        "id": 10,
        "slug": "distribution",
        "name": "Distribution",
        "sort_order": 100,
        "review_required": False,
        "deliverable_label": "Published links",
    },
    {
        "id": 11,
        "slug": "retrospective",
        "name": "Retrospective",
        "sort_order": 110,
        "review_required": False,
        "deliverable_label": "Retro report",
    },
]


class _FakeRepo:
    def __init__(self) -> None:
        self._templates: Dict[str, Dict[str, Any]] = {}
        self._seq = 1000
        self.create_calls = 0

    async def list_templates(self, team_id: str) -> List[Dict[str, Any]]:
        return [
            {
                "id": t["id"],
                "name": t["name"],
                "is_default": t["is_default"],
                "nodes": t["nodes"],
            }
            for t in self._templates.values()
            if t["team_id"] == str(team_id)
        ]

    async def list_stage_library(self) -> List[Dict[str, Any]]:
        return [dict(row) for row in _STAGE_LIBRARY]

    async def create_template(
        self, team_id: str, name: str, created_by: Optional[str]
    ) -> Dict[str, Any]:
        self._seq += 1
        self.create_calls += 1
        tid = str(self._seq)
        self._templates[tid] = {
            "id": tid,
            "team_id": str(team_id),
            "name": name,
            "is_default": False,
            "nodes": [],
        }
        return {"id": tid, "name": name}

    async def update_template(
        self,
        template_id: str,
        team_id: str,
        *,
        name: Optional[str] = None,
        is_default: Optional[bool] = None,
        nodes: Optional[List[Dict[str, Any]]] = None,
    ) -> Optional[Dict[str, Any]]:
        tpl = self._templates.get(str(template_id))
        if tpl is None or tpl["team_id"] != str(team_id):
            return None
        if is_default is not None:
            tpl["is_default"] = is_default
        if nodes is not None:
            tpl["nodes"] = nodes
        return {"id": template_id}


@pytest.mark.asyncio
async def test_empty_team_gets_two_templates():
    repo = _FakeRepo()
    result = await ensure_seed_templates("42", repo=repo)

    assert repo.create_calls == 2
    names = sorted(t["name"] for t in result)
    assert names == ["Long-form", "Short-form"]


@pytest.mark.asyncio
async def test_short_form_is_default_long_form_is_not():
    repo = _FakeRepo()
    result = await ensure_seed_templates("42", repo=repo)

    by_name = {t["name"]: t for t in result}
    assert by_name["Short-form"]["is_default"] is True
    assert by_name["Long-form"]["is_default"] is False


@pytest.mark.asyncio
async def test_both_templates_carry_all_10_nodes_with_null_duration():
    repo = _FakeRepo()
    result = await ensure_seed_templates("42", repo=repo)

    for tpl in result:
        assert len(tpl["nodes"]) == 10
        assert all(n["duration_days"] is None for n in tpl["nodes"])
        assert all(n["parallel_group"] is None for n in tpl["nodes"])


@pytest.mark.asyncio
async def test_skip_matrix_matches_spec():
    repo = _FakeRepo()
    result = await ensure_seed_templates("42", repo=repo)
    by_name = {t["name"]: t for t in result}

    def skipped(tpl: Dict[str, Any]) -> set[str]:
        # source_stage_id maps back to the node-bank row; use name for clarity.
        return {n["name"] for n in tpl["nodes"] if n["skip_default"]}

    assert skipped(by_name["Short-form"]) == {"Voiceover", "Color Grading", "VFX"}
    assert skipped(by_name["Long-form"]) == {"VFX"}


@pytest.mark.asyncio
async def test_deliverable_required_mirrors_review_required():
    repo = _FakeRepo()
    result = await ensure_seed_templates("42", repo=repo)
    nodes = {n["name"]: n for n in result[0]["nodes"]}

    # ✓ acceptance nodes gate on a deliverable; the others do not.
    assert nodes["Script"]["deliverable_required"] is True
    assert nodes["Voiceover"]["deliverable_required"] is False
    assert nodes["Shooting"]["deliverable_required"] is False


@pytest.mark.asyncio
async def test_reentry_is_noop_when_team_already_has_a_template():
    repo = _FakeRepo()
    await ensure_seed_templates("42", repo=repo)
    assert repo.create_calls == 2

    # Second call: team already owns templates → no new creates.
    result = await ensure_seed_templates("42", repo=repo)
    assert repo.create_calls == 2
    assert len(result) == 2


@pytest.mark.asyncio
async def test_seeding_is_per_team():
    repo = _FakeRepo()
    await ensure_seed_templates("42", repo=repo)
    # A different team starts empty and seeds its own pair.
    result = await ensure_seed_templates("99", repo=repo)
    assert repo.create_calls == 4
    assert len(result) == 2
