"""Script version management (spec v3 §5-1).

Version control is built entirely on the append-only ``script_ops`` ledger that
P1 has recorded since the editor's first write:

  * a **commit** is a manual tag — a per-scene ``op_seq`` watermark map plus an
    ordered snapshot of the script's scene set at that moment (no content is
    copied; the ledger already holds it);
  * a **diff** between two commits replays each scene's ledger up to each
    commit's watermark and compares the two element arrays element-by-element;
  * a **rollback** to a commit replays the *inverse* ops between the current
    watermark and the commit's watermark, then applies that inverse batch
    through the SAME optimistic ``apply_element_ops`` protocol — so a rollback
    is itself a new forward op (history stays append-only and re-rollbackable).

This module splits into two halves:

  * **pure logic** (``replay_to`` / ``diff_scenes`` / ``inverse_between``): no
    IO, exhaustively unit-tested, reusing ``scene_ops.apply_ops``;
  * **orchestration** (``VersionService``): reads the ledger + commit rows via
    the repositories and stitches the pure pieces together.

Watermark = a scene's ``content_version`` (== ``MAX(op_seq)`` by construction:
``apply_element_ops`` stamps ``op_seq = new content_version``, and a genesis
scene lands version 1 / op_seq 1). A scene with no ops has watermark 0.
"""

from __future__ import annotations

import difflib
from typing import Any, Dict, List, Optional, Tuple

from app.repositories.script_commit_repository import get_script_commit_repository
from app.repositories.script_scene_repository import get_script_scene_repository
from app.services.script.scene_ops import apply_ops

# Keys that describe an element's *content* (not its identity). Two elements
# with the same id differ in "content" when any of these differ; position is
# handled separately (moved).
_ELEMENT_ID_KEY = "id"


# --------------------------------------------------------------------------- #
# Pure logic — no IO. Exhaustively unit-tested.
# --------------------------------------------------------------------------- #


def _ops_of(row: Dict[str, Any]) -> List[dict]:
    """The forward ops of a ledger row (``op_json.ops``), or []."""
    return (row.get("op_json") or {}).get("ops") or []


def _inverse_of(row: Dict[str, Any]) -> List[dict]:
    """The inverse ops of a ledger row (``op_json.inverse``), or []."""
    return (row.get("op_json") or {}).get("inverse") or []


def replay_to(ops_rows: List[Dict[str, Any]], target_seq: int) -> List[dict]:
    """Replay a scene's ledger from empty up to (and including) ``target_seq``.

    ``ops_rows`` are ledger rows shaped ``{"op_seq": int, "op_json": {...}}`` in
    any order. Rows with ``op_seq <= target_seq`` are applied in ``op_seq`` order
    via the pure ``apply_ops``; the result is the exact element array the scene
    held at that version. ``target_seq`` 0 (or an empty ledger) yields ``[]``."""
    elements: List[dict] = []
    for row in sorted(ops_rows, key=lambda r: r["op_seq"]):
        if row["op_seq"] > target_seq:
            break
        elements, _ = apply_ops(elements, _ops_of(row))
    return elements


def inverse_between(
    ops_rows: List[Dict[str, Any]], from_seq: int, to_seq: int
) -> List[dict]:
    """Build the inverse batch that undoes ops in the ``(from_seq, to_seq]`` range.

    Concatenates each row's precomputed ``op_json.inverse`` in DESCENDING
    ``op_seq`` order — the latest-applied op is undone first, and within a row
    the inverse is already ordered last-applied-first. Applying the returned
    batch to ``replay_to(rows, to_seq)`` reproduces ``replay_to(rows, from_seq)``.
    ``from_seq >= to_seq`` yields ``[]`` (nothing to undo)."""
    region = sorted(
        (r for r in ops_rows if from_seq < r["op_seq"] <= to_seq),
        key=lambda r: r["op_seq"],
        reverse=True,
    )
    inverse: List[dict] = []
    for row in region:
        inverse.extend(_inverse_of(row))
    return inverse


def _element_content(element: dict) -> dict:
    """The element minus its id — the part a 'changed' diff compares."""
    return {k: v for k, v in element.items() if k != _ELEMENT_ID_KEY}


