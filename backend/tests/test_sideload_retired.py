"""Sideload import is retired (2026-09-07).

The workflow registered files already present on the NAS volume. No
container mounts that volume any more (media work dir moved to local NVMe,
finished media lives in S3), so the inbox directory can never exist and the
endpoint could only ever answer 404. The whole track is gone: workflow,
dispatch bundle registration, and the ``POST /resources/sideload`` route.
"""

from __future__ import annotations

import importlib

import pytest


def test_sideload_workflow_module_is_gone():
    with pytest.raises(ModuleNotFoundError):
        importlib.import_module("app.workflows.sideload")


def test_dispatch_bundle_does_not_register_sideload():
    import app.workflows._dispatch_bundle as bundle

    assert not hasattr(bundle, "sideload_workflow")


def test_resources_router_has_no_sideload_route():
    from app.api.resources_upload_router import router

    paths = {getattr(r, "path", "") for r in router.routes}
    assert not any(p.endswith("/sideload") for p in paths), paths
