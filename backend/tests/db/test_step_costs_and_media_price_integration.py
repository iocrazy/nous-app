"""DB-backed integration tests for 3b Task 4's two new ORM statements.

WHY THIS FILE EXISTS
────────────────────
``step_costs.load_step_shares`` and ``media_price.media_price_cents`` are pure
ORM statements whose unit tests hand a **stubbed session** back a hand-built
list of dicts. That pins the folding arithmetic and nothing else — until this
file landed, Postgres had never executed either statement. Specifically, none
of the following can be settled by a stub:

  * ``payload`` is **JSONB**. The unit stub hands back a Python dict; asyncpg /
    SQLAlchemy hand back whatever the driver decodes, and ``payload["cost_cents"]``
    being a usable number on the way out is a round-trip fact, not a given.
  * ``turn`` / ``step`` are **nullable** (mig 453 added them; pre-453 rows have
    NULL). ``_key`` skipping those rows has to survive real NULLs, not the
    absence of a key in a dict.
  * ``TE.run_id.in_([...])`` on a BIGINT column must actually partition — a
    second run's ``step_end`` leaking in would silently halve every share.
  * ``per_call_cents`` is ``NUMERIC(12,4)`` → a **Decimal** out of the driver.
    ``float(value)`` is the only reason the endpoint can JSON-encode it, and
    the unit stub supplies a float, so it never exercises the conversion.
  * ``ORDER BY effective_at DESC LIMIT 1`` + ``per_call_cents IS NOT NULL``:
    the two predicates together are the whole contract ("the newest row that
    actually carries a per-call price"). A stub returns whatever it was told to
    and cannot say which row Postgres would have picked.
  * the ``UNIQUE (model, provider, effective_at)`` on ``ai_model_prices`` is
    what makes "two prices for one model" expressible at all.

Transport: asyncpg on ``INTEGRATION_DATABASE_URL`` for fixture setup; the two
modules under test go through ``app.db.session`` (SQLAlchemy async engine),
which the ``orm_dsn`` fixture repoints at the same DSN. Same pattern as
``tests/db/test_run_deliverables_repository_integration.py``.

Point it at any CI-way Postgres (ci_bootstrap.sql → schema_baseline.sql →
migrations above the watermark, which includes 397/453/466):

  INTEGRATION_DATABASE_URL=postgresql://postgres:postgres@127.0.0.1:5432/drift \\
    uv run pytest tests/db/test_step_costs_and_media_price_integration.py -v

Skips cleanly when INTEGRATION_DATABASE_URL is unset. Every case builds its own
fixture rows with fresh ids and tears them down in a ``finally``, so the cases
are independent and the file is re-runnable against the same DB.
"""

from __future__ import annotations

import os
import uuid
from datetime import datetime
from decimal import Decimal
from typing import Any, Dict

import asyncpg
import pytest

pytestmark = [pytest.mark.integration, pytest.mark.asyncio]

_TEST_DSN = os.environ.get("INTEGRATION_DATABASE_URL", "").strip()

pytest.importorskip("asyncpg")

_skip = pytest.mark.skipif(
    not _TEST_DSN,
    reason=(
        "INTEGRATION_DATABASE_URL not set — step_costs / media_price "
        "integration tests need a DB."
    ),
)


def _uniq(prefix: str) -> str:
    """A model name no other case can collide with — ``ai_model_prices`` has a
    UNIQUE (model, provider, effective_at) and the table is global."""
    return f"{prefix}-{uuid.uuid4().hex[:12]}"


@pytest.fixture
async def orm_dsn():
    """Repoint the ORM engine (app.db.session read scope) at the test DSN for
    the duration of a test, then restore + dispose so no other test inherits a
    stray engine."""
    from app.core.config import settings
    from app.db import engine as engine_mod
    from app.db import session as session_mod

    old = settings.SUPAVISOR_DATABASE_URL
    settings.SUPAVISOR_DATABASE_URL = _TEST_DSN
    await engine_mod.dispose_engine()
    session_mod.dispose_sessionmaker()
    try:
        yield _TEST_DSN
    finally:
        await engine_mod.dispose_engine()
        session_mod.dispose_sessionmaker()
        settings.SUPAVISOR_DATABASE_URL = old


@pytest.fixture
async def pg():
    """A raw asyncpg connection for setup/assertions (plain libpq DSN)."""
    conn = await asyncpg.connect(_TEST_DSN)
    try:
        yield conn
    finally:
        await conn.close()


