# backend/tests/test_model_capabilities.py
import decimal
from contextlib import asynccontextmanager
from unittest.mock import AsyncMock

import pytest
from sqlalchemy import insert as _sa_insert
from sqlalchemy.dialects import postgresql
from sqlalchemy.ext.asyncio import AsyncSession as _AsyncSession
from sqlalchemy.ext.asyncio import async_sessionmaker as _async_sessionmaker
from sqlalchemy.ext.asyncio import create_async_engine as _create_async_engine

from app.models import AiModelPrices
from app.services.ai import model_capabilities as m


@pytest.fixture(autouse=True)
def _reset_cache():
    """Each test gets a fresh cache so previous state can't leak in."""
    m._cache.clear()
    m._local_vision_models.clear()
    m._cache_loaded = False
    yield
    m._cache.clear()
    m._local_vision_models.clear()
    m._cache_loaded = False


@pytest.mark.asyncio
async def test_returns_true_for_seeded_vision_model(monkeypatch):
    """When the DB row says supports_vision=True, return True."""
    monkeypatch.setattr(
        m,
        "_fetch_capabilities",
        AsyncMock(
            return_value=[
                {
                    "model": "doubao-seed-2-0-pro-260215",
                    "provider": "doubao",
                    "supports_vision": True,
                },
                {"model": "doubao-pro", "provider": "doubao", "supports_vision": True},
                {"model": "qwen-plus", "provider": "qwen", "supports_vision": False},
            ]
        ),
    )
    assert await m.model_supports_vision("doubao-seed-2-0-pro-260215", "doubao") is True
    assert await m.model_supports_vision("doubao-pro", "doubao") is True
    assert await m.model_supports_vision("qwen-plus", "qwen") is False


@pytest.mark.asyncio
async def test_db_lookup_ignores_provider_when_unique(monkeypatch):
    """If only one provider has the model, provider can be omitted."""
    monkeypatch.setattr(
        m,
        "_fetch_capabilities",
        AsyncMock(
            return_value=[
                {"model": "gpt-4o", "provider": "openai", "supports_vision": True},
            ]
        ),
    )
    assert await m.model_supports_vision("gpt-4o") is True
    assert await m.model_supports_vision("gpt-4o", "openai") is True


@pytest.mark.asyncio
async def test_falls_back_to_prefix_heuristic_for_unknown_model(monkeypatch):
    """A model not in the DB falls back to the legacy prefix check."""
    monkeypatch.setattr(m, "_fetch_capabilities", AsyncMock(return_value=[]))
    # claude-3 prefix → True via heuristic
    assert await m.model_supports_vision("claude-3-haiku-20240307", "anthropic") is True
    # Unknown plain text model → False
    assert await m.model_supports_vision("totally-made-up-model") is False


@pytest.mark.asyncio
async def test_cache_loads_once(monkeypatch):
    """The DB fetch happens once across multiple calls (cache hit subsequent)."""
    fetch = AsyncMock(
        return_value=[
            {"model": "gpt-4o", "provider": "openai", "supports_vision": True},
        ]
    )
    monkeypatch.setattr(m, "_fetch_capabilities", fetch)
    for _ in range(5):
        await m.model_supports_vision("gpt-4o", "openai")
    assert fetch.await_count == 1


@pytest.mark.asyncio
async def test_db_fetch_error_falls_back_to_heuristic(monkeypatch):
    """If the DB lookup raises, the helper degrades to prefix heuristic, never throws."""
    monkeypatch.setattr(
        m, "_fetch_capabilities", AsyncMock(side_effect=RuntimeError("db down"))
    )
    # gpt-4o matches prefix heuristic → True
    assert await m.model_supports_vision("gpt-4o", "openai") is True
    # Unknown model → False
    assert await m.model_supports_vision("nope") is False


@pytest.mark.asyncio
async def test_empty_or_none_model_returns_false():
    assert await m.model_supports_vision("") is False
    assert await m.model_supports_vision(None) is False  # type: ignore[arg-type]


@pytest.mark.asyncio
async def test_db_row_with_supports_vision_false_overrides_prefix_match(monkeypatch):
    """If a row exists with supports_vision=False, it WINS over the prefix heuristic.
    Admin can opt OUT a model that happens to match a vision prefix."""
    monkeypatch.setattr(
        m,
        "_fetch_capabilities",
        AsyncMock(
            return_value=[
                {
                    "model": "qwen-vl-broken",
                    "provider": "qwen",
                    "supports_vision": False,
                },
            ]
        ),
    )
    # 'qwen-vl' prefix would say True via heuristic, but DB row wins.
    assert await m.model_supports_vision("qwen-vl-broken", "qwen") is False


