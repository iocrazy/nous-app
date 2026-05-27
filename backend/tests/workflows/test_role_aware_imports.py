"""Verify gateway role imports only dispatch-needed workflows.

Worker role imports both bundles. Gateway role skips _scheduled_bundle.
Also pins that the bootstrap-side reconcile import does NOT pull the
@DBOS.scheduled-bearing liveness_scanner module on the gateway.
"""

from __future__ import annotations

import importlib
import sys

import dbos._dbos as _dbos_internals


def _purge(module_prefix: str) -> None:
    for name in list(sys.modules.keys()):
        if name == module_prefix or name.startswith(module_prefix + "."):
            del sys.modules[name]


def _reset_dbos_registry() -> None:
    """Clear DBOS's process-global decorator registry between tests.

    SAFETY: this is ONLY safe when no DBOS() instance has been
    constructed in this test process. A live DBOS() instance holds
    a reference to the same registry; clearing the maps while it's
    alive leaves the instance in an inconsistent state and can
    corrupt unrelated tests that launch DBOS later in the same
    pytest process. The guard below skips the reset if an instance
    is live, on the theory that any DBOS-touching test should
    isolate via its own conftest fixture, not this helper.
    """
    if getattr(_dbos_internals, "_dbos_global_instance", None) is not None:
        return  # don't clobber a live DBOS — caller's test will fail loudly
    registry = _dbos_internals._get_or_create_dbos_registry()
    for attr in (
        "queue_info_map",
        "workflow_info_map",
        "function_type_map",
        "instance_info_map",
        "class_info_map",
    ):
        getattr(registry, attr, {}).clear()


def test_gateway_role_skips_scheduled_bundle(monkeypatch):
    """When MEDIAHUB_ROLE=gateway, _scheduled_bundle must not be imported."""
    monkeypatch.setenv("MEDIAHUB_ROLE", "gateway")
    _purge("app.workflows")
    _reset_dbos_registry()
    importlib.import_module("app.workflows")
    # Dispatch bundle is always loaded.
    assert "app.workflows._dispatch_bundle" in sys.modules
    # Scheduled bundle is NOT loaded on gateway.
    assert "app.workflows._scheduled_bundle" not in sys.modules


def test_worker_role_loads_both_bundles(monkeypatch):
    """When MEDIAHUB_ROLE=worker, both bundles must be imported."""
    monkeypatch.setenv("MEDIAHUB_ROLE", "worker")
    _purge("app.workflows")
    _reset_dbos_registry()
    importlib.import_module("app.workflows")
    assert "app.workflows._dispatch_bundle" in sys.modules
    assert "app.workflows._scheduled_bundle" in sys.modules


def test_combined_role_loads_both_bundles(monkeypatch):
    """combined (legacy single-process) must also import both bundles."""
    monkeypatch.setenv("MEDIAHUB_ROLE", "combined")
    _purge("app.workflows")
    _reset_dbos_registry()
    importlib.import_module("app.workflows")
    assert "app.workflows._dispatch_bundle" in sys.modules
    assert "app.workflows._scheduled_bundle" in sys.modules


def test_bootstrap_import_path_does_not_load_liveness_scanner(monkeypatch):
    """Importing reconcile_stranded_runs via the new path must NOT load
    liveness_scanner (which has @DBOS.scheduled) on the gateway."""
    monkeypatch.setenv("MEDIAHUB_ROLE", "gateway")
    _purge("app.workflows")
    _purge("app.services.liveness")
    _reset_dbos_registry()
    importlib.import_module("app.services.liveness.reconcile")
    assert "app.workflows.liveness_scanner" not in sys.modules
