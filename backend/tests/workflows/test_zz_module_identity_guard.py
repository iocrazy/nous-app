"""Regression guard: nothing in this directory may leak module identity.

Two files here purge ``app.workflows*`` from ``sys.modules`` and re-import it
(``test_role_aware_imports.py``, ``test_session_health_check.py``). Without the
restore in this directory's ``conftest.py``, every later test that bound a
symbol at collection time keeps calling the *old* module while ``mock.patch``
resolves the *new* one through ``sys.modules`` -- a patch that silently does
nothing. That is how ``tests/test_issue_reply_resume.py`` went red when it ran
after this directory and green when it ran before.

**Why the guard lives here and sorts last.** The full-suite gate cannot see
that class of bug: in collection order the victim sits around index 8966 and
the leakers around 13117, so the victim always runs *first* and the leak lands
behind it. A guard placed at the end of this directory runs *after* the
leakers, which makes the leak visible to the ordinary ``pytest tests`` run
instead of only to whoever happens to invoke a subset. ``test_zz_`` is the
sort key doing that work -- pytest executes files in collection order, and no
ordering plugin is installed (no pytest-randomly / pytest-order / xdist), so
this is deterministic rather than lucky.

If this file goes red, do not relax it: some test in this directory tore down
``app.workflows`` and did not put it back.
"""

from __future__ import annotations

import importlib
import sys

import pytest

from app.workflows.issue_lifecycle import _run_reply_turns

# NB: the conftest helpers are imported inside the one test that needs them,
# not at module level. The guard below must stay runnable when this
# directory's conftest is absent -- that is precisely the state it exists to
# detect, and a module-level import would turn a meaningful identity failure
# into a collection error that proves nothing.

# Bound at COLLECTION time, before any test in this directory has run. This is
# exactly the binding a normal test module makes with a module-level
# ``from ... import ...``, and exactly what a purge invalidates.
_PKG_AT_COLLECTION = sys.modules["app.workflows"]
_LEAF_AT_COLLECTION = sys.modules["app.workflows.issue_lifecycle"]
_BOUND_AT_COLLECTION = _run_reply_turns

_REGISTRY_MAPS = (
    "queue_info_map",
    "workflow_info_map",
    "function_type_map",
    "instance_info_map",
    "class_info_map",
)


def test_collection_time_binding_still_matches_the_live_module() -> None:
    """The guard proper. Both views must agree with what collection saw.

    ``sys.modules`` identity and parent-attribute identity are asserted
    separately because they break separately: restoring only the first leaves
    ``import app.workflows`` and ``from app import workflows`` handing out
    different module objects.
    """
    live = sys.modules["app.workflows.issue_lifecycle"]

    assert live.__dict__ is _BOUND_AT_COLLECTION.__globals__, (
        "a test in tests/workflows/ purged app.workflows and did not restore "
        "it: symbols bound at collection time now resolve against a dead "
        "module, so mock.patch on this module patches an object nobody calls"
    )
    assert live is _LEAF_AT_COLLECTION
    assert sys.modules["app.workflows"] is _PKG_AT_COLLECTION

    # Attribute traversal -- the other way Python hands out a module.
    assert getattr(sys.modules["app"], "workflows") is sys.modules["app.workflows"]
    assert getattr(sys.modules["app.workflows"], "issue_lifecycle") is live


def _purge_and_reimport() -> None:
    """Do to the process exactly what the two reload tests here do."""
    import dbos._dbos as dbos_internals

    if getattr(dbos_internals, "_dbos_global_instance", None) is not None:
        pytest.skip("a live DBOS instance is present; registry reset unsafe")
    for name in [n for n in sys.modules if n.startswith("app.workflows")]:
        del sys.modules[name]
    registry = dbos_internals._get_or_create_dbos_registry()
    for attr in _REGISTRY_MAPS:
        getattr(registry, attr, {}).clear()
    registry.pollers.clear()
    importlib.import_module("app.workflows")
    importlib.import_module("app.workflows.issue_lifecycle")


def test_restore_puts_back_sys_modules_and_the_parent_attribute() -> None:
    """Unit-test the restore against a simulated purge, both views.

    Self-contained on purpose: it drives the real helpers rather than relying
    on the autouse fixture having fired between two tests, so it cannot pass by
    accident of ordering. The fixture's own wiring is covered by the guard
    above, which is green only because the fixture ran after the real leakers.
    """
    from .conftest import restore_modules, snapshot_modules

    before = snapshot_modules()

    _purge_and_reimport()

    # The simulation has to actually break something, or the restore below
    # would be asserting nothing. Both views are checked so a future change
    # that fixes one and not the other still fails loudly here.
    assert sys.modules["app.workflows.issue_lifecycle"] is not _LEAF_AT_COLLECTION
    assert getattr(sys.modules["app"], "workflows") is not _PKG_AT_COLLECTION

    restore_modules(before)

    assert sys.modules["app.workflows"] is _PKG_AT_COLLECTION
    assert sys.modules["app.workflows.issue_lifecycle"] is _LEAF_AT_COLLECTION
    assert getattr(sys.modules["app"], "workflows") is _PKG_AT_COLLECTION
    assert (
        getattr(sys.modules["app.workflows"], "issue_lifecycle") is _LEAF_AT_COLLECTION
    )
    assert (
        sys.modules["app.workflows.issue_lifecycle"].__dict__
        is _BOUND_AT_COLLECTION.__globals__
    )
