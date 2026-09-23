"""转写按时长计费（平台模型才扣，BYOK 不扣，价格 0 即免费，一次转写只扣一次）。

生产事实（2026-09-22 查实）：三条转写入口从来没有扣过一分 ——
手动 A 路径用 ``startswith("nous-")`` 判平台模型，而存的是 ``nous:<name>``；
手动 B 路径查空的 ``point_pricing`` 表；下载后自动转写根本没有扣费。
现在三条路径都汇到 ``ai_transcription_workflow``，在它成功收尾时扣一次。
"""

from __future__ import annotations

from decimal import Decimal
from unittest.mock import AsyncMock, MagicMock

import pytest

from app.services.billing import transcription_billing as tb

WF = "wf-1"
USER = "u-1"
TEAM = "42"


def _price(value, pricing_type="per_hour", name="nous-moss-asr"):
    return tb.TranscriptionPrice(
        model_name=name,
        provider="nous",
        pricing_type=pricing_type,
        pricing_value=Decimal(str(value)),
    )


# ─── 公式 ─────────────────────────────────────────────────────────


class TestPointsForDuration:
    @pytest.mark.parametrize(
        "duration,value,expected",
        [
            (3600, 6, 6),  # 整一小时 = 标价
            (600, 6, 1),  # 10 分钟 × 6/小时 = 1.0 → 1（恰好整数不多收）
            (601, 6, 2),  # 超过一丁点就进位
            (1, 6, 1),  # 正价格下任何正时长至少 1 分
            (5400, 6, 9),  # 1.5 小时
            (1800, 1, 1),  # 0.5 → 1
            (7200.4, 0.5, 2),  # 1.00005… → 2
        ],
    )
    def test_per_hour_ceils_the_exact_product(self, duration, value, expected):
        assert tb.points_for_duration(_price(value), duration) == expected

    def test_decimal_arithmetic_does_not_invent_a_point(self):
        # 浮点下 0.1*3600*... 这类乘积会变成 1.0000000002 再被 ceil 成 2。
        assert tb.points_for_duration(_price("0.1"), 36000) == 1

    @pytest.mark.parametrize("duration", [0, 1, 600, 36000])
    def test_price_zero_is_free_without_a_minimum(self, duration):
        assert tb.points_for_duration(_price(0), duration) == 0

    @pytest.mark.parametrize("duration", [None, 0, -5])
    def test_unknown_duration_charges_nothing(self, duration):
        assert tb.points_for_duration(_price(6), duration) == 0

    def test_per_request_is_flat_and_ceiled(self):
        assert tb.points_for_duration(_price("2.2", "per_request"), 9999) == 3
        assert tb.points_for_duration(_price(0, "per_request"), 9999) == 0

    def test_per_token_cannot_price_audio(self):
        assert tb.points_for_duration(_price(5, "per_token"), 600) is None


# ─── 目录价格查询 ─────────────────────────────────────────────────


class TestPriceForCatalogModel:
    async def test_legacy_mediahub_name_resolves_through_the_alias_lookup(
        self, monkeypatch
    ):
        repo = MagicMock()
        repo.get_by_name = AsyncMock(
            return_value={
                "name": "nous-moss-asr",
                "actual_provider": "nous",
                "pricing_type": "per_hour",
                "pricing_value": Decimal("6"),
            }
        )
        repo.get_by_actual_model = AsyncMock(return_value=None)
        monkeypatch.setattr(tb, "_nous_repo", lambda: repo)

        price = await tb.price_for_catalog_model("mediahub-moss-asr")

        repo.get_by_name.assert_awaited_once_with("mediahub-moss-asr")
        assert price == _price(6)

    async def test_falls_back_to_actual_model(self, monkeypatch):
        repo = MagicMock()
        repo.get_by_name = AsyncMock(return_value=None)
        repo.get_by_actual_model = AsyncMock(
            return_value={
                "name": "nous-moss-asr",
                "actual_provider": "nous",
                "pricing_type": "per_hour",
                "pricing_value": 6,
            }
        )
        monkeypatch.setattr(tb, "_nous_repo", lambda: repo)

        price = await tb.price_for_catalog_model("moss-asr")
        assert price is not None and price.model_name == "nous-moss-asr"

    async def test_unknown_model_is_none(self, monkeypatch):
        repo = MagicMock()
        repo.get_by_name = AsyncMock(return_value=None)
        repo.get_by_actual_model = AsyncMock(return_value=None)
        monkeypatch.setattr(tb, "_nous_repo", lambda: repo)
        assert await tb.price_for_catalog_model("ghost") is None


