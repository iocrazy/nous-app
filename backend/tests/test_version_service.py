"""Unit tests for the script version service (Phase B P4).

Two halves, mirroring the module:
  * pure logic — replay determinism, four diff kinds (incl. moved), inverse
    round-trip, empty ledger;
  * orchestration — commit snapshot, diff shape, rollback edge semantics
    (scene added-after-commit not deleted; scene deleted-since not resurrected;
    per-scene partial failure) against in-memory fake repositories.
"""

from __future__ import annotations

from typing import Any, Dict, List

import pytest

from app.services.script.scene_ops import apply_ops
from app.services.script.version_service import (
    VersionService,
    actor_by_element,
    diff_scenes,
    inverse_between,
    last_actor,
    replay_to,
)

pytestmark = pytest.mark.unit


# --------------------------------------------------------------------------- #
# Helpers
# --------------------------------------------------------------------------- #


def _ins(el_id: str, text: str, after_id: str | None = None) -> dict:
    op: Dict[str, Any] = {
        "op": "insert",
        "element_id": el_id,
        "payload": {"type": "action", "text": text},
    }
    if after_id is not None:
        op["after_id"] = after_id
    return op


def _upd(el_id: str, text: str) -> dict:
    return {
        "op": "update",
        "element_id": el_id,
        "payload": {"text": text},
    }


def _ledger(batches: List[List[dict]], actors: List[str] | None = None):
    """Replay ``batches`` forward, returning (ledger_rows, {op_seq: elements}).

    When ``actors`` is supplied (one per batch) each row carries that ``actor``
    so authorship attribution can be exercised; omitted keeps rows actor-free
    (the pre-attribution shape, for the pure replay/diff tests)."""
    rows: List[Dict[str, Any]] = []
    elements: List[dict] = []
    per_seq: Dict[int, List[dict]] = {0: []}
    for seq, ops in enumerate(batches, start=1):
        new, inverse = apply_ops(elements, ops)
        row: Dict[str, Any] = {
            "op_seq": seq,
            "op_json": {"ops": ops, "inverse": inverse},
        }
        if actors is not None:
            row["actor"] = actors[seq - 1]
        rows.append(row)
        elements = new
        per_seq[seq] = new
    return rows, per_seq


# --------------------------------------------------------------------------- #
# replay_to
# --------------------------------------------------------------------------- #


def test_replay_to_reconstructs_each_version():
    rows, per_seq = _ledger(
        [
            [_ins("el_1", "one")],
            [_ins("el_2", "two", after_id="el_1")],
            [_upd("el_1", "one-edited")],
        ]
    )
    assert replay_to(rows, 0) == []
    assert replay_to(rows, 1) == per_seq[1]
    assert replay_to(rows, 2) == per_seq[2]
    assert replay_to(rows, 3) == per_seq[3]


def test_replay_to_is_deterministic():
    rows, per_seq = _ledger([[_ins("el_1", "a")], [_ins("el_2", "b", after_id="el_1")]])
    assert replay_to(rows, 2) == replay_to(rows, 2) == per_seq[2]


def test_replay_to_ignores_row_order():
    rows, per_seq = _ledger([[_ins("el_1", "a")], [_ins("el_2", "b", after_id="el_1")]])
    shuffled = list(reversed(rows))
    assert replay_to(shuffled, 2) == per_seq[2]


def test_replay_empty_ledger():
    assert replay_to([], 5) == []


# --------------------------------------------------------------------------- #
# diff_scenes — four kinds
# --------------------------------------------------------------------------- #


def _el(el_id: str, text: str) -> dict:
    return {"id": el_id, "type": "action", "text": text}


def test_diff_added():
    a = [_el("el_1", "one")]
    b = [_el("el_1", "one"), _el("el_2", "two")]
    diffs = diff_scenes(a, b)
    assert diffs == [
        {"kind": "added", "id": "el_2", "before": None, "after": _el("el_2", "two")}
    ]


def test_diff_removed():
    a = [_el("el_1", "one"), _el("el_2", "two")]
    b = [_el("el_1", "one")]
    diffs = diff_scenes(a, b)
    assert diffs == [
        {"kind": "removed", "id": "el_2", "before": _el("el_2", "two"), "after": None}
    ]


def test_diff_changed():
    a = [_el("el_1", "one")]
    b = [_el("el_1", "one-edited")]
    diffs = diff_scenes(a, b)
    assert len(diffs) == 1
    assert diffs[0]["kind"] == "changed"
    assert diffs[0]["before"] == _el("el_1", "one")
    assert diffs[0]["after"] == _el("el_1", "one-edited")


