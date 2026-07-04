# backend/tests/test_admin_nous_response.py
"""admin nous _to_response maps type + description (api_key still masked)."""

from app.api.admin.mediahub_model_router import _to_response


def test_to_response_maps_type_and_description():
    row = {
        "id": 123,
        "name": "nous-asr",
        "display_name": "Nous ASR",
        "type": "asr",
        "description": "short clips",
        "actual_provider": "volcengine",
        "actual_model": "seed-asr",
        "api_key": "sk-supersecret",
        "pricing_type": "per_hour",
        "pricing_value": 8,
        "is_enabled": True,
        "sort_order": 0,
    }
    resp = _to_response(row)
    assert resp.type == "asr"
    assert resp.description == "short clips"
    assert resp.api_key_masked.endswith("cret")
    assert "supersecret" not in resp.api_key_masked
