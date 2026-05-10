"""I1 — RootAbortRegistry: shared abort across delegation tree."""

from __future__ import annotations

import pytest

from app.agent_framework.abort_controller import AbortController
from app.agent_framework.root_abort_registry import RootAbortRegistry


@pytest.mark.unit
def test_register_root_then_lookup():
    reg = RootAbortRegistry()
    abort = AbortController()
    reg.register_root("root-1", abort)
    assert reg.get_abort("root-1") is abort
    assert len(reg) == 1


@pytest.mark.unit
def test_register_root_rejects_empty_id():
    with pytest.raises(ValueError):
        RootAbortRegistry().register_root("", AbortController())


@pytest.mark.unit
def test_register_child_inherits_parent_abort():
    reg = RootAbortRegistry()
    abort = AbortController()
    reg.register_root("root-1", abort)
    ok = reg.register_child(parent_run_id="root-1", child_run_id="child-1")
    assert ok is True
    # Child reads the SAME controller object
    assert reg.get_abort("child-1") is abort


@pytest.mark.unit
def test_grandchild_inherits_root_abort():
    """Multi-level delegation: root → child → grandchild all share."""
    reg = RootAbortRegistry()
    abort = AbortController()
    reg.register_root("root", abort)
    reg.register_child(parent_run_id="root", child_run_id="child")
    reg.register_child(parent_run_id="child", child_run_id="grandchild")
    assert reg.get_abort("grandchild") is abort


@pytest.mark.unit
def test_register_child_unknown_parent_returns_false():
    reg = RootAbortRegistry()
    ok = reg.register_child(parent_run_id="missing", child_run_id="orphan")
    assert ok is False
    assert reg.get_abort("orphan") is None


@pytest.mark.unit
def test_register_child_empty_id_returns_false():
    reg = RootAbortRegistry()
    reg.register_root("root", AbortController())
    assert reg.register_child(parent_run_id="root", child_run_id="") is False


@pytest.mark.unit
def test_get_abort_unknown_returns_none():
    reg = RootAbortRegistry()
    assert reg.get_abort("never") is None


@pytest.mark.unit
def test_cancel_root_fires_abort_for_root_and_children():
    reg = RootAbortRegistry()
    abort = AbortController()
    reg.register_root("root", abort)
    reg.register_child(parent_run_id="root", child_run_id="c1")
    reg.register_child(parent_run_id="root", child_run_id="c2")

    affected = reg.cancel_root("root", reason="user cancelled")
    assert affected == 3  # root + 2 children
    # The single shared controller is fired
    assert reg.get_abort("root").is_aborted() is True
    assert reg.get_abort("c1").is_aborted() is True
    assert reg.get_abort("c2").is_aborted() is True


@pytest.mark.unit
def test_cancel_unknown_root_returns_zero():
    reg = RootAbortRegistry()
    assert reg.cancel_root("missing") == 0


@pytest.mark.unit
def test_unregister_root_removes_children_too():
    reg = RootAbortRegistry()
    reg.register_root("root", AbortController())
    reg.register_child(parent_run_id="root", child_run_id="c1")
    reg.unregister_run("root")
    assert reg.get_abort("root") is None
    assert reg.get_abort("c1") is None
    assert len(reg) == 0


@pytest.mark.unit
def test_unregister_child_only_removes_self():
    reg = RootAbortRegistry()
    reg.register_root("root", AbortController())
    reg.register_child(parent_run_id="root", child_run_id="c1")
    reg.register_child(parent_run_id="root", child_run_id="c2")
    reg.unregister_run("c1")
    assert reg.get_abort("root") is not None
    assert reg.get_abort("c1") is None
    assert reg.get_abort("c2") is not None
    # Snapshot reflects removed child
    snap = reg.snapshot()
    assert "c1" not in snap["root"]["children"]
    assert "c2" in snap["root"]["children"]


@pytest.mark.unit
def test_unregister_idempotent():
    reg = RootAbortRegistry()
    reg.unregister_run("never_existed")  # no exception


@pytest.mark.unit
def test_known_runs_returns_all_sorted():
    reg = RootAbortRegistry()
    reg.register_root("z-root", AbortController())
    reg.register_root("a-root", AbortController())
    reg.register_child(parent_run_id="a-root", child_run_id="m-child")
    runs = reg.known_runs()
    assert runs == ["a-root", "m-child", "z-root"]


@pytest.mark.unit
def test_snapshot_includes_abort_state():
    reg = RootAbortRegistry()
    abort = AbortController()
    reg.register_root("root", abort)
    reg.register_child(parent_run_id="root", child_run_id="c1")

    snap = reg.snapshot()
    assert snap["root"]["is_aborted"] is False
    assert snap["root"]["child_count"] == 1
    assert snap["root"]["children"] == ["c1"]

    abort.fire(reason="test")
    snap2 = reg.snapshot()
    assert snap2["root"]["is_aborted"] is True