def test_diff_moved():
    a = [_el("el_1", "one"), _el("el_2", "two")]
    b = [_el("el_2", "two"), _el("el_1", "one")]
    diffs = diff_scenes(a, b)
    # A pure swap of two elements reports exactly one as moved (LCS keeps one
    # in place), and nothing as changed/added/removed.
    assert len(diffs) == 1
    assert diffs[0]["kind"] == "moved"


def test_diff_changed_dominates_moved():
    # el_1 both moved AND content-changed → reported 'changed' (content wins).
    a = [_el("el_1", "one"), _el("el_2", "two")]
    b = [_el("el_2", "two"), _el("el_1", "one-edited")]
    kinds = {d["id"]: d["kind"] for d in diff_scenes(a, b)}
    assert kinds["el_1"] == "changed"


def test_diff_identical_is_empty():
    a = [_el("el_1", "one"), _el("el_2", "two")]
    assert diff_scenes(a, list(a)) == []


# --------------------------------------------------------------------------- #
# actor_by_element / last_actor — authorship attribution
# --------------------------------------------------------------------------- #


def test_actor_by_element_takes_last_toucher_in_range():
    rows, _ = _ledger(
        [
            [_ins("el_1", "one")],
            [_ins("el_2", "two", after_id="el_1")],
            [_upd("el_1", "one-edited")],
        ],
        actors=["alice", "bob", "carol"],
    )
    # Range (0, 3]: el_1 last touched by carol (op 3), el_2 by bob (op 2).
    by_el = actor_by_element(rows, 0, 3)
    assert by_el == {"el_1": "carol", "el_2": "bob"}


def test_actor_by_element_respects_watermark_window():
    rows, _ = _ledger(
        [[_ins("el_1", "one")], [_upd("el_1", "two")], [_upd("el_1", "three")]],
        actors=["alice", "bob", "carol"],
    )
    # Only ops in (1, 3]: el_1's last toucher is carol; op 1 (alice) is excluded.
    assert actor_by_element(rows, 1, 3) == {"el_1": "carol"}


def test_actor_by_element_missing_actor_is_none():
    rows, _ = _ledger([[_ins("el_1", "one")]])  # actor-free rows
    assert actor_by_element(rows, 0, 1) == {"el_1": None}


def test_last_actor_returns_highest_seq_within_watermark():
    rows, _ = _ledger(
        [[_ins("el_1", "a")], [_upd("el_1", "b")]], actors=["alice", "bob"]
    )
    assert last_actor(rows, 2) == "bob"
    assert last_actor(rows, 1) == "alice"
    assert last_actor([], 5) is None


# --------------------------------------------------------------------------- #
# inverse_between — round-trip against apply
# --------------------------------------------------------------------------- #


def test_inverse_between_round_trip():
    rows, per_seq = _ledger(
        [
            [_ins("el_1", "one")],
            [_ins("el_2", "two", after_id="el_1")],
            [_upd("el_1", "one-edited")],
            [_ins("el_3", "three", after_id="el_2")],
        ]
    )
    # Undo (from, to] and confirm we land exactly on the 'from' version.
    for from_seq in range(0, 4):
        for to_seq in range(from_seq, 5):
            to_capped = min(to_seq, 4)
            at_to = replay_to(rows, to_capped)
            inverse = inverse_between(rows, from_seq, to_capped)
            landed, _ = apply_ops(at_to, inverse)
            assert landed == per_seq[from_seq], (from_seq, to_capped)


def test_inverse_between_empty_when_from_ge_to():
    rows, _ = _ledger([[_ins("el_1", "one")], [_ins("el_2", "two", after_id="el_1")]])
    assert inverse_between(rows, 2, 2) == []
    assert inverse_between(rows, 2, 1) == []


# --------------------------------------------------------------------------- #
# Orchestration — fake repositories
# --------------------------------------------------------------------------- #


class _FakeSceneRepo:
    def __init__(self, scenes: List[dict], ops_by_scene: Dict[str, List[dict]]):
        self._scenes = scenes
        self._ops = ops_by_scene
        self.applied: List[dict] = []
        self.fail_scene_ids: set = set()
        # Scenes whose ledger READ itself blows up (e.g. a transient DB error),
        # as opposed to fail_scene_ids which fails the later apply step.
        self.fail_ledger_scene_ids: set = set()

    async def list_by_script(self, script_id: str) -> List[dict]:
        return list(self._scenes)

    async def list_ops_by_scene(self, scene_id: str) -> List[dict]:
        if str(scene_id) in self.fail_ledger_scene_ids:
            raise RuntimeError("ledger read boom")
        return list(self._ops.get(str(scene_id), []))

    async def apply_element_ops(self, scene_id, ops, expected_version, actor):
        if str(scene_id) in self.fail_scene_ids:
            raise RuntimeError("boom")
        self.applied.append(
            {
                "scene_id": str(scene_id),
                "ops": ops,
                "expected_version": expected_version,
                "actor": actor,
            }
        )
        return {"content_version": expected_version + 1, "elements": []}