def _moved_ids(a_order: List[str], b_order: List[str]) -> set:
    """Ids common to both whose relative order changed (a genuine reorder).

    Runs an LCS (``difflib``) over the two common-id sequences: ids inside an
    equal block are 'in place'; the remainder moved. Deterministic and minimal
    (a single displaced element is reported, not the whole tail it shifted)."""
    matcher = difflib.SequenceMatcher(a=a_order, b=b_order, autojunk=False)
    in_place: set = set()
    for block in matcher.get_matching_blocks():
        for k in range(block.size):
            in_place.add(b_order[block.b + k])
    return set(b_order) - in_place


def diff_scenes(elements_a: List[dict], elements_b: List[dict]) -> List[Dict[str, Any]]:
    """Element-level diff of two element arrays, aligned by id.

    Emits one entry per changed element in reading order of ``elements_b``
    (added / changed / moved), then removed elements in ``elements_a`` order:

      * ``added``   — id in B only        (before=None, after=B element)
      * ``removed`` — id in A only        (before=A element, after=None)
      * ``changed`` — id in both, content differs   (before=A, after=B)
      * ``moved``   — id in both, content same but position changed

    An element that both changed content and moved is reported ``changed``
    (content dominates). Unchanged, same-position elements are omitted."""
    a_by_id = {e[_ELEMENT_ID_KEY]: e for e in elements_a}
    b_by_id = {e[_ELEMENT_ID_KEY]: e for e in elements_b}
    a_ids = [e[_ELEMENT_ID_KEY] for e in elements_a]
    b_ids = [e[_ELEMENT_ID_KEY] for e in elements_b]
    a_set, b_set = set(a_ids), set(b_ids)
    common = a_set & b_set

    a_common_order = [i for i in a_ids if i in common]
    b_common_order = [i for i in b_ids if i in common]
    moved = _moved_ids(a_common_order, b_common_order)

    diffs: List[Dict[str, Any]] = []
    for eid in b_ids:
        if eid not in a_set:
            diffs.append(
                {"kind": "added", "id": eid, "before": None, "after": b_by_id[eid]}
            )
            continue
        before, after = a_by_id[eid], b_by_id[eid]
        if _element_content(before) != _element_content(after):
            diffs.append(
                {"kind": "changed", "id": eid, "before": before, "after": after}
            )
        elif eid in moved:
            diffs.append({"kind": "moved", "id": eid, "before": before, "after": after})
    for eid in a_ids:
        if eid not in b_set:
            diffs.append(
                {"kind": "removed", "id": eid, "before": a_by_id[eid], "after": None}
            )
    return diffs


# --------------------------------------------------------------------------- #
# Orchestration — reads the ledger + commit rows, stitches the pure pieces.
# --------------------------------------------------------------------------- #


def _scene_snapshot(scene: Dict[str, Any]) -> Dict[str, Any]:
    """The per-scene snapshot stored in a commit's ``scene_ids`` array — enough
    to render scene add/remove/reorder in a diff without a live scene fetch."""
    return {
        "id": str(scene["id"]),
        "sort_order": scene.get("sort_order"),
        "heading_int_ext": scene.get("heading_int_ext"),
        "location_text": scene.get("location_text"),
    }


