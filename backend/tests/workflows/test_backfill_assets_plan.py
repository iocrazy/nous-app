"""Unit tests for the assets migration planner (pure) + admin dispatch wiring.

``plan_migration`` is pure: no DB, no DBOS. The merge policy it encodes is the
one place a wrong decision would silently fuse two different people's
characters into one team asset, so every branch of it is pinned here.
"""

import inspect
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest

from app.api.admin.backfill_router import _BACKFILLS, workflow_kwargs
from app.workflows.backfill_assets_from_project_entities import (
    plan_migration,
    reconcile_counts,
)

TEAM_A, TEAM_B = 100, 200
P1, P2, P3 = 11, 12, 13  # P1,P2 in team A; P3 in team B
P_PERSONAL = 14  # personal project — projects.team_id IS NULL
PROJECT_TEAM = {P1: TEAM_A, P2: TEAM_A, P3: TEAM_B}


def _char(id, project_id, name, **kw):
    return {
        "id": id,
        "project_id": project_id,
        "name": name,
        "role_tag": kw.get("role_tag", ""),
        "description": kw.get("description", ""),
        "tags": kw.get("tags", {}),
        "portrait_url": kw.get("portrait_url"),
        "source": kw.get("source", "manual"),
    }


def _ent(id, project_id, entity_type, name, **kw):
    return {
        "id": id,
        "project_id": project_id,
        "entity_type": entity_type,
        "name": name,
        "badge_tag": kw.get("badge_tag", ""),
        "description": kw.get("description", ""),
        "tags": kw.get("tags", {}),
        "cover_url": kw.get("cover_url"),
        "source": kw.get("source", "manual"),
    }


def test_one_character_becomes_one_asset_with_project_ref():
    plan = plan_migration([_char(1, P1, "Sang Yao", role_tag="lead")], [], PROJECT_TEAM)
    assert plan["counts"] == {
        "characters": 1,
        "entities": 0,
        "assets": 1,
        "merges": 0,
        "skipped_unknown_project": 0,
        "skipped_personal_project": 0,
    }
    a = plan["assets"][0]
    assert a["scope_id"] == TEAM_A and a["asset_type"] == "character"
    assert a["role_tag"] == "lead"
    assert a["project_ids"] == [P1] and a["legacy"] == [("project_characters", 1)]
    assert a["source"] == "migrated"


def test_same_name_same_team_merges_and_keeps_both_project_refs():
    plan = plan_migration(
        [_char(1, P1, "Old Zhang"), _char(2, P2, "old zhang", description="v2")],
        [],
        PROJECT_TEAM,
    )
    assert plan["counts"]["assets"] == 1 and plan["counts"]["merges"] == 1
    a = plan["assets"][0]
    assert sorted(a["project_ids"]) == [P1, P2]
    assert a["legacy"] == [("project_characters", 1), ("project_characters", 2)]
    assert a["description"] == "v2"  # longest description wins
    assert plan["merges"][0]["name"] == "Old Zhang"


def test_merge_fills_role_tag_and_cover_from_a_later_row():
    """First non-empty wins for both — a row that merely lacks a role_tag or a
    portrait must not blank out one that has them."""
    plan = plan_migration(
        [
            _char(1, P1, "Old Zhang"),
            _char(2, P2, "Old Zhang", role_tag="lead", portrait_url="p.png"),
            _char(3, P1, "Old Zhang", role_tag="support", portrait_url="q.png"),
        ],
        [],
        PROJECT_TEAM,
    )
    a = plan["assets"][0]
    assert a["role_tag"] == "lead" and a["cover_url"] == "p.png"


def test_same_name_different_team_does_not_merge():
    plan = plan_migration(
        [_char(1, P1, "Old Zhang"), _char(3, P3, "Old Zhang")], [], PROJECT_TEAM
    )
    assert plan["counts"]["assets"] == 2 and plan["counts"]["merges"] == 0


def test_entities_map_type_and_badge_tag():
    plan = plan_migration(
        [],
        [
            _ent(
                9,
                P1,
                "location",
                "Bamboo Grove",
                badge_tag="exterior",
                cover_url="x.png",
            )
        ],
        PROJECT_TEAM,
    )
    a = plan["assets"][0]
    assert a["asset_type"] == "location" and a["role_tag"] == "exterior"
    assert a["cover_url"] == "x.png"
    assert a["legacy"] == [("project_lib_entities", 9)]


def test_character_and_prop_with_same_name_do_not_merge():
    plan = plan_migration(
        [_char(1, P1, "Blade")], [_ent(9, P1, "prop", "Blade")], PROJECT_TEAM
    )
    assert plan["counts"]["assets"] == 2


