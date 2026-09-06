"""model_pricing_coverage: the per-row 'has a price row?' guard behind mig 454.

Pins (1) the pure classifier, (2) that a failed price-table read reports
``unknown`` rather than a verdict, (3) that the admin list endpoint carries the
field and (4) that the ORM statement selects a single column.
"""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from sqlalchemy.dialects import postgresql

from app.services.ai.model_pricing_coverage import (
    PRICE_COVERAGE,
    _priced_models_select_stmt,
    load_priced_models,
    price_coverage_for,
)

PRICED = {"doubao-seed-2-0-lite-260428", "nous-qwen3-llm"}


def _row(**over):
    base = {
        "id": 1,
        "name": "mediahub-doubao-seed-2-0-lite",
        "type": "llm",
        "actual_provider": "doubao",
        "actual_model": "doubao-seed-2-0-lite-260428",
        "is_enabled": True,
    }
    base.update(over)
    return base


def test_priced_by_actual_model():
    assert price_coverage_for(_row(), PRICED) == "priced"


def test_priced_by_catalog_name_when_runs_record_the_name():
    # The self-hosted OpenAI-compatible path records model=<catalog name>.
    row = _row(
        name="nous-qwen3-llm", actual_provider="openai", actual_model="qwen3-6-35b"
    )
    assert price_coverage_for(row, PRICED) == "priced"


def test_missing_when_neither_key_has_a_row():
    row = _row(
        name="mediahub-deepseek-v4-pro",
        actual_provider="deepseek",
        actual_model="deepseek-v4-pro",
    )
    assert price_coverage_for(row, PRICED) == "missing"


def test_disabled_rows_are_still_classified():
    # A disabled model can be re-enabled with one click; the tag must already be there.
    row = _row(actual_model="deepseek-v4-pro", is_enabled=False)
    assert price_coverage_for(row, PRICED) == "missing"


@pytest.mark.parametrize("mtype", ["image", "video", "tts", "asr", "embedding"])
def test_non_llm_types_are_not_applicable(mtype):
    assert (
        price_coverage_for(_row(type=mtype, actual_model="whatever"), PRICED)
        == "not_applicable"
    )


@pytest.mark.parametrize("prov", ["codex-local", "jimeng-local"])
def test_local_engine_providers_are_not_applicable(prov):
    assert (
        price_coverage_for(_row(actual_provider=prov, actual_model=""), PRICED)
        == "not_applicable"
    )


def test_unknown_when_price_table_could_not_be_read():
    # None = the lookup failed. A verdict here would be a self-concealing failure.
    assert price_coverage_for(_row(actual_model="deepseek-v4-pro"), None) == "unknown"
    # not_applicable still wins: no lookup is needed to know an image model is out of scope.
    assert price_coverage_for(_row(type="image"), None) == "not_applicable"


def test_every_answer_is_in_the_closed_enum():
    rows = [
        _row(),
        _row(actual_model="x"),
        _row(type="image"),
        _row(actual_provider="codex-local"),
    ]
    for r in rows:
        assert price_coverage_for(r, PRICED) in PRICE_COVERAGE
        assert price_coverage_for(r, None) in PRICE_COVERAGE


def test_select_stmt_is_single_column_distinct():
    sql = str(_priced_models_select_stmt().compile(dialect=postgresql.dialect()))
    assert sql.startswith("SELECT DISTINCT ")
    assert "ai_model_prices.model" in sql
    # one column: no provider (the recorder relaxes to model-only when the
    # run's provider is empty, so a provider match here would under-count)
    assert "provider" not in sql
    assert "cents" not in sql


@pytest.mark.asyncio
async def test_load_returns_none_when_read_fails():
    class _Boom:
        async def __aenter__(self):
            raise RuntimeError("db down")

        async def __aexit__(self, *a):
            return False

    with patch("app.db.session.read_scope", lambda: _Boom()):
        assert await load_priced_models() is None


@pytest.mark.asyncio
async def test_admin_list_carries_price_coverage_per_row():
    from app.api.admin.mediahub_model_router import list_mediahub_models

    rows = [
        _row(id=1),
        _row(
            id=2,
            name="mediahub-deepseek-v4-pro",
            actual_provider="deepseek",
            actual_model="deepseek-v4-pro",
        ),
        _row(
            id=3,
            name="img",
            type="image",
            actual_provider="jimeng",
            actual_model="jimeng-4",
        ),
    ]
    for r in rows:
        r.update(
            {
                "display_name": r["name"],
                "api_key": "sk-secret1234",
                "pricing_type": "per_request",
                "pricing_value": 0,
                "sort_order": 0,
            }
        )
    repo = MagicMock()
    repo.list_all = AsyncMock(return_value=rows)
    with (
        patch(
            "app.api.admin.mediahub_model_router.get_mediahub_model_repository",
            return_value=repo,
        ),
        patch(
            "app.api.admin.mediahub_model_router.load_priced_models",
            AsyncMock(return_value=PRICED),
        ),
    ):
        out = await list_mediahub_models(MagicMock())
    assert [o.price_coverage for o in out] == ["priced", "missing", "not_applicable"]