@pytest.fixture
async def fx(pg) -> Dict[str, Any]:
    """Two ``agent_runs`` rows.

    ``agent_run_transcript_events.run_id`` FK-references ``public.agent_runs``
    (ON DELETE CASCADE), so the teardown of the runs takes every event with it.
    The SECOND run is not decoration: it is what proves ``run_id IN (...)``
    really partitions — a leaking ``step_end`` from a neighbouring run would
    halve a share without any test noticing.
    """
    user_id = uuid.uuid4()
    agent_id = await pg.fetchval(
        "INSERT INTO public.ai_agents (name) VALUES ($1) RETURNING id",
        _uniq("stepcost-test-agent"),
    )

    async def _mk_run() -> int:
        return await pg.fetchval(
            """
            INSERT INTO public.agent_runs (agent_id, user_id, status, trigger)
            VALUES ($1, $2, 'completed', 'test')
            RETURNING id
            """,
            agent_id,
            user_id,
        )

    run_a = await _mk_run()
    run_b = await _mk_run()
    try:
        yield {"run_a": run_a, "run_b": run_b}
    finally:
        await pg.execute(
            "DELETE FROM public.agent_runs WHERE id = ANY($1::bigint[])",
            [run_a, run_b],
        )
        await pg.execute("DELETE FROM public.ai_agents WHERE id = $1", agent_id)


async def _event(pg, run_id, seq, event_type, payload, *, turn=1, step=1) -> None:
    """One transcript row, written the way ``RunRecorder`` writes it: the
    payload is a JSONB document, and ``(run_id, seq)`` is UNIQUE."""
    import json

    await pg.execute(
        """
        INSERT INTO public.agent_run_transcript_events
            (run_id, seq, event_type, payload, turn, step)
        VALUES ($1, $2, $3, $4::jsonb, $5, $6)
        """,
        run_id,
        seq,
        event_type,
        json.dumps(payload),
        turn,
        step,
    )


def _at(day: str) -> datetime:
    """``effective_at`` as a real aware datetime.

    asyncpg binds by INFERRED parameter type, so a ``'2026-01-01Z'`` string
    with a ``::timestamptz`` cast in the SQL is still rejected at bind time —
    the cast applies to the already-bound value. Same family as the
    ``origin_run_id`` int-vs-str lesson: the driver, not the SQL text, decides
    what a parameter may be."""
    return datetime.fromisoformat(f"{day}T00:00:00+00:00")


async def _price(pg, model, provider, *, per_call, effective_at) -> None:
    """One catalog price row. ``per_call`` is ``NUMERIC(12,4)`` — asyncpg wants
    a ``Decimal`` for it (a float is rejected), which is exactly why the read
    side has to convert back to float."""
    await pg.execute(
        """
        INSERT INTO public.ai_model_prices
            (model, provider, prompt_cents_per_1k, completion_cents_per_1k,
             per_call_cents, effective_at)
        VALUES ($1, $2, 0, 0, $3, $4)
        """,
        model,
        provider,
        None if per_call is None else Decimal(str(per_call)),
        effective_at,
    )


# ---------------------------------------------------------------------------
# load_step_shares — the read-time fold over agent_run_transcript_events
# ---------------------------------------------------------------------------


@_skip
async def test_a_step_with_two_deliverables_splits_its_cost_in_half(orm_dsn, fx, pg):
    """spec §5 验收⑥ on real rows: one step, 0.18 分, two产出 → 0.09 each.

    The numerator comes out of a JSONB document and the denominator out of a
    COUNT of sibling rows; both halves of that arithmetic cross the driver here
    for the first time.
    """
    from app.services.deliverables.step_costs import load_step_shares

    run = fx["run_a"]
    await _event(pg, run, 1, "step_end", {"cost_cents": 0.18}, step=3)
    await _event(
        pg, run, 2, "deliverable", {"kind": "script_shot", "ref_id": "9"}, step=3
    )
    await _event(
        pg, run, 3, "deliverable", {"kind": "script_shot", "ref_id": "10"}, step=3
    )
    # A second step of the SAME run, one产出 — proves the key is per-step, not
    # per-run (a per-run fold would smear 0.58 across three deliverables).
    await _event(pg, run, 4, "step_end", {"cost_cents": 0.4}, step=4)
    await _event(
        pg, run, 5, "deliverable", {"kind": "script_shot", "ref_id": "9"}, step=4
    )

    shares = await load_step_shares([run])

    assert shares == {(run, 1, 3): 0.09, (run, 1, 4): 0.4}
    assert all(isinstance(v, float) for v in shares.values())


