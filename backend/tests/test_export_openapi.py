"""Tests for the offline OpenAPI exporter (``scripts/export_openapi.py``).

The CI step ``Export OpenAPI & diff`` regenerates ``backend/openapi.json`` and
fails on any diff, so the export must be byte-reproducible. These tests pin the
two guards that keep it so: duplicate operationIds and same-named Pydantic
models (FastAPI names colliding models in set-iteration order, which changes
from process to process).
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

import scripts.export_openapi as mod


def test_render_is_sorted_and_newline_terminated() -> None:
    text = mod.render({"b": 1, "a": {"d": 2, "c": "中"}})
    assert text.endswith("}\n")
    assert text.index('"a"') < text.index('"b"')
    assert text.index('"c"') < text.index('"d"')
    assert "中" in text  # not \\u-escaped: keeps diffs readable


def test_find_name_collisions_flags_module_qualified_names() -> None:
    schema = {
        "components": {
            "schemas": {
                "TagCreate": {},
                "app__api__admin__tags_router__TagCreate": {},
            }
        }
    }
    assert mod.find_name_collisions(schema) == [
        "app__api__admin__tags_router__TagCreate"
    ]


def test_find_name_collisions_empty_for_clean_schema() -> None:
    assert mod.find_name_collisions({"components": {"schemas": {"A": {}}}}) == []


@pytest.fixture(scope="module")
def live_schema() -> dict:
    return mod.build_schema()


def test_live_app_has_unique_operation_ids(live_schema: dict) -> None:
    ids = [
        op["operationId"]
        for item in live_schema["paths"].values()
        for op in item.values()
        if isinstance(op, dict) and "operationId" in op
    ]
    assert len(ids) == len(set(ids))


def test_live_app_has_no_model_name_collisions(live_schema: dict) -> None:
    assert mod.find_name_collisions(live_schema) == []


def test_check_mode_reports_stale_file(tmp_path: Path, live_schema: dict) -> None:
    out = tmp_path / "openapi.json"
    out.write_text(json.dumps({"stale": True}))
    assert mod.main(["--check", "--output", str(out)]) == 1
    out.write_text(mod.render(live_schema))
    assert mod.main(["--check", "--output", str(out)]) == 0
