"""The admin response says whether a catalog row is private to one user.

WHY THIS WAS MISSING AND WHY IT MATTERS
───────────────────────────────────────
Migration 431 gave ``nous_models`` an ``owner_user_id``: a non-NULL owner
makes the row that ONE user's, and RLS plus ``list_enabled(viewer_user_id=…)``
hide it from everybody else. The admin page, however, never received the field
— ``_to_response`` simply did not project it.

The cost is not theoretical. Production on 2026-09-21:

    codex-image         enabled, owner_user_id = 8e1584e3-…   ← private
    codex-local-image   enabled, owner_user_id = NULL         ← platform-wide

Two image rows, both enabled, both driving a gpt-image binary, rendered by the
admin page as two indistinguishable cards. An admin looking at that screen has
no way to learn that one of them serves exactly one account — which is also why
those rows read as "retired duplicates" from any vantage point that could not
see them.

Same family as the `not_probed` fix: a fact the backend already knew, that
never reached the surface where somebody could act on it.
"""

from __future__ import annotations

import pytest

from app.api.admin.nous_model_router import _to_response


def _row(**over):
    base = {
        "id": 123,
        "name": "codex-image",
        "display_name": "GPT Image (Codex)",
        "type": "image",
        "actual_provider": "codex",
        "actual_model": "gpt-6-astra",
        "api_key": "",
        "pricing_type": "per_call",
        "pricing_value": 0,
        "is_enabled": True,
        "sort_order": 1,
    }
    base.update(over)
    return base


@pytest.mark.unit
def test_owner_scoped_row_reports_its_owner():
    resp = _to_response(_row(owner_user_id="8e1584e3-9c29-4a5b-90fe-125b74259f7f"))
    assert resp.owner_user_id == "8e1584e3-9c29-4a5b-90fe-125b74259f7f"


@pytest.mark.unit
def test_platform_row_reports_no_owner():
    """The negative control. Without it a projection that hardcoded some
    truthy placeholder would pass the test above and mark every card private.
    """
    assert _to_response(_row(owner_user_id=None)).owner_user_id is None
    # A row that predates migration 431 has no key at all, not a None value.
    assert _to_response(_row()).owner_user_id is None


@pytest.mark.unit
def test_owner_is_serialized_as_a_string():
    """asyncpg hands back a uuid.UUID, and the admin page compares/render it as
    text. Pydantic would coerce, but the coercion is the contract here — the
    id column's own `str(row["id"])` treatment exists for the same reason.
    """
    import uuid

    oid = uuid.UUID("8e1584e3-9c29-4a5b-90fe-125b74259f7f")
    resp = _to_response(_row(owner_user_id=oid))
    assert isinstance(resp.owner_user_id, str)
    assert resp.owner_user_id == str(oid)
