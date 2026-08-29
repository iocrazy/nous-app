import datetime

from app.repositories.asset_relations_repository import (
    _serialize_file,
    _serialize_link,
    _serialize_loadout,
)

NOW = datetime.datetime(2026, 8, 28, tzinfo=datetime.timezone.utc)


def test_serialize_file_numeric_ids_become_strings():
    out = _serialize_file(
        {
            "asset_id": 727145299382534145,
            "resource_id": 727145299382534146,
            "slot": "sheet",
            "loadout_id": None,
            "sort_order": 0,
            "note": None,
            "attached_by": None,
            "attached_at": NOW,
        }
    )
    assert out["asset_id"] == "727145299382534145"
    assert out["resource_id"] == "727145299382534146"
    assert out["loadout_id"] is None
    assert out["attached_at"] == NOW.isoformat()


def test_serialize_link():
    out = _serialize_link(
        {"from_asset_id": 1, "to_asset_id": 2, "relation": "wears", "created_at": NOW}
    )
    assert out == {
        "from_asset_id": "1",
        "to_asset_id": "2",
        "relation": "wears",
        "created_at": NOW.isoformat(),
    }


def test_serialize_loadout_arrays_become_string_lists():
    out = _serialize_loadout(
        {
            "id": 10,
            "asset_id": 5,
            "name": "Night raid",
            "is_default": False,
            "costume_ids": [727145299382534147],
            "prop_ids": [],
            "prompt_extra": None,
            "sort_order": 0,
            "created_at": NOW,
        }
    )
    assert out["id"] == "10" and out["asset_id"] == "5"
    assert out["costume_ids"] == ["727145299382534147"] and out["prop_ids"] == []
