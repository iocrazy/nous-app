"""Postgres connection-slot pressure: thresholds, logging, failure shape.

The point of this monitor is to make the 2026-08-21 near-miss visible before
it repeats: the Supabase stack's own fixed floor ate 60-70 of 100 slots, a
deploy window ran two pools at once, and 106/100 was reached — at which point
``psql`` could not connect either, so the diagnosis tooling died with the
service.

Everything here is DB-free. The query itself is stubbed; what is under test is
the classification, the log behaviour in BOTH directions, and the promise that
a failed reading is reported as ``unknown`` rather than as healthy.
"""

from __future__ import annotations

import pytest

from app.services.infra import pg_connection_monitor as mon

# ── Thresholds ────────────────────────────────────────────────────────────


@pytest.mark.unit
@pytest.mark.parametrize(
    "percent,expected",
    [
        (0.0, "ok"),
        (79.9, "ok"),
        (80.0, "warning"),  # boundary is inclusive
        (94.9, "warning"),
        (95.0, "critical"),  # boundary is inclusive
        (106.0, "critical"),  # the observed over-subscription
    ],
)
def test_classify_boundaries(percent, expected):
    """80 and 95 are inclusive lower bounds, not exclusive.

    Parametrised across both sides of each boundary so an off-by-one in the
    comparison operator cannot pass: a ``>`` instead of ``>=`` breaks exactly
    the 80.0 and 95.0 rows.
    """
    assert mon.classify(percent) == expected


# ── Logging, both directions ──────────────────────────────────────────────


def _capture(monkeypatch):
    """Collect the WARNING/ERROR lines ``log_pressure`` emits.

    ⚠️ Deliberately NOT pytest's ``caplog``: this project logs through loguru,
    which does not go through stdlib logging handlers, so ``caplog.records``
    is always empty and BOTH directions of a two-way test pass vacuously —
    including against an implementation that logs unconditionally. That trap
    is documented at ``tests/workflows/test_publish_distribution.py``
    ``_capture_warnings``; this is the same fix.
    """
    warnings: list[str] = []
    errors: list[str] = []
    real_warning = mon.logger.warning
    real_error = mon.logger.error
    monkeypatch.setattr(
        mon.logger,
        "warning",
        lambda msg, *a, **k: (
            warnings.append(str(msg).format(*a) if a else str(msg)),
            real_warning(msg, *a, **k),
        )[0],
    )
    monkeypatch.setattr(
        mon.logger,
        "error",
        lambda msg, *a, **k: (
            errors.append(str(msg).format(*a) if a else str(msg)),
            real_error(msg, *a, **k),
        )[0],
    )
    return warnings, errors


def _row(
    used: int = 1,
    max_connections: int = 200,
    total_backends: int = 62,
    typed_backends: int = 62,
    idle_in_transaction: int = 0,
    oldest_idle_in_transaction_seconds: int = 0,
) -> dict:
    """A ``pg_stat_activity`` aggregate row as the real query returns it.

    ``total_backends``/``typed_backends`` default to EQUAL — an unmasked
    view, which is what production reads (62/62). Tests that want the
    masked case pass them explicitly.
    """
    return {
        "used": used,
        "max_connections": max_connections,
        "total_backends": total_backends,
        "typed_backends": typed_backends,
        "idle_in_transaction": idle_in_transaction,
        "oldest_idle_in_transaction_seconds": oldest_idle_in_transaction_seconds,
    }


def _sample(percent: float) -> dict:
    return {
        "status": mon.classify(percent),
        "used": int(percent * 2),
        "max_connections": 200,
        "percent": percent,
        "idle_in_transaction": 0,
        "oldest_idle_in_transaction_seconds": 0,
        "sampled_at": 0.0,
    }


@pytest.mark.unit
def test_the_capture_helper_actually_captures(monkeypatch):
    """Guard for the two tests below.

    Without this, "logs nothing when healthy" would pass just as happily
    against a broken capture helper that never records anything — the assert
    would hold because nothing was captured, not because the behaviour was
    right. This test fails first if the helper stops working.
    """
    warnings, errors = _capture(monkeypatch)
    mon.logger.warning("probe line")
    mon.logger.error("probe line")

    assert warnings == ["probe line"]
    assert errors == ["probe line"]


@pytest.mark.unit
def test_pressure_at_or_above_80_percent_logs_a_warning(monkeypatch):
    warnings, errors = _capture(monkeypatch)

    mon.log_pressure(_sample(80.0))

    assert len(warnings) == 1
    assert "elevated" in warnings[0]
    assert errors == []


@pytest.mark.unit
def test_pressure_below_80_percent_logs_nothing(monkeypatch):
    """The other direction — the one that catches an unconditional log.

    Paired with the test above, this is what makes the threshold falsifiable:
    an implementation that always logs fails here, and one that never logs
    fails there.
    """
    warnings, errors = _capture(monkeypatch)

    mon.log_pressure(_sample(79.9))

    assert warnings == []
    assert errors == []