@pytest.mark.asyncio
async def test_fetch_capabilities_uses_distinct_on_model_provider(monkeypatch):
    """Phase B5 Task 1: _fetch_capabilities itself (not mocked away) — the
    real ORM statement must compile to DISTINCT ON (model, provider) ordered
    by effective_at DESC (picks the latest row per pair), and consumers
    (_ensure_loaded) read row['model']/['provider']/['supports_vision'] via
    a column-level select+.mappings(), never an entity-level select()."""
    row = {"model": "gpt-4o", "provider": "openai", "supports_vision": True}

    class _FakeResult:
        def mappings(self):
            return self

        def all(self):
            return [row]

    captured = {}

    class _FakeSession:
        async def execute(self, stmt):
            captured["stmt"] = stmt
            return _FakeResult()

    @asynccontextmanager
    async def fake_read_scope():
        yield _FakeSession()

    import app.db.session as db_session

    # _fetch_capabilities does a function-local ``from app.db.session import
    # read_scope`` (deferred import, per-call) — patch the source attribute
    # so it resolves at call time, not a (nonexistent) module-level name.
    monkeypatch.setattr(db_session, "read_scope", fake_read_scope)

    rows = await m._fetch_capabilities()
    assert rows == [row]

    sql = str(captured["stmt"].compile(dialect=postgresql.dialect()))
    assert "SELECT DISTINCT ON (public.ai_model_prices.model, " in sql
    assert "public.ai_model_prices.provider) " in sql
    assert (
        "ORDER BY public.ai_model_prices.model, public.ai_model_prices.provider, "
        "public.ai_model_prices.effective_at DESC" in sql
    )


@pytest.mark.asyncio
async def test_refresh_reloads_cache(monkeypatch):
    """refresh_capabilities() forces a fresh DB fetch."""
    fetch = AsyncMock(
        side_effect=[
            [{"model": "gpt-4o", "provider": "openai", "supports_vision": True}],
            [{"model": "gpt-4o", "provider": "openai", "supports_vision": False}],
        ]
    )
    monkeypatch.setattr(m, "_fetch_capabilities", fetch)
    assert await m.model_supports_vision("gpt-4o", "openai") is True
    await m.refresh_capabilities()
    assert await m.model_supports_vision("gpt-4o", "openai") is False
    assert fetch.await_count == 2


# ── Real-aiosqlite row-shape regression (B5 review leftover — deferred
# minors batch, Minor 4) ─────────────────────────────────────────────────
#
# test_fetch_capabilities_uses_distinct_on_model_provider above only ever
# checks the COMPILED SQL text against a hand-rolled dict "row" — never a
# genuine materialized Result — so the B4 row-shape bug class (an
# entity-level select producing one key per row instead of one per column)
# has no coverage here. This statement already selects three individually-
# named columns (no select(Entity)/select(*Entity.__table__.c) ambiguity is
# even possible), so — mirroring test_orm_b5_task2_row_shape_e2e.py's
# treatment of its own no-ambiguity sites — this gets a POSITIVE real-engine
# round trip proving the DISTINCT + column-shape mechanics actually
# materialize as expected, not a negative control.
#
# DISTINCT ON (model, provider) is Postgres-only syntax; SQLAlchemy silently
# degrades ``.distinct(col1, col2)`` to a plain ``DISTINCT`` over the
# selected columns on other dialects (verified: compiles clean against the
# sqlite dialect, no CompileError) — irrelevant to what this test checks
# (row SHAPE, not "latest per pair" semantics, which the compile-level test
# above already pins against the postgresql dialect).

_AI_MODEL_PRICES_DDL = """
CREATE TABLE ai_model_prices (
    id TEXT PRIMARY KEY, model TEXT NOT NULL, provider TEXT NOT NULL,
    prompt_cents_per_1k NUMERIC, completion_cents_per_1k NUMERIC,
    effective_at TIMESTAMP, created_at TIMESTAMP, supports_vision INTEGER,
    cached_input_cents_per_1k NUMERIC
)
"""


