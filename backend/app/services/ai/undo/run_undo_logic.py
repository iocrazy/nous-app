"""Run 撤销纯逻辑：shot 账本归并 + scene 选择性 inverse（spec §4.2/§4.3）。

纯逻辑无 IO，IO 在 run_undo_service（Task 4）。
"""

from __future__ import annotations

from collections import OrderedDict
from dataclasses import dataclass
from typing import Any

from app.services.script.version_service import _inverse_of, _ops_of

# The 6 writable shot fields a `create` row's before/after snapshot carries.
_SHOT_FIELDS = (
    "shot_type",
    "camera_angle",
    "camera_movement",
    "focal_length",
    "lighting",
    "description",
)


@dataclass(frozen=True)
class ShotUndoPlan:
    shot_id: int
    scene_id: int
    kind: str  # 'delete' | 'revert'
    expected: dict[str, Any]
    restore: dict[str, Any]


def merge_shot_ops(rows: list[dict]) -> list[ShotUndoPlan]:
    """Merge a run's `script_shot_ops` rows into one undo plan per shot.

    Rows are sorted `(created_at, id)` ascending, then grouped by `shot_id`
    (grouping is by key, not by contiguity — a shot's rows need not be
    adjacent if another shot's rows interleave in time). A group containing
    a `create` row folds to a single `delete` plan whose `expected` is the
    full 6-field snapshot with every subsequent update's `after_json`
    folded on top. A group of only `update` rows yields a `revert` plan:
    `expected` takes each touched field's last `after_json` value,
    `restore` takes each touched field's first `before_json` value.
    """
    ordered = sorted(rows, key=lambda r: (r["created_at"], r["id"]))
    grouped: "OrderedDict[int, list[dict]]" = OrderedDict()
    for row in ordered:
        grouped.setdefault(row["shot_id"], []).append(row)

    plans: list[ShotUndoPlan] = []
    for shot_id, group in grouped.items():
        scene_id = group[0]["scene_id"]
        create_row = next((r for r in group if r["action"] == "create"), None)
        updates = [r for r in group if r["action"] == "update"]

        if create_row is not None:
            expected = {
                field: create_row["after_json"].get(field) for field in _SHOT_FIELDS
            }
            for row in updates:
                expected.update(row["after_json"])
            plans.append(
                ShotUndoPlan(
                    shot_id=shot_id,
                    scene_id=scene_id,
                    kind="delete",
                    expected=expected,
                    restore={},
                )
            )
            continue

        expected = {}
        restore: dict[str, Any] = OrderedDict()
        for row in updates:
            expected.update(row["after_json"])
            for field, value in row["before_json"].items():
                if field not in restore:
                    restore[field] = value
        plans.append(
            ShotUndoPlan(
                shot_id=shot_id,
                scene_id=scene_id,
                kind="revert",
                expected=expected,
                restore=dict(restore),
            )
        )

    plans.sort(key=lambda p: p.shot_id)
    return plans


@dataclass(frozen=True)
class SceneUndoPlan:
    scene_id: int
    inverse_ops: list[dict]
    skipped_element_ids: tuple[str, ...]
    expected_version: int
    undone_element_ids: tuple[str, ...]


def scene_undo_plan(
    scene_id: int, ops_rows: list[dict], run_actor: str
) -> SceneUndoPlan:
    """Build a scene's undo batch, skipping elements a foreign actor touched
    after the run last touched them.

    For each element the run touched, find the run's last `op_seq` on it
    (`s_e`). If any row with a different actor and `op_seq > s_e` also
    touched that element, it is skipped. The remaining run rows are walked
    in descending `op_seq` order, concatenating each row's `_inverse_of`
    ops for elements that were not skipped. `expected_version` is the max
    `op_seq` across the entire ledger (0 for an empty ledger) — the
    invariant that must match `content_version` before applying.
    """
    run_rows = [r for r in ops_rows if r["actor"] == run_actor]

    last_run_seq: dict[str, int] = {}
    for row in run_rows:
        for op in _ops_of(row):
            eid = op.get("element_id")
            if eid:
                seq = row["op_seq"]
                if seq > last_run_seq.get(eid, -1):
                    last_run_seq[eid] = seq

    skipped: set[str] = set()
    for row in ops_rows:
        if row["actor"] == run_actor:
            continue
        for op in _ops_of(row):
            eid = op.get("element_id")
            if eid in last_run_seq and row["op_seq"] > last_run_seq[eid]:
                skipped.add(eid)

    inverse_ops: list[dict] = []
    undone: "OrderedDict[str, None]" = OrderedDict()
    for row in sorted(run_rows, key=lambda r: r["op_seq"], reverse=True):
        for op in _inverse_of(row):
            eid = op.get("element_id")
            if eid in skipped:
                continue
            inverse_ops.append(op)
            if eid:
                undone[eid] = None

    expected_version = max((r["op_seq"] for r in ops_rows), default=0)

    return SceneUndoPlan(
        scene_id=scene_id,
        inverse_ops=inverse_ops,
        skipped_element_ids=tuple(sorted(skipped)),
        expected_version=expected_version,
        undone_element_ids=tuple(undone.keys()),
    )