@pytest.mark.unit
def test_pressure_at_or_above_95_percent_logs_an_error(monkeypatch):
    warnings, errors = _capture(monkeypatch)

    mon.log_pressure(_sample(95.0))

    assert len(errors) == 1
    assert "critical" in errors[0]


@pytest.mark.unit
def test_an_unavailable_reading_is_logged_not_swallowed(monkeypatch):
    """``unknown`` must be noisy: a probe that silently stops reporting is
    indistinguishable from one reporting health."""
    warnings, _ = _capture(monkeypatch)

    mon.log_pressure(mon.unknown_sample("engine not configured"))

    assert len(warnings) == 1
    assert "unavailable" in warnings[0]


# ── Sampling ──────────────────────────────────────────────────────────────


@pytest.mark.unit
async def test_sample_reports_unknown_when_the_engine_is_unconfigured(monkeypatch):
    """DB-less boot must yield ``unknown``, never a zeroed-out ``ok``."""
    monkeypatch.setattr("app.db.engine.is_configured", lambda: False)

    sample = await mon.sample_connection_usage()

    assert sample["status"] == "unknown"
    assert sample["used"] is None
    assert sample["percent"] is None


@pytest.mark.unit
async def test_sample_reports_unknown_when_the_query_raises(monkeypatch):
    """A monitoring read must not be able to break the thing it monitors."""
    monkeypatch.setattr("app.db.engine.is_configured", lambda: True)

    async def _boom(*_a, **_k):
        raise RuntimeError("too many clients already")

    monkeypatch.setattr("app.db.scoped_sql.scoped_fetch_one", _boom)

    sample = await mon.sample_connection_usage()

    assert sample["status"] == "unknown"
    assert "too many clients already" in sample["reason"]


@pytest.mark.unit
async def test_sample_computes_percent_and_status_from_the_row(monkeypatch):
    monkeypatch.setattr("app.db.engine.is_configured", lambda: True)

    async def _stub(*_a, **_k):
        return _row(
            used=170,
            max_connections=200,
            idle_in_transaction=3,
            oldest_idle_in_transaction_seconds=42,
        )

    monkeypatch.setattr("app.db.scoped_sql.scoped_fetch_one", _stub)

    sample = await mon.sample_connection_usage()

    assert sample["used"] == 170
    assert sample["max_connections"] == 200
    assert sample["percent"] == 85.0
    assert sample["status"] == "warning"
    assert sample["idle_in_transaction"] == 3
    assert sample["oldest_idle_in_transaction_seconds"] == 42


@pytest.mark.unit
async def test_sample_declares_itself_a_system_read(monkeypatch):
    """Raw ``text()`` against a PG system catalog still goes through the
    ``scoped_sql`` choke point with an audit reason.

    ``scoped_sql`` fails closed when a caller declares neither ``scope=`` nor
    ``system=True``, so this asserts the declaration is present AND carries a
    non-empty reason (an empty one would raise there, not here).
    """
    monkeypatch.setattr("app.db.engine.is_configured", lambda: True)
    seen: dict = {}

    async def _spy(sql, params=None, **kwargs):
        seen.update(kwargs)
        seen["sql"] = sql
        return {
            "used": 1,
            "max_connections": 200,
            "idle_in_transaction": 0,
            "oldest_idle_in_transaction_seconds": 0,
        }

    monkeypatch.setattr("app.db.scoped_sql.scoped_fetch_one", _spy)

    await mon.sample_connection_usage()

    assert seen["system"] is True
    assert seen["reason"].strip()
    assert "pg_stat_activity" in seen["sql"]


@pytest.mark.unit
async def test_only_client_backends_count_against_max_connections(monkeypatch):
    """Since PG 12 the four backend classes have INDEPENDENT budgets.

        MaxBackends = max_connections
                    + autovacuum_max_workers + 1
                    + max_worker_processes
                    + max_wal_senders

    so only ``client backend`` draws from ``max_connections``. An earlier
    version of this list also counted walsender / parallel worker /
    autovacuum worker, citing the pre-PG-12 rule that ``max_wal_senders``
    must be < ``max_connections``. Verified in a throwaway PG 17 container
    that the rule is gone: ``-c max_connections=5 -c max_wal_senders=20``
    starts, and with all 5 slots held a REPLICATION connection still gets in
    while a 6th normal one gets "too many clients already".

    On production that mistake read 56 when the truth was 54 — inflation, in
    the direction of MANUFACTURING FALSE ALARMS, which is the exact thing the
    allowlist exists to prevent. The allowlist idea was right; the membership
    was wrong.
    """
    assert mon.SLOT_BACKEND_TYPES == ("client backend",)
    for not_a_slot in (
        # own budgets since PG 12 — counting them inflates the reading
        "walsender",
        "parallel worker",
        "autovacuum worker",
        # auxiliary processes — never took a max_connections slot
        "checkpointer",
        "background writer",
        "walwriter",
        "autovacuum launcher",
        "logical replication launcher",
    ):
        assert not_a_slot not in mon.SLOT_BACKEND_TYPES

    monkeypatch.setattr("app.db.engine.is_configured", lambda: True)
    captured: dict = {}

    async def _spy(sql, params=None, **_k):
        captured["sql"] = sql
        captured["params"] = params or {}
        return _row()

    monkeypatch.setattr("app.db.scoped_sql.scoped_fetch_one", _spy)
    await mon.sample_connection_usage()

    # The allowlist is actually bound as a parameter — a predicate built but
    # never wired would otherwise count every backend silently.
    bound = {v for k, v in captured["params"].items() if k.startswith("bt")}
    assert bound == set(mon.SLOT_BACKEND_TYPES)
    assert "backend_type IN (" in captured["sql"]


