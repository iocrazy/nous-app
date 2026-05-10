"""Root-shared abort registry — when root run cancels, fan out to subagents.

Wave I (I1). delegate_tool.py already has:
  - self-delegate hard reject
  - MAX_DELEGATION_DEPTH cap (3)
  - parent_run_id chain walk for A→B→A cycle detection
  - per-caller rate limit

What it doesn't have: when the user cancels the ROOT run (frontend
cancel button), the cancellation never propagates to the running
SUBAGENT. The root run's AbortController fires, but each subagent
spawned via Delegate has its OWN AbortController + run_id; they keep
burning compute.

This registry is the bridge:
  - At root run start: register root_run_id → AbortController
  - When subagent dispatched: register child run_id → SAME controller
  - Cancel root → all registered child runs see is_aborted()=True
  - Subagent runs poll its abort just like root does

Per-process; for multi-process worker fleet (future) we'd back this
with Redis pub/sub. For NAS single-process, in-memory is enough.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

from app.agent_framework.abort_controller import AbortController


@dataclass
class _Entry:
    abort: AbortController
    children: set[str]  # run_ids of subagents subscribed to same controller


class RootAbortRegistry:
    """Per-process map: run_id → root AbortController.

    A "root" run is one not delegated FROM another run. When delegate_tool
    spawns a child run, it registers the child against the parent's
    SAME controller — so any cancel on the root fans out automatically.
    """

    def __init__(self) -> None:
        # run_id (str) → root AbortController
        self._by_run_id: dict[str, AbortController] = {}
        # root_run_id → set of child run_ids (for visibility / cleanup)
        self._tree: dict[str, _Entry] = {}

    def register_root(self, run_id: str, abort: AbortController) -> None:
        """Register a fresh root run + its abort controller."""
        if not run_id:
            raise ValueError("run_id must be non-empty")
        self._by_run_id[run_id] = abort
        self._tree[run_id] = _Entry(abort=abort, children=set())

    def register_child(self, *, parent_run_id: str, child_run_id: str) -> bool:
        """Register a child run as inheriting parent's abort controller.
        Returns True if successfully attached, False if parent unknown."""
        if not child_run_id:
            return False
        # Walk to root — parent might itself be a child
        root_id = self._find_root(parent_run_id)
        if root_id is None:
            return False
        root_entry = self._tree[root_id]
        root_entry.children.add(child_run_id)
        # Child shares root's abort
        self._by_run_id[child_run_id] = root_entry.abort
        return True

    def _find_root(self, run_id: str) -> Optional[str]:
        """Walk up via membership: a run is a root iff it IS in self._tree
        as a key. Children are in self._by_run_id but not in self._tree."""
        if run_id in self._tree:
            return run_id
        # Child — figure out which root it belongs to
        for root_id, entry in self._tree.items():
            if run_id in entry.children:
                return root_id
        return None

    def get_abort(self, run_id: str) -> Optional[AbortController]:
        """Look up the abort controller for any run id (root or child).
        Returns None if not registered (caller should construct a fresh
        controller — back-compat for runs that haven't migrated)."""
        return self._by_run_id.get(run_id)

    def cancel_root(self, root_run_id: str, *, reason: Optional[str] = None) -> int:
        """Fire the abort for a root + all its children. Returns count
        of run_ids affected (root + children, deduped)."""
        entry = self._tree.get(root_run_id)
        if entry is None:
            return 0
        entry.abort.fire(reason=reason or "root cancelled")
        # All children share this abort, so they're already cancelled
        # by reference. Just return the count for telemetry.
        return 1 + len(entry.children)

    def unregister_run(self, run_id: str) -> None:
        """Clean up after a run finishes (success / fail / cancel).
        Removes from by_run_id; removes from tree if root.
        Idempotent."""
        self._by_run_id.pop(run_id, None)
        if run_id in self._tree:
            entry = self._tree.pop(run_id)
            for child_id in entry.children:
                self._by_run_id.pop(child_id, None)
        else:
            # Child — remove from parent's children set
            for entry in self._tree.values():
                entry.children.discard(run_id)

    def known_runs(self) -> list[str]:
        """All currently-tracked run ids (roots + children). Sorted."""
        return sorted(self._by_run_id.keys())

    def snapshot(self) -> dict[str, dict]:
        """Admin/debug: roots → list of children + abort state."""
        out: dict[str, dict] = {}
        for root_id, entry in self._tree.items():
            out[root_id] = {
                "is_aborted": entry.abort.is_aborted(),
                "children": sorted(entry.children),
                "child_count": len(entry.children),
            }
        return out

    def __len__(self) -> int:
        return len(self._tree)


__all__ = ["RootAbortRegistry"]