class _FakeCommitRepo:
    def __init__(self, commit: dict | None = None, usernames: dict | None = None):
        self._commit = commit
        self._usernames = usernames or {}
        self.created: List[dict] = []
        self.resolved_with: List[List[str]] = []

    async def create(self, data: dict) -> dict:
        self.created.append(data)
        return {"id": "5000", **data}

    async def get(self, commit_id: str) -> dict | None:
        return self._commit

    async def resolve_usernames(self, user_ids: List[str]) -> dict:
        self.resolved_with.append(list(user_ids))
        return {u: self._usernames[u] for u in user_ids if u in self._usernames}


@pytest.mark.asyncio
async def test_create_commit_snapshots_watermarks_and_scenes():
    scenes = [
        {
            "id": 111,
            "content_version": 3,
            "sort_order": 1000,
            "heading_int_ext": "INT",
            "location_text": "Kitchen",
        },
        {
            "id": 222,
            "content_version": 0,
            "sort_order": 2000,
            "heading_int_ext": "EXT",
            "location_text": "Street",
        },
    ]
    scene_repo = _FakeSceneRepo(scenes, {})
    commit_repo = _FakeCommitRepo()
    svc = VersionService(scene_repo=scene_repo, commit_repo=commit_repo)

    out = await svc.create_commit("900", "First cut", "user-uuid")

    assert commit_repo.created, "repo.create was not called"
    payload = commit_repo.created[0]
    assert payload["watermarks"] == {"111": 3, "222": 0}
    assert payload["scene_ids"][0]["id"] == "111"
    assert payload["created_by"] == "user-uuid"
    assert payload["message"] == "First cut"
    assert out["id"] == "5000"


@pytest.mark.asyncio
async def test_compute_diff_against_current_reports_element_and_scene_changes():
    rows, _ = _ledger(
        [
            [_ins("el_1", "one")],
            [_upd("el_1", "one-edited")],
        ]
    )
    # Live scenes: scene 111 now at version 2; scene 333 is new (added since A).
    scenes = [
        {"id": 111, "content_version": 2, "sort_order": 1000},
        {"id": 333, "content_version": 0, "sort_order": 3000},
    ]
    scene_repo = _FakeSceneRepo(scenes, {"111": rows})
    svc = VersionService(scene_repo=scene_repo, commit_repo=_FakeCommitRepo())

    commit_a = {
        "watermarks": {"111": 1, "222": 5},  # 222 existed at A, gone now
        "scene_ids": [
            {"id": "111", "sort_order": 1000},
            {"id": "222", "sort_order": 2000},
        ],
    }
    diff = await svc.compute_diff("900", commit_a, None)

    # scene 111 changed el_1 between v1 and v2.
    scene_111 = next(s for s in diff["scenes"] if s["scene_id"] == "111")
    assert scene_111["elements"][0]["kind"] == "changed"
    # 333 added since the commit; 222 removed since the commit.
    assert [s["id"] for s in diff["scenes_added"]] == ["333"]
    assert [s["id"] for s in diff["scenes_removed"]] == ["222"]
    # authors map is present (empty here — the fake ledger carries no actors).
    assert diff["authors"] == {}


@pytest.mark.asyncio
async def test_compute_diff_attributes_actor_and_resolves_authors():
    rows, _ = _ledger(
        [[_ins("el_1", "one")], [_upd("el_1", "one-edited")]],
        actors=["u-alice", "u-bob"],
    )
    scenes = [{"id": 111, "content_version": 2, "sort_order": 1000}]
    scene_repo = _FakeSceneRepo(scenes, {"111": rows})
    commit_repo = _FakeCommitRepo(usernames={"u-bob": "Bob"})
    svc = VersionService(scene_repo=scene_repo, commit_repo=commit_repo)

    commit_a = {"watermarks": {"111": 1}, "scene_ids": [{"id": "111"}]}
    diff = await svc.compute_diff("900", commit_a, None)

    scene_111 = next(s for s in diff["scenes"] if s["scene_id"] == "111")
    change = scene_111["elements"][0]
    # el_1 was last touched by u-bob (op 2) between watermark 1 and 2.
    assert change["actor"] == "u-bob"
    assert scene_111["author"] == "u-bob"
    # The batched resolve turned the uuid into a display name.
    assert diff["authors"] == {"u-bob": "Bob"}


