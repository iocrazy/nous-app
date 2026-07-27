"""Form-based deliverables schema/validation (mig 390, M3 PR-I task I1).

Covers only the schema/model layer (I1's scope) — the repo write path that
generates ``key`` from ``label`` (slugify + dedupe with a ``-2`` suffix) is
I2's territory and is not exercised here.

Five surfaces under test (per the task brief):
  1. ``FormFieldDef.type`` is a six-value whitelist — anything else is a
     pydantic ``ValidationError`` (→ 422 at the API boundary).
  2. ``options`` is only meaningful for ``type == "select"``: a non-select
     field may not carry it, and a select field must carry a non-empty list.
  3. ``TemplateNodeIn.form_schema`` may hold at most ``MAX_FORM_FIELDS`` (20)
     entries; the 21st raises.
  4. ``FormFieldDef.label`` is stripped and must be non-empty after
     stripping (whitespace-only is rejected; surrounding whitespace is
     trimmed on the way in).
  5. ``NodePatch`` has no ``form_schema`` field at all — an instance may PATCH
     ``form_data`` (the entered values) but can never change what fields
     exist. This is a regression pin on NodePatch's existing "unknown extras
     are silently ignored" behavior (``model_config`` is unset → pydantic v2
     default, which is ``extra="ignore"``): passing ``form_schema`` to the
     constructor does not raise, but it is dropped, not stored — proven by
     checking both ``model_fields`` (declared) and ``model_dump()`` (dumped).

A sixth surface (mig 390, M3 PR-I task I2, FakeSession-backed — same house
pattern as ``test_workflow_flow_rules.py``): ``ProjectStageNodesRepository.
update_node``'s ``form_data`` merge is WHITELIST-filtered against the node's
OWN ``form_schema`` before merging — a key the client sends that the node's
schema doesn't declare is dropped silently, never persisted, and never
clobbers a previously-set key it doesn't mention.
"""

from __future__ import annotations

from contextlib import asynccontextmanager
from typing import Any, List

import pytest
from pydantic import ValidationError

from app.models import ProjectStageNodes
from app.repositories.project_stage_nodes_repository import ProjectStageNodesRepository
from app.schemas.workflow import (
    MAX_FORM_FIELDS,
    FormFieldDef,
    NodeOut,
    NodePatch,
    TemplateNodeIn,
)


def _field(**overrides):
    base = {"label": "Notes", "type": "text"}
    base.update(overrides)
    return base


# ── FormFieldDef.type whitelist ─────────────────────────────────────────────


def test_form_field_type_whitelist_accepts_all_six():
    for t in ("text", "textarea", "number", "select", "checkbox", "date"):
        kwargs = {"label": "X", "type": t}
        if t == "select":
            kwargs["options"] = ["a", "b"]
        field = FormFieldDef(**kwargs)
        assert field.type == t


def test_form_field_type_rejects_unknown_value():
    with pytest.raises(ValidationError):
        FormFieldDef(**_field(type="bogus"))


# ── options only for select, and non-empty when present ────────────────────


def test_options_rejected_for_non_select_type():
    with pytest.raises(ValidationError, match="only allowed when type is 'select'"):
        FormFieldDef(**_field(type="text", options=["a", "b"]))


def test_select_requires_non_empty_options():
    with pytest.raises(ValidationError, match="non-empty options list"):
        FormFieldDef(**_field(type="select"))

    with pytest.raises(ValidationError, match="non-empty options list"):
        FormFieldDef(**_field(type="select", options=[]))


def test_select_with_options_succeeds():
    field = FormFieldDef(**_field(type="select", options=["red", "blue"]))
    assert field.options == ["red", "blue"]


# ── key defaults empty (server-generated in the I2 repo write path) ────────


def test_key_defaults_to_empty_string():
    field = FormFieldDef(**_field())
    assert field.key == ""


# ── label stripped + non-empty ──────────────────────────────────────────────


def test_label_whitespace_only_rejected():
    with pytest.raises(ValidationError, match="label must not be blank"):
        FormFieldDef(**_field(label="   "))


