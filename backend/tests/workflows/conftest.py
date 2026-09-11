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


@pytest.fixture(autouse=True)
def _restore_workflow_module_identity():
    """Put back every ``app.workflows*`` module object the test swapped out.

    Entries the test *added* are left in place -- re-importing a module that
    was not loaded before is not a leak, and dropping it would only force a
    pointless re-import later. Only entries whose identity changed (or which
    were deleted outright) are restored.
    """
    before = {name: mod for name, mod in sys.modules.items() if _is_watched(name)}
    registry_snapshot = _snapshot_registry()
    role_before = os.environ.get("MEDIAHUB_ROLE")
    try:
        yield
    finally:
        for name, module in before.items():
            if sys.modules.get(name) is not module:
                sys.modules[name] = module
        _restore_registry(registry_snapshot)
        if role_before is None:
            os.environ.pop("MEDIAHUB_ROLE", None)
        else:
            os.environ["MEDIAHUB_ROLE"] = role_before
