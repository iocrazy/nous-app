"""Undo the process-global damage the reload tests in this directory do.

Two files here deliberately tear down and rebuild ``app.workflows`` to prove
role-aware import wiring: ``test_role_aware_imports.py`` and
``test_session_health_check.py`` both delete every ``app.workflows*`` entry
from ``sys.modules``, clear DBOS's process-global decorator registry, set
``MEDIAHUB_ROLE`` and re-import the package. Tearing down is the point of
those tests. Not putting anything back is the bug.

What leaks is *module identity*. A test module that did
``from app.workflows.issue_lifecycle import _run_reply_turns`` at collection
time holds a function whose ``__globals__`` is the **original** module dict.
After a purge-and-reimport, ``sys.modules["app.workflows.issue_lifecycle"]``
is a *different* module object. ``mock.patch`` resolves its target through
``sys.modules``, so it patches the new module while the test calls the old
function, which resolves its own globals and reaches the unpatched original.
The patch silently does nothing: no error, just a mock that is never called.
That is exactly how ``tests/test_issue_reply_resume.py`` went red when it ran
after ``tests/workflows/`` and green when it ran before.

So this fixture restores the three pieces of global state those tests move:
the ``sys.modules`` entries (identity, not just presence), the DBOS registry,
and ``MEDIAHUB_ROLE``. It is autouse so that a future test which reloads
modules is covered without anyone remembering this file exists.

Note the deliberate non-fixes: ``-p no:randomly``, reordering, and
``pytest.mark.order`` all make the symptom go away while leaving a suite whose
result depends on collection order.
"""

from __future__ import annotations

import os
import sys

import pytest

# Packages the reload tests purge. Anything imported from one of these can have
# its module identity swapped out mid-session.
_WATCHED_PREFIXES = ("app.workflows", "app.services.liveness")

# The registry attributes the reload helpers clear. ``pollers`` is a list, the
# rest are dicts; ``test_session_health_check`` clears all six.
_REGISTRY_MAPS = (
    "queue_info_map",
    "workflow_info_map",
    "function_type_map",
    "instance_info_map",
    "class_info_map",
)


def _is_watched(name: str) -> bool:
    if not name.startswith(_WATCHED_PREFIXES):
        return False
    return any(name == p or name.startswith(p + ".") for p in _WATCHED_PREFIXES)


def _snapshot_registry() -> tuple | None:
    """Shallow-copy DBOS's global registry, or ``None`` if it is unsafe to touch.

    Same safety boundary the two reload helpers already use: a live ``DBOS()``
    instance holds a reference to this registry, so neither clearing nor
    restoring it behind the instance's back is sound. In that case we leave it
    alone -- the module-identity restore below is independent and still runs.
    """
    import dbos._dbos as dbos_internals

    if getattr(dbos_internals, "_dbos_global_instance", None) is not None:
        return None
    registry = dbos_internals._get_or_create_dbos_registry()
    maps = {
        attr: dict(getattr(registry, attr))
        for attr in _REGISTRY_MAPS
        if isinstance(getattr(registry, attr, None), dict)
    }
    pollers = list(getattr(registry, "pollers", []))
    return registry, maps, pollers


def _restore_registry(snapshot: tuple | None) -> None:
    if snapshot is None:
        return
    registry, maps, pollers = snapshot
    for attr, saved in maps.items():
        live = getattr(registry, attr)
        live.clear()
        live.update(saved)
    live_pollers = getattr(registry, "pollers", None)
    if isinstance(live_pollers, list):
        live_pollers[:] = pollers


def _restore_parent_attributes(names) -> None:
    """Re-point each parent package's attribute at the restored submodule.

    Putting a module object back into ``sys.modules`` only fixes one of the two
    ways Python hands out a module. The import system also binds each submodule
    as an *attribute of its parent package*, and ``import app.workflows`` /
    ``from app import workflows`` read that attribute rather than
    ``sys.modules``. A purge-and-reimport rebinds it to the new module, and the
    parent is usually outside the purged set -- ``app`` is never deleted, so
    nothing puts its ``workflows`` attribute back.

    Leaving it split is the same bug one level up: ``sys.modules`` says old,
    attribute traversal says new. ``mock.patch`` resolving through
    ``sys.modules`` is what makes the reply-resume test pass, but any consumer
    that traverses attributes would get the other module and silently diverge.

    Sorted so parents are fixed before their children, and every lookup goes
    through ``sys.modules`` so a parent that was itself restored is already the
    restored object by the time its children are re-pointed.
    """
    for name in sorted(names):
        parent_name, _, leaf = name.rpartition(".")
        if not parent_name:
            continue
        parent = sys.modules.get(parent_name)
        module = sys.modules.get(name)
        if parent is None or module is None:
            continue
        if getattr(parent, leaf, None) is not module:
            setattr(parent, leaf, module)


def restore_modules(before: dict) -> None:
    """Restore both views of every module in ``before`` whose identity moved.

    ``before`` maps dotted name -> the module object that was in
    ``sys.modules`` beforehand. Exposed (public name) so the guard test in this
    directory can exercise the restore directly rather than re-deriving it.
    """
    for name, module in before.items():
        if sys.modules.get(name) is not module:
            sys.modules[name] = module
    _restore_parent_attributes(before.keys())


def snapshot_modules() -> dict:
    """The ``sys.modules`` entries this directory's reload tests may disturb."""
    return {name: mod for name, mod in sys.modules.items() if _is_watched(name)}


@pytest.fixture(autouse=True)
def _restore_workflow_module_identity():
    """Put back every ``app.workflows*`` module object the test swapped out.

    Both ways Python hands out a module are restored: the ``sys.modules`` entry
    and the parent package's attribute (see ``_restore_parent_attributes``).
    Fixing only the first leaves the two views disagreeing.

    Entries the test *added* are left in place -- re-importing a module that
    was not loaded before is not a leak, and dropping it would only force a
    pointless re-import later. Only entries whose identity changed (or which
    were deleted outright) are restored.
    """
    before = snapshot_modules()
    registry_snapshot = _snapshot_registry()
    role_before = os.environ.get("MEDIAHUB_ROLE")
    try:
        yield
    finally:
        restore_modules(before)
        _restore_registry(registry_snapshot)
        if role_before is None:
            os.environ.pop("MEDIAHUB_ROLE", None)
        else:
            os.environ["MEDIAHUB_ROLE"] = role_before