def test_label_surrounding_whitespace_is_stripped():
    field = FormFieldDef(**_field(label="  Notes  "))
    assert field.label == "Notes"


# ── TemplateNodeIn.form_schema: default + MAX_FORM_FIELDS guard ────────────


def _node_kwargs(form_schema):
    return {"name": "Draft", "sort_order": 1, "form_schema": form_schema}


def test_template_node_in_form_schema_defaults_to_empty_list():
    node = TemplateNodeIn(name="Draft", sort_order=1)
    assert node.form_schema == []


def test_template_node_in_accepts_up_to_max_form_fields():
    fields = [_field(label=f"Field {i}") for i in range(MAX_FORM_FIELDS)]
    node = TemplateNodeIn(**_node_kwargs(fields))
    assert len(node.form_schema) == MAX_FORM_FIELDS


def test_template_node_in_rejects_more_than_max_form_fields():
    fields = [_field(label=f"Field {i}") for i in range(MAX_FORM_FIELDS + 1)]
    with pytest.raises(ValidationError, match=f"at most {MAX_FORM_FIELDS}"):
        TemplateNodeIn(**_node_kwargs(fields))


# ── NodeOut carries form_schema + form_data ─────────────────────────────────


def _node_out_kwargs(**overrides):
    base = dict(
        id="1",
        project_id="2",
        source_template_node_id=None,
        legacy_stage_id=None,
        name="Draft",
        sort_order=1,
        parallel_group=None,
        status="pending",
        owner_user_id=None,
        owner_agent_id=None,
        planned_start=None,
        planned_due=None,
        review_required=False,
        deliverable_required=False,
        deliverable_label=None,
        skipped=False,
    )
    base.update(overrides)
    return base


def test_node_out_defaults_form_schema_and_form_data_empty():
    node = NodeOut(**_node_out_kwargs())
    assert node.form_schema == []
    assert node.form_data == {}


def test_node_out_carries_form_schema_and_form_data():
    node = NodeOut(
        **_node_out_kwargs(
            form_schema=[_field(type="select", options=["a"])],
            form_data={"Notes": "a"},
        )
    )
    assert len(node.form_schema) == 1
    assert node.form_schema[0].type == "select"
    assert node.form_data == {"Notes": "a"}
    dumped = node.model_dump()
    assert dumped["form_schema"][0]["options"] == ["a"]
    assert dumped["form_data"] == {"Notes": "a"}


# ── NodePatch has no form_schema field (instance can't change config) ──────


def test_node_patch_has_form_data_but_not_form_schema_declared():
    assert "form_data" in NodePatch.model_fields
    assert "form_schema" not in NodePatch.model_fields


def test_node_patch_ignores_form_schema_kwarg_silently():
    # NodePatch has no explicit model_config, so it inherits pydantic v2's
    # default extra="ignore" — passing an unknown/forbidden field does not
    # raise, but it also is not stored. Pinning that behavior here: if a
    # future change tightened this to extra="forbid", this call would raise
    # instead, which would also be an acceptable (stricter) way to satisfy
    # "form_schema must not reach an instance patch" — but silent-drop is
    # today's actual behavior and must not silently start persisting it.
    patch = NodePatch(form_data={"Notes": "x"}, form_schema=[_field()])
    assert not hasattr(patch, "form_schema")
    dumped = patch.model_dump(exclude_unset=True)
    assert "form_schema" not in dumped
    assert dumped["form_data"] == {"Notes": "x"}


def test_node_patch_form_data_optional_defaults_none():
    patch = NodePatch()
    assert patch.form_data is None


# ── update_node: form_data merge is whitelist-filtered against form_schema ──


class _Result:
    """Wraps a canned row list for the ``.scalars().first()`` access pattern
    ``update_node`` uses to fetch the node — same tiny helper duplicated
    across the FakeSession-backed test modules (test_workflow_flow_rules.py,
    test_workflow_instantiation.py)."""

    def __init__(self, rows: List[Any]):
        self._rows = list(rows)

    def scalars(self) -> "_Result":
        return self

    def first(self) -> Any:
        return self._rows[0] if self._rows else None