@pytest.mark.asyncio
async def test_capabilities_select_stmt_yields_column_keyed_row_against_real_sqlite():
    """The REAL production statement (``_capabilities_select_stmt``, imported
    — not reconstructed here) round-tripped through a genuine aiosqlite
    ``Result`` gives a column-keyed RowMapping matching ``_fetch_capabilities``'s
    ``dict(r)`` consumption and ``_ensure_loaded``'s ``row.get("model")``/
    ``row.get("provider")``/``row.get("supports_vision")`` reads."""
    engine = _create_async_engine("sqlite+aiosqlite://")
    engine = engine.execution_options(schema_translate_map={"public": None})
    async with engine.begin() as conn:
        await conn.exec_driver_sql(_AI_MODEL_PRICES_DDL)
        await conn.execute(
            _sa_insert(AiModelPrices.__table__).values(
                model="gpt-4o",
                provider="openai",
                prompt_cents_per_1k=decimal.Decimal("0.5"),
                completion_cents_per_1k=decimal.Decimal("1.5"),
                supports_vision=True,
            )
        )

    sessionmaker = _async_sessionmaker(
        engine, class_=_AsyncSession, expire_on_commit=False
    )
    try:
        async with sessionmaker() as session:
            rows = (
                (await session.execute(m._capabilities_select_stmt())).mappings().all()
            )
    finally:
        await engine.dispose()

    assert len(rows) == 1
    row = dict(rows[0])  # exact consumption shape used by _fetch_capabilities
    assert row["model"] == "gpt-4o"
    assert row["provider"] == "openai"
    assert bool(row["supports_vision"]) is True


# ── codex-local: a real vision model with no ai_model_prices row ──────────
#
# 真链验收第 4 项: "Codex (Local)" 有 ai_model_prices 行、也不匹配任何
# _FALLBACK_PREFIXES,于是 model_supports_vision 返回 False,图片在进 adapter
# 之前就被 build_user_message 摊成文字占位符 —— 而 codex exec --image 是真实
# 存在的能力。返回 False 在这里不是"安全降级",是静默丢用户的图。


@pytest.mark.asyncio
async def test_local_catalog_model_supports_vision(monkeypatch):
    monkeypatch.setattr(m, "_fetch_capabilities", AsyncMock(return_value=[]))
    monkeypatch.setattr(
        m, "_fetch_local_vision_models", AsyncMock(return_value={"codex (local)"})
    )
    assert await m.model_supports_vision("Codex (Local)") is True


@pytest.mark.asyncio
async def test_unknown_model_still_false(monkeypatch):
    """反向对照 —— 没有它,一个"永远返回 True"的实现同样会绿。"""
    monkeypatch.setattr(m, "_fetch_capabilities", AsyncMock(return_value=[]))
    monkeypatch.setattr(
        m, "_fetch_local_vision_models", AsyncMock(return_value={"codex (local)"})
    )
    assert await m.model_supports_vision("some-unknown-model") is False
    assert await m.model_supports_vision("") is False
    assert await m.model_supports_vision(None) is False


@pytest.mark.asyncio
async def test_explicit_price_row_still_wins_over_local_provider(monkeypatch):
    """管理员用 ai_model_prices 行显式 supports_vision=FALSE 关掉某个模型的视觉,
    这条约定(模块 docstring 写着的)不能被本机分支绕过 —— 所以本机判定放在显式
    查表之后、前缀启发式之前。"""
    monkeypatch.setattr(
        m,
        "_fetch_capabilities",
        AsyncMock(
            return_value=[
                {
                    "model": "codex (local)",
                    "provider": "",
                    "supports_vision": False,
                }
            ]
        ),
    )
    monkeypatch.setattr(
        m, "_fetch_local_vision_models", AsyncMock(return_value={"codex (local)"})
    )
    assert await m.model_supports_vision("Codex (Local)") is False


@pytest.mark.asyncio
async def test_local_vision_load_failure_degrades_without_killing_price_cache(
    monkeypatch,
):
    """两次加载各自 try —— 目录查询炸了不能顺手把已经加载好的能力表也丢掉。"""
    monkeypatch.setattr(
        m,
        "_fetch_capabilities",
        AsyncMock(
            return_value=[
                {"model": "gpt-4o", "provider": "openai", "supports_vision": True}
            ]
        ),
    )
    monkeypatch.setattr(
        m,
        "_fetch_local_vision_models",
        AsyncMock(side_effect=RuntimeError("catalog down")),
    )
    assert await m.model_supports_vision("gpt-4o", "openai") is True
    assert await m.model_supports_vision("Codex (Local)") is False


def test_local_vision_providers_excludes_image_generators():
    """jimeng-local 是出图引擎不是聊天模型,不该出现在"接受图片输入"的集合里。"""
    assert m._LOCAL_VISION_PROVIDERS == frozenset({"codex-local"})