@pytest.mark.asyncio
async def test_rollback_edge_semantics_and_partial_failure():
    rows_a, _ = _ledger(
        [
            [_ins("el_1", "one")],
            [_upd("el_1", "one-edited")],
            [_upd("el_1", "one-edited-again")],
        ]
    )
    rows_b, _ = _ledger([[_ins("el_9", "nine")], [_upd("el_9", "nine-edited")]])
    scenes = [
        {"id": 111, "content_version": 3, "sort_order": 1000},  # roll back 3→1
        {"id": 222, "content_version": 2, "sort_order": 2000},  # will fail
        {"id": 444, "content_version": 0, "sort_order": 4000},  # added after commit
    ]
    scene_repo = _FakeSceneRepo(scenes, {"111": rows_a, "222": rows_b})
    scene_repo.fail_scene_ids = {"222"}
    commit = {
        "watermarks": {"111": 1, "222": 1, "333": 4},
        "scene_ids": [],
    }
    svc = VersionService(scene_repo=scene_repo, commit_repo=_FakeCommitRepo(commit))

    out = await svc.rollback_to("900", "5000", "actor-uuid")

    by_scene = {r["scene_id"]: r for r in out["results"]}
    assert by_scene["111"]["status"] == "rolled_back"
    assert by_scene["222"]["status"] == "failed"
    assert out["partial_failure"] is True
    # 444 exists now but not at commit → reported, not deleted.
    assert out["not_deleted"] == ["444"]
    # 333 existed at commit but gone now → reported, not resurrected.
    assert out["not_resurrected"] == ["333"]
    # The rolled-back scene applied its inverse under optimistic concurrency at
    # the CURRENT version.
    applied_111 = next(a for a in scene_repo.applied if a["scene_id"] == "111")
    assert applied_111["expected_version"] == 3
    assert applied_111["actor"] == "actor-uuid"


@pytest.mark.asyncio
async def test_rollback_ledger_read_failure_is_per_scene_not_fatal():
    """I-1 regression: a ledger read (list_ops_by_scene) that raises for one
    scene must NOT abort the whole rollback — it should land as that scene's
    'failed' result, same as an apply_element_ops failure, while scenes
    processed before it (sorted by scene_id) keep their committed rollback."""
    rows_a, _ = _ledger(
        [
            [_ins("el_1", "one")],
            [_upd("el_1", "one-edited")],
            [_upd("el_1", "one-edited-again")],
        ]
    )
    scenes = [
        {"id": 111, "content_version": 3, "sort_order": 1000},  # rolls back fine
        {"id": 222, "content_version": 2, "sort_order": 2000},  # ledger read raises
    ]
    scene_repo = _FakeSceneRepo(scenes, {"111": rows_a})
    scene_repo.fail_ledger_scene_ids = {"222"}
    commit = {"watermarks": {"111": 1, "222": 1}, "scene_ids": []}
    svc = VersionService(scene_repo=scene_repo, commit_repo=_FakeCommitRepo(commit))

    out = await svc.rollback_to("900", "5000", "actor-uuid")

    by_scene = {r["scene_id"]: r for r in out["results"]}
    # 111 (processed first, scene_ids sorted) rolled back and stayed committed
    # even though 222 (processed after) blew up reading its ledger.
    assert by_scene["111"]["status"] == "rolled_back"
    assert by_scene["222"]["status"] == "failed"
    assert by_scene["222"]["error_code"] == "error"
    assert out["partial_failure"] is True
    applied_ids = {a["scene_id"] for a in scene_repo.applied}
    assert applied_ids == {"111"}


@pytest.mark.asyncio
async def test_rollback_missing_commit_returns_none():
    svc = VersionService(
        scene_repo=_FakeSceneRepo([], {}), commit_repo=_FakeCommitRepo(None)
    )
    assert await svc.rollback_to("900", "nope", "actor") is None


@pytest.mark.asyncio
async def test_rollback_unchanged_scene_is_noop():
    scenes = [{"id": 111, "content_version": 2, "sort_order": 1000}]
    scene_repo = _FakeSceneRepo(scenes, {"111": []})
    commit = {"watermarks": {"111": 2}, "scene_ids": []}
    svc = VersionService(scene_repo=scene_repo, commit_repo=_FakeCommitRepo(commit))

    out = await svc.rollback_to("900", "5000", "actor")

    assert out["results"] == [{"scene_id": "111", "status": "unchanged"}]
    assert scene_repo.applied == []