# ─── 扣费（在 workflow 收尾处调用）─────────────────────────────────


def _wire(monkeypatch, *, price, team=TEAM, already=None, consume=None):
    monkeypatch.setattr(tb, "price_for_catalog_model", AsyncMock(return_value=price))
    monkeypatch.setattr(tb, "_team_for_user", AsyncMock(return_value=team))
    repo = MagicMock()
    repo.charged_points_for_references = AsyncMock(return_value=already or {})
    ps = MagicMock()
    ps.repo = repo
    ps.ensure_team_quota = AsyncMock()
    ps.check_and_consume = AsyncMock(
        return_value=consume
        or {"success": True, "points_cost": 0, "balance_after": 1, "reason": None}
    )
    monkeypatch.setattr(tb, "PointsService", lambda: ps)
    return ps


async def _charge(**overrides):
    kwargs = {
        "workflow_id": WF,
        "user_id": USER,
        "catalog_model": "nous-moss-asr",
        "duration_seconds": 1800.0,
        "title": "My Video",
    }
    kwargs.update(overrides)
    return await tb.charge_transcription(**kwargs)


class TestChargeTranscription:
    async def test_platform_model_charged_by_duration_with_ledger_detail(
        self, monkeypatch
    ):
        ps = _wire(
            monkeypatch,
            price=_price(6),
            consume={
                "success": True,
                "points_cost": 3,
                "balance_after": 97,
                "reason": None,
            },
        )

        out = await _charge()

        assert out.status == "charged"
        assert out.points == 3
        ps.check_and_consume.assert_awaited_once()
        kw = ps.check_and_consume.await_args.kwargs
        assert kw["team_id"] == TEAM
        assert kw["user_id"] == USER
        assert kw["action_type"] == "ai_transcription"
        assert kw["reference_id"] == WF
        assert kw["override_cost"] == 3
        assert kw["description"] == "AI Transcription: My Video (nous-moss-asr, 30:00)"
        assert kw["ledger_fields"] == {
            "provider": "nous",
            "model": "nous-moss-asr",
            "duration_seconds": Decimal("1800.0"),
            "is_nous": True,
        }

    async def test_price_zero_charges_nothing_and_writes_no_transaction(
        self, monkeypatch
    ):
        ps = _wire(monkeypatch, price=_price(0))
        out = await _charge()
        assert out.status == "free"
        assert out.points == 0
        ps.check_and_consume.assert_not_awaited()

    @pytest.mark.parametrize("catalog_model", ["", None])
    async def test_byok_or_governance_run_is_never_charged(
        self, monkeypatch, catalog_model
    ):
        ps = _wire(monkeypatch, price=_price(6))
        out = await _charge(catalog_model=catalog_model)
        assert out.status == "not_platform"
        tb.price_for_catalog_model.assert_not_awaited()
        ps.check_and_consume.assert_not_awaited()

    async def test_second_call_for_the_same_workflow_does_not_charge_again(
        self, monkeypatch
    ):
        """DBOS 重放 / 步骤重跑：账本里已有这个 workflow 的扣分行 → 不再扣。"""
        ps = _wire(monkeypatch, price=_price(6), already={WF: 3.0})
        out = await _charge()
        assert out.status == "already_charged"
        ps.repo.charged_points_for_references.assert_awaited_once_with(
            reference_type="ai_transcription", reference_ids=[WF]
        )
        ps.check_and_consume.assert_not_awaited()

    async def test_insufficient_balance_keeps_the_transcript_and_records_denial(
        self, monkeypatch
    ):
        _wire(
            monkeypatch,
            price=_price(6),
            consume={
                "success": False,
                "points_cost": 3,
                "balance_after": 1,
                "reason": "Insufficient points balance.",
            },
        )
        out = await _charge()
        assert out.status == "denied"
        assert out.points == 0
        assert out.reason == "Insufficient points balance."

    async def test_no_team_is_not_charged(self, monkeypatch):
        ps = _wire(monkeypatch, price=_price(6), team=None)
        out = await _charge()
        assert out.status == "no_team"
        ps.check_and_consume.assert_not_awaited()

    async def test_unknown_duration_is_not_charged(self, monkeypatch):
        ps = _wire(monkeypatch, price=_price(6))
        out = await _charge(duration_seconds=None)
        assert out.status == "free"
        ps.check_and_consume.assert_not_awaited()

    async def test_unpriceable_model_is_not_charged(self, monkeypatch):
        ps = _wire(monkeypatch, price=None)
        out = await _charge()
        assert out.status == "unpriced"
        ps.check_and_consume.assert_not_awaited()

    async def test_an_exception_never_escapes(self, monkeypatch):
        """扣费失败不能把一次成功的转写变成失败的 workflow。"""
        ps = _wire(monkeypatch, price=_price(6))
        ps.check_and_consume = AsyncMock(side_effect=RuntimeError("db down"))
        out = await _charge()
        assert out.status == "error"
        assert "db down" in (out.reason or "")

    async def test_ledger_read_failure_does_not_charge(self, monkeypatch):
        """查不到「扣过没有」时宁可不扣，也不冒重复扣的险。"""
        ps = _wire(monkeypatch, price=_price(6))
        ps.repo.charged_points_for_references = AsyncMock(
            side_effect=RuntimeError("read failed")
        )
        out = await _charge()
        assert out.status == "error"
        ps.check_and_consume.assert_not_awaited()