def test_unknown_project_is_skipped_and_counted():
    plan = plan_migration([_char(1, 999, "Ghost")], [], PROJECT_TEAM)
    assert plan["counts"]["assets"] == 0
    assert plan["counts"]["skipped_unknown_project"] == 1
    assert plan["counts"]["skipped_personal_project"] == 0


def test_personal_project_is_counted_apart_from_unknown():
    """A personal project (projects.team_id IS NULL) is not a data hole — P3
    decides how it maps to a personal team, so it must not hide inside the
    "unknown project" bucket that means "this row's project is gone"."""
    plan = plan_migration(
        [_char(1, P_PERSONAL, "Solo"), _char(2, 999, "Ghost")],
        [_ent(9, P_PERSONAL, "prop", "Sword")],
        PROJECT_TEAM,
        personal_project_ids={P_PERSONAL},
    )
    assert plan["counts"]["assets"] == 0 and plan["assets"] == []
    assert plan["counts"]["skipped_personal_project"] == 2
    assert plan["counts"]["skipped_unknown_project"] == 1


def test_plan_is_pure_and_repeatable():
    """No hidden state: the same inputs plan identically, and the inputs are
    not mutated (a merged group must not write back into its source rows)."""
    chars = [_char(1, P1, "Old Zhang", tags={"k": "v"}), _char(2, P2, "Old Zhang")]
    before = [dict(c) for c in chars]
    first = plan_migration(chars, [], PROJECT_TEAM)
    second = plan_migration(chars, [], PROJECT_TEAM)
    assert first == second
    assert chars == before
    first["assets"][0]["tags"]["mutated"] = True
    assert chars[0]["tags"] == {"k": "v"}


class TestRouterWiring:
    def test_registered(self):
        assert "assets_from_project_entities" in _BACKFILLS

    def test_run_user_id_passed_and_limit_omitted(self):
        """The planner takes no ``limit`` (a partial scan would produce a wrong
        merge plan), so dispatch must not pass one; workflows that do take it
        still get it."""
        body = SimpleNamespace(dry_run=True, limit=10)
        assert workflow_kwargs("assets_from_project_entities", body, "admin-uuid") == {
            "dry_run": True,
            "run_user_id": "admin-uuid",
        }
        assert workflow_kwargs("issue_scope", body, "admin-uuid") == {
            "dry_run": True,
            "limit": 10,
        }


class TestReconcileCounts:
    """The arithmetic half of spec §4's reconciliation. The previous form
    (``created + existing == len(plan["assets"])``) was true by construction —
    these pin a comparison that can actually fail."""

    def test_passes_when_db_agrees_with_the_plan(self):
        assert reconcile_counts(3, 3, 7, 7) is None

    def test_raises_when_asset_count_disagrees(self):
        with pytest.raises(RuntimeError) as e:
            reconcile_counts(3, 2, 7, 7)
        msg = str(e.value)
        assert "assets expected=3 actual=2" in msg
        assert "project_refs expected=7 actual=7" in msg

    def test_raises_when_ref_count_disagrees(self):
        with pytest.raises(RuntimeError) as e:
            reconcile_counts(3, 3, 7, 5)
        assert "project_refs expected=7 actual=5" in str(e.value)


class TestExecutionBlockedInP0:
    async def test_dry_run_false_raises_before_touching_the_database(self):
        """One admin POST with ``dry_run: false`` must not reach ``_apply``,
        which has never run against a database."""
        import app.workflows.backfill_assets_from_project_entities as m

        applied = AsyncMock()
        with (
            patch.object(
                m, "_load_inputs", AsyncMock(return_value=([], [], {}, set()))
            ),
            patch.object(m, "_apply", applied),
            patch(
                "app.services.infra.unified_task_manager.get_task_manager",
                return_value=AsyncMock(),
            ),
        ):
            with pytest.raises(NotImplementedError, match="P3"):
                await inspect.unwrap(m.backfill_assets_from_project_entities)(
                    dry_run=False, run_user_id="admin-uuid"
                )
        applied.assert_not_awaited()

    async def test_dry_run_true_still_returns_the_plan(self):
        import app.workflows.backfill_assets_from_project_entities as m

        with (
            patch.object(
                m,
                "_load_inputs",
                AsyncMock(
                    return_value=([_char(1, P1, "Sang Yao")], [], PROJECT_TEAM, set())
                ),
            ),
            patch(
                "app.services.infra.unified_task_manager.get_task_manager",
                return_value=AsyncMock(),
            ),
        ):
            out = await inspect.unwrap(m.backfill_assets_from_project_entities)(
                dry_run=True, run_user_id="admin-uuid"
            )
        assert out["dry_run"] is True
        assert out["counts"]["assets"] == 1
        assert out["merges"] == []
        assert "applied" not in out
