"""Episode-scoped workflow nodes — data layer (mig 402, B1).

Covers the three schema additions landed by B1, per the plan's checklist
(``docs/superpowers/plans/2026-08-04-episode-workflow-and-agent-layer.md``
section B1) and the P0 audit
(``docs/superpowers/plans/2026-08-04-p0-cursor-audit.md`` §4):

  1. ``project_stage_nodes.episode_id`` — nullable FK; ``_node_row`` must
     surface it (stringified when present, None when not) the same way it
     already does for every other id column.
  2. ``surface`` on both ``workflow_template_nodes`` and
     ``project_stage_nodes`` — a NULLABLE column with NO server_default,
     unlike ``events``/``completion_policy``/``form_schema`` — so ``_node_row``
     must pass a None value through untouched, never coerced via an
     ``(x or {})``/``(x or "default")`` idiom (that idiom is correct for
     ``events`` precisely because ``events`` is NOT NULL; it would be a bug
     here). ``surface`` must also be UNREACHABLE through ``update_node`` and
     ``NodePatch`` — frozen at instantiation, same as
     completion_policy/events/form_schema.
  3. The migration file itself: idempotent (every ADD COLUMN guarded, every
     CHECK constraint follows the unconditional drop+recreate idiom already
     used by migration 398), and defines the columns/constraints/index this
     module's other tests assume exist.

``instantiate_from_template``'s FakeSession-backed copy-freeze coverage for
``surface`` (including the NULL-survives-verbatim case) lives in
``test_workflow_instantiation.py`` alongside the equivalent ``form_schema``
test, not duplicated here. ``EpisodeRepository.set_current_node_id`` (the
per-episode cursor accessor) is covered in ``test_episode_repository.py``
alongside its sibling ``create``/``update`` tests, same reason.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from app.models import ProjectStageNodes
from app.repositories.project_stage_nodes_repository import (
    ProjectStageNodesRepository,
    _node_row,
)
from app.schemas.workflow import NodeOut, NodePatch, TemplateNodeIn

MIG = (
    Path(__file__).resolve().parents[2]
    / "supabase/migrations/402_episode_scoped_workflow_nodes.sql"
)


def _node(**overrides):
    base = dict(
        id=1001,
        project_id=50,
        source_template_node_id=None,
        legacy_stage_id=None,
        name="Script",
        sort_order=1,
        parallel_group=None,
        episode_id=None,
        status="pending",
        owner_user_id=None,
        owner_agent_id=None,
        planned_start=None,
        planned_due=None,
        review_required=False,
        deliverable_required=False,
        deliverable_label=None,
        skipped=False,
        folder_id=None,
        completion_policy="owner",
        events={},
        metadata_={},
        form_schema=[],
        form_data={},
        brief="",
        surface=None,
    )
    base.update(overrides)
    return ProjectStageNodes(**base)


# ── _node_row: episode_id / surface serialization ───────────────────────────


def test_node_row_episode_id_none_stays_none():
    row = _node_row(_node(episode_id=None), [], [])
    assert row["episode_id"] is None


def test_node_row_episode_id_stringified_when_present():
    # Snowflake ids ride as strings at the API boundary (bigIntSafeFetch),
    # same treatment as every other id/FK in this row (project_id,
    # folder_id, source_template_node_id, ...).
    row = _node_row(_node(episode_id=777), [], [])
    assert row["episode_id"] == "777"
    assert isinstance(row["episode_id"], str)


def test_node_row_surface_none_is_not_coerced():
    # The P0-flagged asymmetry: events is NOT NULL so `(obj.events or {})`
    # is safe; surface has no such fallback and must read back exactly None
    # for a deliverable-type node, not "" / {} / some default surface.
    row = _node_row(_node(surface=None), [], [])
    assert row["surface"] is None


def test_node_row_surface_passes_through_known_values():
    for value in ("script", "storyboard", "renders"):
        row = _node_row(_node(surface=value), [], [])
        assert row["surface"] == value


# ── surface / episode_id are frozen: unreachable through update_node ────────


def test_update_node_signature_has_no_surface_or_episode_id_kwarg():
    """surface and episode_id (mig 402, B1) are instantiate-then-freeze
    columns — same discipline as completion_policy/events/form_schema.
    ``update_node`` has no ``**kwargs`` catch-all, so passing either as a
    keyword fails FAST with TypeError (raised at the call itself, before any
    coroutine runs) rather than being silently accepted and either ignored
    or — worse — actually written."""
    repo = ProjectStageNodesRepository()
    with pytest.raises(TypeError):
        repo.update_node("1001", "50", surface="renders")
    with pytest.raises(TypeError):
        repo.update_node("1001", "50", episode_id="777")


# ── NodePatch has no surface field (regression pin, same idiom as the ──────
# ── existing form_schema pin in test_workflow_form_schema.py) ───────────────


def test_node_patch_has_no_surface_field_declared():
    assert "surface" not in NodePatch.model_fields


def test_node_patch_ignores_surface_kwarg_silently():
    # NodePatch has no explicit model_config, so it inherits pydantic v2's
    # default extra="ignore" — passing surface doesn't raise, but it isn't
    # stored either. Pinning today's actual behavior, same as the analogous
    # form_schema pin.
    patch = NodePatch(skipped=True, surface="script")
    assert not hasattr(patch, "surface")
    dumped = patch.model_dump(exclude_unset=True)
    assert "surface" not in dumped
    assert dumped["skipped"] is True


# ── NodeOut / TemplateNodeIn declare the new fields (else pydantic drops ───
# ── the repo dict's keys silently converting it to a response model) ───────


def test_node_out_declares_surface_and_episode_id_with_none_defaults():
    assert "surface" in NodeOut.model_fields
    assert "episode_id" in NodeOut.model_fields
    out = NodeOut(
        id="1",
        project_id="50",
        name="Script",
        sort_order=1,
        status="pending",
        review_required=False,
        deliverable_required=False,
        skipped=False,
    )
    assert out.surface is None
    assert out.episode_id is None


def test_template_node_in_declares_surface_with_none_default():
    assert "surface" in TemplateNodeIn.model_fields
    node = TemplateNodeIn(name="Script", sort_order=1)
    assert node.surface is None


def test_template_node_in_rejects_unknown_surface_value():
    with pytest.raises(ValueError):
        TemplateNodeIn(name="Script", sort_order=1, surface="bogus")


# ── migration file: idempotent, defines the expected shape ──────────────────


def test_migration_defines_expected_columns_and_constraints():
    sql = MIG.read_text()
    assert "ADD COLUMN IF NOT EXISTS episode_id" in sql
    assert "ADD COLUMN IF NOT EXISTS surface" in sql
    assert "ADD COLUMN IF NOT EXISTS current_node_id" in sql
    assert "REFERENCES public.episodes(id) ON DELETE SET NULL" in sql
    assert "REFERENCES public.project_stage_nodes(id) ON DELETE SET NULL" in sql
    assert "workflow_template_nodes_surface_check" in sql
    assert "project_stage_nodes_surface_check" in sql
    assert "idx_project_stage_nodes_episode" in sql
    assert "NOTIFY pgrst" in sql
    # CHECK allows NULL explicitly (the P0-flagged asymmetry) rather than
    # leaning on three-valued-logic silently letting NULL pass. The
    # expression wraps across lines in the file, hence the whitespace regex.
    assert len(re.findall(r"surface IS NULL\s+OR surface", sql)) == 2


def test_migration_every_add_column_is_guarded():
    sql = MIG.read_text()
    add_column_lines = [
        line for line in sql.splitlines() if "ADD COLUMN" in line and "--" not in line
    ]
    assert len(add_column_lines) == 4  # episode_id, surface x2, current_node_id
    for line in add_column_lines:
        assert "IF NOT EXISTS" in line, f"unguarded ADD COLUMN: {line!r}"


def test_migration_check_constraints_use_unconditional_drop_then_add():
    # Same idempotent idiom as migration 398: DROP CONSTRAINT IF EXISTS is
    # safe to rerun; a bare ADD CONSTRAINT (or ADD CONSTRAINT IF NOT EXISTS,
    # which Postgres doesn't support for CHECK) would not be. Only count
    # actual DDL lines — the header comment also mentions the idiom by name.
    sql = MIG.read_text()
    ddl_drop_lines = [
        line
        for line in sql.splitlines()
        if "DROP CONSTRAINT IF EXISTS" in line and not line.strip().startswith("--")
    ]
    assert len(ddl_drop_lines) == 2
    assert re.search(r"CREATE INDEX IF NOT EXISTS idx_project_stage_nodes_episode", sql)
