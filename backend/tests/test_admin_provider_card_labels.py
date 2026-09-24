"""Admin provider card names: ``ai_provider_card_labels`` system setting.

The shape check lives in ``settings_validation`` (so the generic settings
PATCH and the dedicated ``/nous-models/card-labels`` endpoints reject the same
things); the endpoints are called directly, per the admin endpoint convention.
"""

from __future__ import annotations

import importlib
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, patch

import pytest
from fastapi import HTTPException

from app.api.admin.settings_validation import (
    AI_PROVIDER_CARD_LABELS_KEY,
    CARD_LABEL_MAX_LEN,
    SETTING_VALIDATORS,
    SettingValidationError,
    validate_setting_value,
)
from app.schemas.nous_model import CardLabelsUpdate

# ``app.api.admin`` re-exports the APIRouter under the module's name, so a
# plain ``from app.api.admin import nous_model_router`` yields the router.
router_mod = importlib.import_module("app.api.admin.nous_model_router")

KEY = AI_PROVIDER_CARD_LABELS_KEY


def test_key_is_registered_in_the_generic_allowlist() -> None:
    assert KEY in SETTING_VALIDATORS


@pytest.mark.parametrize(
    "value",
    [
        {},
        {"nous|": "Nous Engine"},
        {"openai|https://api.openai.com/v1": "OpenAI (team key)"},
        {"qwen|": "x" * CARD_LABEL_MAX_LEN},
    ],
)
def test_legal_shapes_round_trip(value: dict[str, str]) -> None:
    assert validate_setting_value(KEY, value) == value


def test_names_are_trimmed_and_blank_names_dropped() -> None:
    raw = {"nous|": "  Engine  ", "qwen|": "", "ark|": "   "}
    assert validate_setting_value(KEY, raw) == {"nous|": "Engine"}
    # Input untouched (immutability).
    assert raw == {"nous|": "  Engine  ", "qwen|": "", "ark|": "   "}


@pytest.mark.parametrize(
    "value",
    [
        ["nous|", "Engine"],
        "Engine",
        None,
        {"nous|": 3},
        {"nous|": None},
        {"nous|": ["Engine"]},
        {"nous|": "x" * (CARD_LABEL_MAX_LEN + 1)},
        {"no-separator": "Engine"},
        {"|https://x/v1": "Engine"},
    ],
)
def test_illegal_shapes_rejected(value: Any) -> None:
    with pytest.raises(SettingValidationError) as info:
        validate_setting_value(KEY, value)
    assert info.value.key == KEY


def _auth() -> SimpleNamespace:
    return SimpleNamespace(user_id="00000000-0000-0000-0000-000000000001")


def _repo(stored: Any) -> SimpleNamespace:
    return SimpleNamespace(
        get_value=AsyncMock(return_value=stored),
        upsert_setting=AsyncMock(return_value={}),
    )


@pytest.mark.asyncio
async def test_get_returns_empty_when_unset() -> None:
    repo = _repo(None)
    with patch.object(router_mod, "get_system_settings_repository", return_value=repo):
        resp = await router_mod.get_card_labels(auth=_auth())
    assert resp.labels == {}


@pytest.mark.asyncio
async def test_get_reads_corrupt_row_as_no_names() -> None:
    repo = _repo("not an object")
    with patch.object(router_mod, "get_system_settings_repository", return_value=repo):
        resp = await router_mod.get_card_labels(auth=_auth())
    assert resp.labels == {}


@pytest.mark.asyncio
async def test_put_merges_and_blank_removes() -> None:
    repo = _repo({"nous|": "Engine", "qwen|": "Qwen Team"})
    body = CardLabelsUpdate(labels={"qwen|": "", "ark|": " Ark Prod "})
    with (
        patch.object(router_mod, "get_system_settings_repository", return_value=repo),
        patch.object(router_mod, "create_audit_log", new=AsyncMock()),
    ):
        resp = await router_mod.update_card_labels(body=body, auth=_auth())
    expected = {"nous|": "Engine", "ark|": "Ark Prod"}
    assert resp.labels == expected
    repo.upsert_setting.assert_awaited_once_with(KEY, expected, _auth().user_id)


@pytest.mark.parametrize(
    "labels",
    [
        {"nous|": 3},
        {"nous|": "x" * (CARD_LABEL_MAX_LEN + 1)},
        {"no-separator": "Engine"},
    ],
)
@pytest.mark.asyncio
async def test_put_rejects_bad_shape_with_422_and_writes_nothing(
    labels: dict[str, Any],
) -> None:
    repo = _repo({})
    with (
        patch.object(router_mod, "get_system_settings_repository", return_value=repo),
        patch.object(router_mod, "create_audit_log", new=AsyncMock()),
        pytest.raises(HTTPException) as info,
    ):
        await router_mod.update_card_labels(
            body=CardLabelsUpdate(labels=labels), auth=_auth()
        )
    assert info.value.status_code == 422
    assert info.value.detail["code"] == "setting_invalid"
    repo.upsert_setting.assert_not_awaited()


def test_put_body_must_be_an_object() -> None:
    from pydantic import ValidationError

    with pytest.raises(ValidationError):
        CardLabelsUpdate(labels=["nous|"])  # type: ignore[arg-type]


def test_card_label_routes_are_declared_before_model_id_routes() -> None:
    """``PUT /{model_id}`` would swallow ``PUT /card-labels`` if declared first."""
    paths = [(r.path, sorted(r.methods)) for r in router_mod.router.routes]
    put_paths = [p for p, m in paths if "PUT" in m]
    assert put_paths.index("/card-labels") < put_paths.index("/{model_id}")