@_skip
async def test_a_step_with_no_step_end_yet_has_no_share(orm_dsn, fx, pg):
    """回合还在跑（step_end 没落成）→ 这一步不进表。缺席是「不知道」，而
    ``allocate_step_costs`` 把缺席读成 ``cost_cents=None``，不是 0。"""
    from app.services.deliverables.step_costs import load_step_shares

    run = fx["run_a"]
    await _event(
        pg, run, 1, "deliverable", {"kind": "script_shot", "ref_id": "9"}, step=3
    )
    await _event(
        pg, run, 2, "deliverable", {"kind": "script_shot", "ref_id": "10"}, step=3
    )

    assert await load_step_shares([run]) == {}


@_skip
async def test_a_step_that_produced_nothing_is_not_a_share(orm_dsn, fx, pg):
    """分母为 0 的步（纯思考/纯工具调用的一步）不进表 —— 除零与「凭空多出
    一整份」都是错的答案。"""
    from app.services.deliverables.step_costs import load_step_shares

    run = fx["run_a"]
    await _event(pg, run, 1, "step_end", {"cost_cents": 0.18}, step=3)

    assert await load_step_shares([run]) == {}


@_skip
async def test_another_runs_events_never_leak_into_this_runs_shares(orm_dsn, fx, pg):
    """``run_id IN (...)`` 必须真的分区：邻居 run 的一条 deliverable 漏进来就会
    把份额悄悄减半，而算术本身仍然「正确」。"""
    from app.services.deliverables.step_costs import load_step_shares

    a, b = fx["run_a"], fx["run_b"]
    await _event(pg, a, 1, "step_end", {"cost_cents": 0.18}, step=3)
    await _event(
        pg, a, 2, "deliverable", {"kind": "script_shot", "ref_id": "9"}, step=3
    )
    # Same (turn, step) coordinates on the OTHER run — only the run id differs.
    await _event(pg, b, 1, "step_end", {"cost_cents": 99.0}, step=3)
    await _event(
        pg, b, 2, "deliverable", {"kind": "script_shot", "ref_id": "x"}, step=3
    )
    await _event(
        pg, b, 3, "deliverable", {"kind": "script_shot", "ref_id": "y"}, step=3
    )

    assert await load_step_shares([a]) == {(a, 1, 3): 0.18}
    # Both at once: two independent keys, no cross-contamination.
    assert await load_step_shares([a, b]) == {(a, 1, 3): 0.18, (b, 1, 3): 49.5}


@_skip
async def test_rows_with_null_coordinates_are_skipped_not_crashed(orm_dsn, fx, pg):
    """``turn`` / ``step`` are nullable — every event written before mig 453 has
    NULLs. A real NULL is not the same thing as a dict missing a key, which is
    all the unit stub can produce."""
    from app.services.deliverables.step_costs import load_step_shares

    run = fx["run_a"]
    await _event(pg, run, 1, "step_end", {"cost_cents": 5.0}, turn=None, step=None)
    await _event(
        pg,
        run,
        2,
        "deliverable",
        {"kind": "script_shot", "ref_id": "9"},
        turn=None,
        step=None,
    )
    await _event(pg, run, 3, "step_end", {"cost_cents": 0.2}, step=7)
    await _event(
        pg, run, 4, "deliverable", {"kind": "script_shot", "ref_id": "9"}, step=7
    )

    assert await load_step_shares([run]) == {(run, 1, 7): 0.2}


@_skip
async def test_an_event_type_we_do_not_fold_is_not_read(orm_dsn, fx, pg):
    """只读 ``step_end`` 与 ``deliverable`` 两类。别的事件（这里是
    ``tool_call``）既不占分母也不贡献分子。"""
    from app.services.deliverables.step_costs import load_step_shares

    run = fx["run_a"]
    await _event(pg, run, 1, "step_end", {"cost_cents": 0.18}, step=3)
    await _event(
        pg, run, 2, "deliverable", {"kind": "script_shot", "ref_id": "9"}, step=3
    )
    await _event(pg, run, 3, "tool_call", {"cost_cents": 77.0, "name": "Write"}, step=3)

    assert await load_step_shares([run]) == {(run, 1, 3): 0.18}


@_skip
async def test_a_run_with_no_events_is_simply_absent(orm_dsn, fx):
    from app.services.deliverables.step_costs import load_step_shares

    assert await load_step_shares([fx["run_a"], fx["run_b"]]) == {}


