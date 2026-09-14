"""媒体每次调用价：``ai_model_prices.per_call_cents`` 按 effective_at 取最新一行。"""

from contextlib import asynccontextmanager

import pytest

pytestmark = pytest.mark.unit


def _fake_read_scope(row):
    @asynccontextmanager
    async def _scope():
        class _R:
            def mappings(self):
                return self

            def first(self):
                return row

        class _Session:
            async def execute(self, stmt):
                return _R()

        yield _Session()

    return _scope


async def test_returns_the_newest_per_call_price(monkeypatch):
    import app.services.deliverables.media_price as mp

    monkeypatch.setattr(mp, "read_scope", _fake_read_scope({"per_call_cents": 12.0}))
    assert await mp.media_price_cents("doubao-seedream-4-0", "ark") == 12.0


@pytest.mark.parametrize("row", [None, {"per_call_cents": None}])
async def test_no_price_is_none_not_zero(monkeypatch, row):
    """没配价 ≠ 免费。None 让 UI 显 '—'，0.0 会谎称这次生成不要钱。
    第二种形状是 LLM 行：有每千 token 价，但没有每次调用价。"""
    import app.services.deliverables.media_price as mp

    monkeypatch.setattr(mp, "read_scope", _fake_read_scope(row))
    assert await mp.media_price_cents("m", "p") is None


async def test_a_failed_read_is_none_and_never_raises(monkeypatch):
    """查价失败不许连坐一次已经生成并付过钱的图。"""
    import app.services.deliverables.media_price as mp

    @asynccontextmanager
    async def _boom():
        raise RuntimeError("db down")
        yield

    monkeypatch.setattr(mp, "read_scope", _boom)
    assert await mp.media_price_cents("m", "p") is None


def _fake_catalog(rows):
    """按 ``(model, provider)`` 精确命中的价目表桩。

    断的是**发出去的语句**：编译后的参数就是那两个字面量，没有任何回退。
    """
    from sqlalchemy.dialects import postgresql

    @asynccontextmanager
    async def _scope():
        class _R:
            def __init__(self, stmt):
                params = dict(stmt.compile(dialect=postgresql.dialect()).params)
                self._key = (params.get("model_1"), params.get("provider_1"))

            def mappings(self):
                return self

            def first(self):
                return rows.get(self._key)

        class _Session:
            async def execute(self, stmt):
                return _R(stmt)

        yield _Session()

    return _scope


async def test_the_provider_spelling_is_taken_literally(monkeypatch):
    """provider 是协议规范键（Task 0 已在写入方归一）。查表**不**做别名回退：
    存成 ``jimeng-cli`` 的价只认 ``jimeng-cli``，问 ``jimeng`` 查不到——把这条
    写成断言，免得两侧各自猜一套别名表。"""
    import app.services.deliverables.media_price as mp

    monkeypatch.setattr(
        mp,
        "read_scope",
        _fake_catalog({("jimeng-4.0", "jimeng-cli"): {"per_call_cents": 3.0}}),
    )
    assert await mp.media_price_cents("jimeng-4.0", "jimeng-cli") == 3.0
    assert await mp.media_price_cents("jimeng-4.0", "jimeng") is None


async def test_an_empty_key_never_queries(monkeypatch):
    """归因缺一半时咽喉点就不该问——这里再钉一道，免得空串匹配到空 provider 行。"""
    import app.services.deliverables.media_price as mp

    @asynccontextmanager
    async def _boom():
        raise AssertionError("must not query")
        yield

    monkeypatch.setattr(mp, "read_scope", _boom)
    assert await mp.media_price_cents("", "ark") is None
    assert await mp.media_price_cents("m", "") is None