class VersionService:
    """Commit / diff / rollback orchestration over the op ledger.

    Repositories are injectable so the orchestration can be unit-tested against
    in-memory fakes (the pure logic above needs no repos at all)."""

    def __init__(self, scene_repo: Any = None, commit_repo: Any = None):
        self.scenes = scene_repo or get_script_scene_repository()
        self.commits = commit_repo or get_script_commit_repository()

    async def snapshot_watermarks(
        self, script_id: str
    ) -> Tuple[Dict[str, int], List[Dict[str, Any]]]:
        """Current ``({scene_id: op_seq}, ordered scene snapshots)`` for a script.

        Watermark per scene = its ``content_version`` (== MAX(op_seq)). Scene ids
        are stringified (JSONB object keys are strings; a bigint key would not
        round-trip)."""
        scenes = await self.scenes.list_by_script(script_id)
        watermarks = {str(s["id"]): int(s.get("content_version") or 0) for s in scenes}
        scene_ids = [_scene_snapshot(s) for s in scenes]
        return watermarks, scene_ids

    async def create_commit(
        self, script_id: str, message: str, actor: str
    ) -> Dict[str, Any]:
        """Tag the script's current state: snapshot watermarks + scene set, then
        persist one ``script_commits`` row."""
        watermarks, scene_ids = await self.snapshot_watermarks(script_id)
        return await self.commits.create(
            {
                "script_id": script_id,
                "message": message,
                "watermarks": watermarks,
                "scene_ids": scene_ids,
                "created_by": actor,
            }
        )

    async def _resolve_side(
        self, script_id: str, commit: Optional[Dict[str, Any]]
    ) -> Tuple[Dict[str, int], Dict[str, Dict[str, Any]]]:
        """Resolve one diff side to ``(watermarks, {scene_id: snapshot})``.

        ``commit=None`` means the live current state (diff against 'current')."""
        if commit is None:
            watermarks, scene_ids = await self.snapshot_watermarks(script_id)
        else:
            watermarks = {str(k): int(v) for k, v in (commit["watermarks"]).items()}
            scene_ids = commit["scene_ids"] or []
        snap_by_id = {str(s["id"]): s for s in scene_ids}
        return watermarks, snap_by_id

    async def compute_diff(
        self,
        script_id: str,
        commit_a: Dict[str, Any],
        commit_b: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        """Diff ``commit_a`` → ``commit_b`` (``commit_b=None`` = current state).

        Returns scene-set adds/removes plus, for each scene present on both
        sides, an element-level diff (only scenes with changes are listed)."""
        wm_a, snap_a = await self._resolve_side(script_id, commit_a)
        wm_b, snap_b = await self._resolve_side(script_id, commit_b)

        ids_a, ids_b = set(wm_a), set(wm_b)
        scenes_added = [snap_b[sid] for sid in snap_b if sid not in ids_a]
        scenes_removed = [snap_a[sid] for sid in snap_a if sid not in ids_b]

        scene_diffs: List[Dict[str, Any]] = []
        for sid in ids_a & ids_b:
            rows = await self.scenes.list_ops_by_scene(sid)
            elements_a = replay_to(rows, wm_a[sid])
            elements_b = replay_to(rows, wm_b[sid])
            elements = diff_scenes(elements_a, elements_b)
            if elements:
                scene_diffs.append({"scene_id": sid, "elements": elements})

        return {
            "scenes": scene_diffs,
            "scenes_added": scenes_added,
            "scenes_removed": scenes_removed,
        }

    async def rollback_to(
        self, script_id: str, commit_id: str, actor: str
    ) -> Optional[Dict[str, Any]]:
        """Roll every scene back to its watermark in ``commit_id``.

        Per-scene, replays the inverse ops between the current watermark and the
        commit watermark and applies them through ``apply_element_ops`` (so the
        rollback is a new forward op — history stays append-only). Edge semantics
        (spec §5-1): a scene created AFTER the commit is reported but NOT deleted;
        a scene that existed at commit time but is gone now is reported but NOT
        resurrected. Failures are per-scene and non-fatal — the response lists
        each scene's outcome so the caller can surface a partial rollback."""
        commit = await self.commits.get(commit_id)
        if not commit:
            return None
        target_wm = {str(k): int(v) for k, v in (commit["watermarks"]).items()}
        current_wm, _ = await self.snapshot_watermarks(script_id)

        target_ids, current_ids = set(target_wm), set(current_wm)
        not_deleted = sorted(current_ids - target_ids)  # created after the commit
        not_resurrected = sorted(target_ids - current_ids)  # deleted since the commit

        results: List[Dict[str, Any]] = []
        for sid in sorted(current_ids & target_ids):
            cur_seq, tgt_seq = current_wm[sid], target_wm[sid]
            if cur_seq <= tgt_seq:
                results.append({"scene_id": sid, "status": "unchanged"})
                continue
            rows = await self.scenes.list_ops_by_scene(sid)
            inverse = inverse_between(rows, tgt_seq, cur_seq)
            if not inverse:
                results.append({"scene_id": sid, "status": "unchanged"})
                continue
            try:
                await self.scenes.apply_element_ops(
                    sid, inverse, expected_version=cur_seq, actor=actor
                )
                results.append({"scene_id": sid, "status": "rolled_back"})
            except Exception as exc:  # noqa: BLE001 — per-scene partial failure
                results.append({"scene_id": sid, "status": "failed", "error": str(exc)})

        failed = [r for r in results if r["status"] == "failed"]
        return {
            "commit_id": str(commit_id),
            "results": results,
            "not_deleted": not_deleted,
            "not_resurrected": not_resurrected,
            "partial_failure": bool(failed),
        }


def get_version_service() -> "VersionService":
    """Return the VersionService (repositories resolved lazily)."""
    return VersionService()
