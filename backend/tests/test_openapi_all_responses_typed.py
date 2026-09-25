"""Contract gate: every JSON route declares its response schema. Zero tolerance.

A route with no ``response_model`` (or a bare ``-> dict``) exports as ``{}``
in ``backend/openapi.json``, and the frontend gets ``unknown`` for it: its
shape lives only in a hand-written copy, which nothing checks against the
backend.

This used to be a ratchet (a committed count that could only go down, P2 to
P8 of the spec). P9 brought the count to zero, so the snapshot files are gone
and the rule is absolute: any untyped JSON success response fails, and the
failure lists the routes. Declare a ``response_model``, or, for a route that
does not return JSON (a file, SSE, a third-party callback receipt), declare
its real media type with ``response_class`` / ``responses=``.

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


def test_every_json_route_declares_a_response_schema() -> None:
    schema = json.loads(exporter.DEFAULT_OUTPUT.read_text())
    untyped = exporter.untyped_operations(schema)
    listing = "\n  ".join(untyped)
    assert not untyped, (
        f"{len(untyped)} JSON route(s) have no response schema. Declare a "
        "response_model (or the real non-JSON media type), then re-export "
        f"with `uv run python scripts/export_openapi.py`:\n  {listing}"
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