# ─── 派发前的余额预检（不扣）──────────────────────────────────────


class TestPreflight:
    async def test_platform_price_blocks_when_balance_short(self, monkeypatch):
        monkeypatch.setattr(
            tb, "_platform_catalog_model_for", AsyncMock(return_value="nous-moss-asr")
        )
        monkeypatch.setattr(
            tb, "price_for_catalog_model", AsyncMock(return_value=_price(6))
        )
        monkeypatch.setattr(tb, "_team_for_user", AsyncMock(return_value=TEAM))
        ps = MagicMock()
        ps.ensure_team_quota = AsyncMock()
        ps.check_quota = AsyncMock(
            return_value={"allowed": False, "reason": "Insufficient points balance."}
        )
        ps.check_and_consume = AsyncMock()
        monkeypatch.setattr(tb, "PointsService", lambda: ps)

        reason = await tb.preflight_transcription(USER, 3600)

        assert reason == "Insufficient points balance."
        kw = ps.check_quota.await_args.kwargs
        assert kw["override_cost"] == 6
        assert kw["action_type"] == "ai_transcription"
        ps.check_and_consume.assert_not_awaited()

    async def test_free_or_byok_skips_the_balance_check(self, monkeypatch):
        monkeypatch.setattr(
            tb, "_platform_catalog_model_for", AsyncMock(return_value="")
        )
        ps = MagicMock()
        ps.check_quota = AsyncMock()
        monkeypatch.setattr(tb, "PointsService", lambda: ps)
        assert await tb.preflight_transcription(USER, 3600) is None
        ps.check_quota.assert_not_awaited()

    async def test_zero_price_skips_the_balance_check(self, monkeypatch):
        monkeypatch.setattr(
            tb, "_platform_catalog_model_for", AsyncMock(return_value="nous-moss-asr")
        )
        monkeypatch.setattr(
            tb, "price_for_catalog_model", AsyncMock(return_value=_price(0))
        )
        ps = MagicMock()
        ps.check_quota = AsyncMock()
        monkeypatch.setattr(tb, "PointsService", lambda: ps)
        assert await tb.preflight_transcription(USER, 3600) is None
        ps.check_quota.assert_not_awaited()


class TestPlatformCatalogModelFor:
    async def test_platform_origin_returns_catalog_name(self, monkeypatch):
        from app.services.ai.providers import ai_provider_helpers as h

        cfg = h.ResolvedAIConfig(
            provider_key="nous",
            provider_config={},
            model="nous:moss-asr",
            agent_slug="",
            origin="platform",
            catalog_model="nous-moss-asr",
        )
        monkeypatch.setattr(
            h, "resolve_transcription_config", AsyncMock(return_value=cfg)
        )
        assert await tb._platform_catalog_model_for(USER) == "nous-moss-asr"

    @pytest.mark.parametrize("origin", ["byok", "env", "governance"])
    async def test_other_origins_are_not_platform(self, monkeypatch, origin):
        from app.services.ai.providers import ai_provider_helpers as h

        cfg = h.ResolvedAIConfig(
            provider_key="volcengine",
            provider_config={},
            model="volcengine:bigasr",
            agent_slug="",
            origin=origin,
        )
        monkeypatch.setattr(
            h, "resolve_transcription_config", AsyncMock(return_value=cfg)
        )
        assert await tb._platform_catalog_model_for(USER) == ""

    async def test_unresolvable_settings_are_left_to_the_workflow(self, monkeypatch):
        from app.services.ai.providers import ai_provider_helpers as h

        monkeypatch.setattr(
            h,
            "resolve_transcription_config",
            AsyncMock(side_effect=RuntimeError("no user_settings for u-1")),
        )
        assert await tb._platform_catalog_model_for(USER) == ""
