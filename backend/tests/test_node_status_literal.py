"""``NodeOut.status`` is a Literal, and it must list exactly the statuses the
repository accepts and the DB allows (mig 380's CHECK). A value the Literal
lacks would turn a valid row into a 500 on GET /projects/{id}/workflow."""

from __future__ import annotations

import re
from pathlib import Path
from typing import get_args

from app.repositories.project_stage_nodes_repository import _VALID_STATUSES
from app.schemas.workflow import NodeOut, NodeStatus

MIGRATION = (
    Path(__file__).resolve().parents[2]
    / "supabase"
    / "migrations"
    / "380_workflow_nodes_m1.sql"
)


def test_literal_matches_repository_statuses() -> None:
    assert set(get_args(NodeStatus)) == set(_VALID_STATUSES)


def test_literal_matches_db_check() -> None:
    match = re.search(r"CHECK \(status IN \(([^)]*)\)\)", MIGRATION.read_text())
    assert match, "status CHECK not found in mig 380"
    values = {v.strip().strip("'") for v in match.group(1).split(",")}
    assert set(get_args(NodeStatus)) == values


def test_node_out_uses_the_literal() -> None:
    assert NodeOut.model_fields["status"].annotation == NodeStatus