def _write_scope_with(session: Any):
    @asynccontextmanager
    async def _scope():
        yield session

    return _scope


class _NodeFakeSession:
    """Just enough to drive ``update_node``'s single node-select — no
    members/schedule branch is exercised by these tests, so exactly one
    ``execute`` call happens."""

    def __init__(self, node: ProjectStageNodes):
        self._node = node

    async def execute(self, stmt: Any) -> _Result:
        return _Result([self._node])


def _live_node(*, form_schema, form_data) -> ProjectStageNodes:
    return ProjectStageNodes(
        id=10,
        project_id=50,
        source_template_node_id=None,
        legacy_stage_id=None,
        name="Draft",
        sort_order=1,
        parallel_group=None,
        status="pending",
        owner_user_id=None,
        owner_agent_id=None,
        planned_start=None,
        planned_due=None,
        review_required=False,
        deliverable_required=False,
        deliverable_label=None,
        skipped=False,
        completion_policy="owner",
        events={
            "notify_on_arrival": True,
            "notify_on_complete": False,
            "suggest_agent_run": False,
        },
        form_schema=form_schema,
        form_data=form_data,
    )


async def _install_and_call(monkeypatch, node: ProjectStageNodes, **update_kwargs):
    import app.repositories.project_stage_nodes_repository as mod

    monkeypatch.setattr(mod, "write_scope", _write_scope_with(_NodeFakeSession(node)))

    repo = ProjectStageNodesRepository()

    async def _fake_get_node(node_id, project_id):
        return {"sentinel": True}

    monkeypatch.setattr(repo, "get_node", _fake_get_node)

    result = await repo.update_node("10", "50", **update_kwargs)
    assert result == {"sentinel": True}  # proves get_node's return value ships through
    return node


@pytest.mark.asyncio
async def test_update_node_drops_unknown_form_data_key(monkeypatch):
    node = _live_node(
        form_schema=[
            {"key": "notes", "label": "Notes", "type": "text", "required": True}
        ],
        form_data={},
    )

    await _install_and_call(
        monkeypatch, node, form_data={"notes": "hello", "bogus_key": "dropped"}
    )

    assert node.form_data == {"notes": "hello"}


@pytest.mark.asyncio
async def test_update_node_merges_without_clobbering_untouched_keys(monkeypatch):
    node = _live_node(
        form_schema=[
            {"key": "notes", "label": "Notes", "type": "text", "required": True},
            {"key": "count", "label": "Count", "type": "number", "required": False},
        ],
        form_data={"notes": "old", "count": 3},
    )

    await _install_and_call(monkeypatch, node, form_data={"notes": "new"})

    # notes updated; count (not mentioned in this patch) survives untouched.
    assert node.form_data == {"notes": "new", "count": 3}


@pytest.mark.asyncio
async def test_update_node_no_form_schema_drops_every_key(monkeypatch):
    """A node with an empty form_schema (e.g. pre-mig-390, or simply a node
    with no form configured) has no allowed keys at all — any form_data patch
    is entirely dropped, never persisted. Zero impact on a workflow that
    never touches this feature."""
    node = _live_node(form_schema=[], form_data={})

    await _install_and_call(monkeypatch, node, form_data={"anything": "value"})

    assert node.form_data == {}


@pytest.mark.asyncio
async def test_update_node_without_form_data_kwarg_leaves_form_data_untouched(
    monkeypatch,
):
    """form_data=None (the default — caller didn't touch it) must not run the
    merge at all, so a node's existing form_data survives a PATCH that only
    changes e.g. skipped."""
    node = _live_node(
        form_schema=[
            {"key": "notes", "label": "Notes", "type": "text", "required": True}
        ],
        form_data={"notes": "existing"},
    )

    await _install_and_call(monkeypatch, node, skipped=True)

    assert node.form_data == {"notes": "existing"}
