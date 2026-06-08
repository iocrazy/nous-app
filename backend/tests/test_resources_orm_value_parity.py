"""Unit tests for strategy-C value-type parity in ``ResourcesRepositoryOrm``.

AUTHZ-CRITICAL pin. The ORM read helpers (``_orm_obj_to_dict`` / RETURNING
``.mappings()``) return NATIVE Python types — ``uuid.UUID`` for uuid columns
and ``datetime`` for timestamptz columns — whereas the legacy REST (PostgREST)
repo returned them as ``str``. Six consumer comparisons (ai_router x3,
resources_service x3) do ``resource["creator_id"] != user_id`` with a ``str``
user id; a native ``UUID`` makes ``!=`` ALWAYS true → owner wrongly DENIED.

``_rest_parity`` coerces uuid → str + datetime → ISO str (recursing into nested
dicts / lists) so the ORM dict is a true drop-in for the REST baseline. These
tests assert the TYPE (a bare ``==`` assert would pass vacuously) — pure unit,
no DB required.
"""

from __future__ import annotations

import datetime as dt
import uuid

from app.models import Resources
from app.repositories.resources_repository_orm import (
    _resources_row_to_dict,
    _rest_parity,
    _to_rest_value,
)

_UUID = uuid.UUID("11111111-2222-3333-4444-555555555555")
_DT = dt.datetime(2026, 3, 18, 6, 9, 32, 314912, tzinfo=dt.timezone.utc)


def test_uuid_coerced_to_str() -> None:
    out = _rest_parity({"creator_id": _UUID})
    # TYPE assert, not value — a native UUID == str is False, so this is the
    # authz-load-bearing check.
    assert type(out["creator_id"]) is str
    assert out["creator_id"] == "11111111-2222-3333-4444-555555555555"


def test_datetime_coerced_to_iso_str() -> None:
    out = _rest_parity({"created_at": _DT})
    assert type(out["created_at"]) is str
    # tz-aware UTC → ``...+00:00`` (matches PostgREST wire shape).
    assert out["created_at"] == "2026-03-18T06:09:32.314912+00:00"


def test_bigint_ids_stay_int() -> None:
    # BIGINT snowflake ids are int on BOTH sides — must NOT be stringified.
    row = {"id": 7434567890123456789, "media_id": 42, "scope_id": 99, "rating": 5}
    out = _rest_parity(row)
    assert type(out["id"]) is int
    assert out["id"] == 7434567890123456789
    assert type(out["media_id"]) is int
    assert type(out["scope_id"]) is int
    assert type(out["rating"]) is int


def test_nested_resource_subdict_coerced() -> None:
    # The embedded ``resource`` sub-dict (e.g. get_resource_items /
    # get_trashed_resources) must be coerced too — recurse into nested dicts.
    row = {
        "id": 5,
        "created_at": _DT,
        "resource": {
            "id": 9,
            "creator_id": _UUID,
            "created_at": _DT,
            "updated_at": _DT,
        },
    }
    out = _rest_parity(row)
    assert type(out["created_at"]) is str
    nested = out["resource"]
    assert type(nested["creator_id"]) is str
    assert type(nested["created_at"]) is str
    assert type(nested["updated_at"]) is str
    assert type(nested["id"]) is int  # nested bigint id stays int


def test_list_of_dicts_coerced() -> None:
    # A value that is a list of dicts (defensive — recurse into list elements).
    row = {"items": [{"added_by": _UUID, "created_at": _DT, "id": 3}]}
    out = _rest_parity(row)
    assert type(out["items"][0]["added_by"]) is str
    assert type(out["items"][0]["created_at"]) is str
    assert type(out["items"][0]["id"]) is int


def test_none_and_scalars_pass_through() -> None:
    row = {"file_path": None, "filename": "clip.mp4", "duration_seconds": 12}
    out = _rest_parity(row)
    assert out["file_path"] is None
    assert out["filename"] == "clip.mp4"
    assert out["duration_seconds"] == 12


def test_rest_parity_does_not_mutate_input() -> None:
    # Immutable contract: returns a NEW dict, leaves the input untouched.
    row = {"creator_id": _UUID, "created_at": _DT}
    out = _rest_parity(row)
    assert out is not row
    assert type(row["creator_id"]) is uuid.UUID  # input unchanged
    assert type(row["created_at"]) is dt.datetime
    assert type(out["creator_id"]) is str


def test_to_rest_value_scalar_paths() -> None:
    assert type(_to_rest_value(_UUID)) is str
    assert type(_to_rest_value(_DT)) is str
    assert _to_rest_value(123) == 123
    assert _to_rest_value(None) is None
    # plain ``date`` (not just datetime) is coerced via isoformat too.
    assert _to_rest_value(dt.date(2026, 3, 18)) == "2026-03-18"


def test_resources_row_to_dict_coerces_orm_row() -> None:
    # End-to-end through the actual repo read path: build a transient Resources
    # ORM instance (no DB) and confirm _resources_row_to_dict hands back the
    # REST wire shape — uuid creator_id → str, datetime created_at → str — so
    # the 6 consumer authz comparisons see a str (not a native UUID).
    obj = Resources(
        id=7434567890123456789,
        creator_id=_UUID,
        media_id=42,
        filename="clip.mp4",
        source_type="web",
        created_at=_DT,
        updated_at=_DT,
    )
    out = _resources_row_to_dict(obj)
    assert type(out["creator_id"]) is str
    assert out["creator_id"] == str(_UUID)
    assert type(out["created_at"]) is str
    assert type(out["updated_at"]) is str
    assert type(out["id"]) is int  # bigint snowflake stays int
    assert type(out["media_id"]) is int
    # the authz comparison the consumers do — now succeeds for the owner.
    assert out["creator_id"] == str(_UUID)
    assert (out["creator_id"] != str(_UUID)) is False
