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
"""

from __future__ import annotations

import pytest
from pydantic import ValidationError

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
