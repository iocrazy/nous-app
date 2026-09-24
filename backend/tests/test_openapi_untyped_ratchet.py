"""Ratchet: the number of JSON routes with no response schema only goes down.

A route with no ``response_model`` (or a bare ``-> dict``) exports as ``{}``
in ``backend/openapi.json``, and the frontend gets ``unknown`` for it: its
shape lives only in a hand-written copy in ``frontend/types.ts``, which
nothing checks against the backend. This test pins how many such routes
exist, in both directions:

- **more than the snapshot** fails and lists the routes that are not in
  the committed contract's untyped set: declare a ``response_model``;
- **fewer than the snapshot** fails too, asking for the snapshot to be
  lowered, so the progress is locked in and cannot be spent later.

Same shape as the ORM index ratchet in ``tests/db/test_schema_drift.py``.

It reads the committed ``openapi.json``. The ``Export OpenAPI & diff`` CI
step separately proves that file is current, so the two together cover the
live app without this test importing it.

The judgment (what "untyped" means) is ``untyped_operations`` in
``scripts/export_openapi.py``, shared with the exporter's summary line.

Spec: docs/superpowers/specs/2026-09-24-openapi-typed-frontend-design.md §3.3
"""

from __future__ import annotations

import json

import scripts.export_openapi as exporter

SNAPSHOT = exporter.RATCHET_COUNT
BASELINE_LIST = exporter.RATCHET_LIST


def _current() -> list[str]:
    schema = json.loads(exporter.DEFAULT_OUTPUT.read_text())
    return exporter.untyped_operations(schema)


def _baseline() -> set[str]:
    lines = BASELINE_LIST.read_text().splitlines()
    return {line for line in lines if line.strip() and not line.startswith("#")}


def test_untyped_count_matches_snapshot() -> None:
    current = _current()
    allowed = int(SNAPSHOT.read_text().strip())
    if len(current) > allowed:
        new = sorted(set(current) - _baseline())
        listing = "\n  ".join(new) or "(none by name: an operation was renamed?)"
        raise AssertionError(
            f"{len(current)} JSON routes have no response schema; the ratchet "
            f"allows {allowed}. Declare a response_model on:\n  {listing}"
        )
    assert len(current) == allowed, (
        f"Only {len(current)} JSON routes lack a response schema now (snapshot "
        f"says {allowed}). Lock the progress in by rewriting "
        "the snapshot: `uv run python scripts/export_openapi.py --write-ratchet`."
    )


def test_baseline_list_matches_count() -> None:
    """The name list and the number move together, so a diff shows which
    routes got typed, and the "new routes" hint above stays accurate."""
    baseline, current = _baseline(), set(_current())
    assert len(baseline) == int(SNAPSHOT.read_text().strip())
    added, removed = sorted(current - baseline), sorted(baseline - current)
    assert not added and not removed, (
        "The untyped set changed (a route got typed while another lost its "
        f"schema, or an operation was renamed).\nNewly untyped: {added}\n"
        f"No longer untyped: {removed}\nType the new ones; then run "
        "`uv run python scripts/export_openapi.py --write-ratchet`."
    )


def test_empty_schema_is_untyped() -> None:
    schema = {
        "paths": {
            "/a": {"get": _op({})},
            "/b": {"get": _op({"type": "object", "additionalProperties": True})},
            "/c": {"get": _op({"type": "array", "items": {}})},
        }
    }
    assert len(exporter.untyped_operations(schema)) == 3


def test_structured_or_non_json_is_typed() -> None:
    schema = {
        "paths": {
            "/ref": {"get": _op({"$ref": "#/components/schemas/X"})},
            "/list": {"get": _op({"type": "array", "items": {"$ref": "#/x"}})},
            "/str": {"get": _op({"type": "string"})},
            "/opt": {"get": _op({"anyOf": [{"$ref": "#/x"}, {"type": "null"}]})},
            "/file": {"get": {"responses": {"200": {"description": "file"}}}},
            "/gone": {"delete": {"responses": {"204": {"description": "none"}}}},
        }
    }
    assert exporter.untyped_operations(schema) == []


def test_lowest_success_code_is_the_success_response() -> None:
    op = {
        "responses": {
            "201": {"content": {"application/json": {"schema": {}}}},
            "202": {"content": {"application/json": {"schema": {"$ref": "#/x"}}}},
        }
    }
    assert len(exporter.untyped_operations({"paths": {"/p": {"post": op}}})) == 1


def _op(schema: dict) -> dict:
    return {
        "operationId": "op",
        "responses": {"200": {"content": {"application/json": {"schema": schema}}}},
    }
