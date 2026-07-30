"""Regression pins for resources-table column drift in raw SQL.

2026-07-30 production find: resource_ref_resolver selected ``r.file_size``
and ``r.description`` — the real columns are ``file_size_bytes`` and
``notes`` (CLAUDE.md schema-drift family). The failure was SILENT for the
user: resolution warned non-fatally, the <available_resources> block and
the ResourceFetch tool were simply never wired, and the agent bluffed
about the image. resource_fetch_tool carried the same ``r.description``.

Mocked-DB unit tests can't catch drifted column names, so these tests at
least pin the module source against regressing to the known-bad names.
The authoritative check remains information_schema against the live DB.
"""

from __future__ import annotations

import inspect

import pytest


def _source(module) -> str:
    return inspect.getsource(module)


@pytest.mark.unit
def test_resource_ref_resolver_uses_real_column_names():
    from app.services.ai.chat import resource_ref_resolver as m

    src = _source(m)
    assert "file_size_bytes" in src
    assert "r.file_size " not in src and "r.file_size\n" not in src
    assert "r.description" not in src
    assert "r.notes" in src


@pytest.mark.unit
def test_resource_fetch_tool_uses_real_column_names():
    from app.services.ai.tools import resource_fetch_tool as m

    src = _source(m)
    assert "r.description" not in src
    assert "r.notes" in src
