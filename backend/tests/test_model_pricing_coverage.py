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
    _per_call_priced_models_select_stmt,
    _priced_models_select_stmt,
    load_priced_models,
    price_coverage_for,
)

# 两个价目面各自一套键（3b §3.2）：LLM 走 RunRecorder 的每千 token 价，
# image / video 走 ``ai_model_prices.per_call_cents``。同一个模型名在一个面上
# 有价、另一个面上没有，是正常状态——所以判定必须按 type 选集合，不能混成一堆。
PRICED = {
    "token": {"doubao-seed-2-0-lite-260428", "nous-qwen3-llm"},
    "per_call": {"doubao-seedream-4-0"},
}


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


@pytest.mark.parametrize("mtype", ["tts", "asr", "embedding"])
def test_types_billed_by_other_paths_are_not_applicable(mtype):
    assert (
        price_coverage_for(_row(type=mtype, actual_model="whatever"), PRICED)
        == "not_applicable"
    )


@pytest.mark.parametrize("mtype", ["image", "video"])
def test_a_media_model_with_a_per_call_price_is_priced(mtype):
    """3b §3.2：图/视频的花费来自 ``per_call_cents``，所以它们进判定面了——
    在此之前这两类永远是 ``not_applicable``，等于「没配价」在这一页不可见。"""
    row = _row(type=mtype, actual_provider="ark", actual_model="doubao-seedream-4-0")
    assert price_coverage_for(row, PRICED) == "priced"


@pytest.mark.parametrize("mtype", ["image", "video"])
def test_a_media_model_without_a_per_call_price_is_missing(mtype):
    """没有 per_call 行 = 这个模型生成的图在血缘与预算里都是 '—'。"""
    row = _row(type=mtype, actual_provider="jimeng-cli", actual_model="jimeng-4.0")
    assert price_coverage_for(row, PRICED) == "missing"


def test_the_two_price_faces_are_not_interchangeable():
    """LLM 只看 token 面、媒体只看 per_call 面。混用会让一个只配了每千 token
    价的模型在图片那一行谎称「已配价」。"""
    llm = _row(actual_model="doubao-seedream-4-0", actual_provider="ark")
    assert price_coverage_for(llm, PRICED) == "missing"
    img = _row(
        type="image",
        name="img",
        actual_provider="ark",
        actual_model="doubao-seed-2-0-lite-260428",
    )
    assert price_coverage_for(img, PRICED) == "missing"


@pytest.mark.parametrize("prov", ["codex-local", "jimeng-local"])
def test_local_engine_providers_are_not_applicable(prov):
    assert (
        price_coverage_for(_row(actual_provider=prov, actual_model=""), PRICED)
        == "not_applicable"
    )


def test_unknown_when_price_table_could_not_be_read():
    # None = the lookup failed. A verdict here would be a self-concealing failure.
    assert price_coverage_for(_row(actual_model="deepseek-v4-pro"), None) == "unknown"
    # 图片也一样：它已经在判定面里了（3b），读不到价目表就是「不知道」。
    assert price_coverage_for(_row(type="image"), None) == "unknown"
    # not_applicable still wins: no lookup is needed to know a tts model is out of scope.
    assert price_coverage_for(_row(type="tts"), None) == "not_applicable"


def test_every_answer_is_in_the_closed_enum():
    rows = [
        _row(),
        _row(actual_model="x"),
        _row(type="image"),
        _row(type="tts"),
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


def test_the_old_flat_set_shape_is_refused_loudly():
    """3b 把第二个参数从一个扁平 set 换成了 ``{"token", "per_call"}`` 两面。

    旧形状**必须炸**：``set`` 上的 ``.get(face)`` 不存在，若改成宽容取值就会
    静默退化成「每一行都 missing」——一个会教管理员忽略这个红标签的答案
    （2026-08-14 红灯教训）。陈旧调用方应当当场失败，不是安静地错。
    """
    with pytest.raises(TypeError):
        price_coverage_for(_row(), {"doubao-seed-2-0-lite-260428"})
    # 空 set 同样是旧形状，不能因为「反正是空的」就放过。
    with pytest.raises(TypeError):
        price_coverage_for(_row(), set())
    # 但 not_applicable 的行根本不看价目表，先返回、不校验形状。
    assert price_coverage_for(_row(type="tts"), {"x"}) == "not_applicable"


def test_the_per_call_stmt_is_single_column_distinct_and_filtered():
    sql = str(
        _per_call_priced_models_select_stmt().compile(dialect=postgresql.dialect())
    )
    assert sql.startswith("SELECT DISTINCT ")
    assert "ai_model_prices.model" in sql
    # 只要**配了每次调用价**的行。不加这个条件，每个有每千 token 价的 LLM 行都
    # 会让同名媒体模型谎称「已配价」。
    assert "per_call_cents IS NOT NULL" in sql


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
async def test_load_reads_both_faces_in_one_session():
    """两条语句一次 session：这一页每次列表只该付一次连接的钱。"""
    from contextlib import asynccontextmanager

    sessions: list[object] = []

    class _Session:
        def __init__(self):
            self.stmts: list[str] = []

        async def execute(self, stmt):
            sql = str(stmt.compile(dialect=postgresql.dialect()))
            self.stmts.append(sql)
            rows = (
                ["m-per-call"] if "per_call_cents IS NOT NULL" in sql else ["m-token"]
            )

            class _R:
                def scalars(self):
                    return self

                def all(self):
                    return rows

            return _R()

    @asynccontextmanager
    async def _scope():
        session = _Session()
        sessions.append(session)
        yield session

    with patch("app.db.session.read_scope", _scope):
        out = await load_priced_models()
    assert out == {"token": {"m-token"}, "per_call": {"m-per-call"}}
    assert len(sessions) == 1 and len(sessions[0].stmts) == 2


@pytest.mark.asyncio
async def test_admin_list_carries_price_coverage_per_row():
    from app.api.admin.nous_model_router import list_nous_models

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
            actual_provider="jimeng-cli",
            actual_model="jimeng-4.0",
        ),
        _row(
            id=4,
            name="img-priced",
            type="image",
            actual_provider="ark",
            actual_model="doubao-seedream-4-0",
        ),
        _row(id=5, name="tts", type="tts", actual_provider="volc", actual_model="t"),
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
            "app.api.admin.nous_model_router.get_nous_model_repository",
            return_value=repo,
        ),
        patch(
            "app.api.admin.nous_model_router.load_priced_models",
            AsyncMock(return_value=PRICED),
        ),
    ):
        out = await list_nous_models(MagicMock())
    assert [o.price_coverage for o in out] == [
        "priced",
        "missing",
        # 3b：没配 per_call 的图片模型现在**看得见**了，不再是永久 not_applicable。
        "missing",
        "priced",
        "not_applicable",
    ]