# ── Visibility self-check (the "silent green" guard) ──────────────────────


async def test_a_column_masked_view_reports_unknown_not_a_healthy_zero(monkeypatch):
    """Losing ``pg_monitor`` must be loud, not a green light.

    ``pg_stat_activity`` is readable by everyone, but a role without
    ``pg_monitor`` / ``pg_read_all_stats`` sees other sessions' COLUMNS as
    NULL while the rows remain. Production reads it as ``postgres``, which is
    not a superuser — it only works because that grant is in place, and a
    grant can be revoked.

    Verified in a throwaway container: privileged role saw ``7 total / 7
    typed``; a plain role saw ``6 total / 1 typed`` — only its own session.
    Fed through the old code that produced ``used=1, percent=1.0,
    status="ok"``: no exception, no reason, a perfectly healthy-looking
    reading from a probe that could no longer see anything.

    This is the failure family the repo keeps paying for (``not_probed``,
    ``xvfb_ready``): the broken output has the same shape as a correct one.
    """
    monkeypatch.setattr("app.db.engine.is_configured", lambda: True)

    async def _masked(*_a, **_k):
        # Exactly the shape the container reproduced: rows present, columns
        # blanked, so the FILTER count collapses to this session alone.
        return _row(used=1, total_backends=62, typed_backends=1)

    monkeypatch.setattr("app.db.scoped_sql.scoped_fetch_one", _masked)

    sample = await mon.sample_connection_usage()

    assert sample["status"] == "unknown"
    assert sample["used"] is None
    assert sample["percent"] is None
    assert "pg_monitor" in sample["reason"]


async def test_an_unmasked_view_is_not_flagged(monkeypatch):
    """The other direction — otherwise the guard could just always fire.

    Production reads 62/62, so equality is the normal case; a check that
    flagged everything would take the whole monitor offline.
    """
    monkeypatch.setattr("app.db.engine.is_configured", lambda: True)

    async def _clean(*_a, **_k):
        return _row(used=53, total_backends=62, typed_backends=62)

    monkeypatch.setattr("app.db.scoped_sql.scoped_fetch_one", _clean)

    sample = await mon.sample_connection_usage()

    assert sample["status"] == "ok"
    assert sample["used"] == 53


async def test_a_row_without_the_visibility_columns_is_unusable(monkeypatch):
    """Fail closed if the self-check columns are missing.

    Assuming "good" when the check itself did not run would reintroduce the
    silent-green path through the back door.
    """
    monkeypatch.setattr("app.db.engine.is_configured", lambda: True)

    async def _legacy(*_a, **_k):
        return {
            "used": 1,
            "max_connections": 200,
            "idle_in_transaction": 0,
            "oldest_idle_in_transaction_seconds": 0,
        }

    monkeypatch.setattr("app.db.scoped_sql.scoped_fetch_one", _legacy)

    sample = await mon.sample_connection_usage()

    assert sample["status"] == "unknown"


def test_masked_reason_is_a_pure_predicate():
    """Both directions on the detector itself, no DB and no async."""
    assert mon._masked_reason({"total_backends": 62, "typed_backends": 62}) is None
    assert mon._masked_reason({"total_backends": 62, "typed_backends": 1})
    assert mon._masked_reason({"total_backends": None, "typed_backends": None})


# ── Cache hand-off ────────────────────────────────────────────────────────


@pytest.mark.unit
async def test_missing_cache_reads_as_none_not_as_healthy(monkeypatch):
    """``/readyz`` renders ``None`` as "no reading". If this returned a
    default-shaped dict instead, a dead sampler would look like a healthy
    database."""

    class _NoData:
        async def get(self, _key):
            return None

    async def _client():
        return _NoData()

    monkeypatch.setattr("app.core.redis.get_async_redis", _client)

    assert await mon.get_cached_sample() is None


@pytest.mark.unit
async def test_cache_read_survives_a_dead_redis(monkeypatch):
    async def _client():
        raise ConnectionError("redis down")

    monkeypatch.setattr("app.core.redis.get_async_redis", _client)

    assert await mon.get_cached_sample() is None