# ---------------------------------------------------------------------------
# media_price_cents — the per-call catalog price
# ---------------------------------------------------------------------------


@_skip
async def test_the_newest_effective_price_wins_and_arrives_as_a_float(orm_dsn, pg):
    """``NUMERIC(12,4)`` is a ``Decimal`` out of the driver — the endpoint
    cannot JSON-encode that, so the float conversion is load-bearing."""
    from app.services.deliverables.media_price import media_price_cents

    model, provider = _uniq("seedream"), "ark"
    try:
        await _price(pg, model, provider, per_call=1.5, effective_at=_at("2026-01-01"))
        await _price(pg, model, provider, per_call=12.0, effective_at=_at("2026-09-01"))
        await _price(pg, model, provider, per_call=9.0, effective_at=_at("2025-06-01"))

        price = await media_price_cents(model, provider)

        assert price == 12.0 and isinstance(price, float)
    finally:
        await pg.execute("DELETE FROM public.ai_model_prices WHERE model = $1", model)


@_skip
async def test_four_decimals_survive_the_round_trip(orm_dsn, pg):
    """``NUMERIC(12,4)`` 的最后一位有效数字是真的（mig 466 选这个精度就是为了
    分级出图的零点几分钱）。"""
    from app.services.deliverables.media_price import media_price_cents

    model = _uniq("cheap")
    try:
        await _price(pg, model, "ark", per_call=0.0125, effective_at=_at("2026-01-01"))
        assert await media_price_cents(model, "ark") == pytest.approx(0.0125)
    finally:
        await pg.execute("DELETE FROM public.ai_model_prices WHERE model = $1", model)


@_skip
async def test_a_newer_row_without_a_per_call_price_does_not_hide_the_priced_one(
    orm_dsn, pg
):
    """价目表同时伺候两个面：一行可以只有每千 token 价（``per_call_cents``
    NULL）。如果只按 ``effective_at`` 取最新，一次纯 token 的调价就会让这个模型
    的**每次调用价凭空消失**，生成的图在血缘里退回 '—' 而没有任何人会察觉。
    所以语句里的 ``per_call_cents IS NOT NULL`` 与 admin 那个覆盖率查询用的是
    同一条谓词——「有价」在两处必须是同一件事。"""
    from app.services.deliverables.media_price import media_price_cents

    model = _uniq("seedream")
    try:
        await _price(pg, model, "ark", per_call=12.0, effective_at=_at("2026-01-01"))
        # 更新的一行，只调了 token 价。
        await _price(pg, model, "ark", per_call=None, effective_at=_at("2026-09-01"))

        assert await media_price_cents(model, "ark") == 12.0
    finally:
        await pg.execute("DELETE FROM public.ai_model_prices WHERE model = $1", model)


@_skip
async def test_a_model_with_only_token_prices_is_none_not_zero(orm_dsn, pg):
    """没配每次调用价 ≠ 免费。0.0 会谎称这次生成不要钱。"""
    from app.services.deliverables.media_price import media_price_cents

    model = _uniq("llm-only")
    try:
        await _price(pg, model, "ark", per_call=None, effective_at=_at("2026-01-01"))
        assert await media_price_cents(model, "ark") is None
    finally:
        await pg.execute("DELETE FROM public.ai_model_prices WHERE model = $1", model)


@_skip
async def test_the_provider_is_part_of_the_key(orm_dsn, pg):
    """同一个模型名可以挂两个 provider（订阅行与 API-key 行），价完全不同。
    provider 是协议规范键，查表按字面匹配、不做别名回退。"""
    from app.services.deliverables.media_price import media_price_cents

    model = _uniq("dual")
    try:
        await _price(pg, model, "ark", per_call=12.0, effective_at=_at("2026-01-01"))
        await _price(
            pg, model, "jimeng-cli", per_call=3.0, effective_at=_at("2026-01-01")
        )

        assert await media_price_cents(model, "ark") == 12.0
        assert await media_price_cents(model, "jimeng-cli") == 3.0
        # 别名不是键：存成 jimeng-cli 的价，问 jimeng 查不到。
        assert await media_price_cents(model, "jimeng") is None
    finally:
        await pg.execute("DELETE FROM public.ai_model_prices WHERE model = $1", model)


@_skip
async def test_a_model_that_is_not_in_the_catalog_is_none(orm_dsn):
    from app.services.deliverables.media_price import media_price_cents

    assert await media_price_cents(_uniq("nobody"), "ark") is None
