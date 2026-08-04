"""Retry / budget behaviour of the shared runner, with the browser stubbed out."""

import pytest

from app import validation
from app.config import get_settings
from app.platforms.douyin import SPEC
from app.schemas import EnvironmentConfig, SessionStatus
from app.validation import ProbeKind, ProbeOutcome, run_dom_session_validation

pytestmark = pytest.mark.unit

LIVE_STATE = {"cookies": [{"name": "sessionid", "value": "x"}]}


def _stub_probe(monkeypatch, outcomes):
    calls = []

    async def fake_probe(spec, storage_state, env):
        calls.append((spec, storage_state, env))
        return outcomes[min(len(calls) - 1, len(outcomes) - 1)]

    monkeypatch.setattr(validation, "_probe_once", fake_probe)
    return calls


def _outcome(kind, reason="stub"):
    return ProbeOutcome(kind=kind, reason=reason, detail={})


async def test_empty_storage_state_short_circuits_before_launching(monkeypatch):
    calls = _stub_probe(monkeypatch, [_outcome(ProbeKind.VALID)])
    result = await run_dom_session_validation(SPEC, {})
    assert result.status is SessionStatus.SESSION_INVALID
    assert result.detail["stage"] == "fail_fast"
    assert calls == []


async def test_first_attempt_valid_returns_immediately(monkeypatch):
    calls = _stub_probe(monkeypatch, [_outcome(ProbeKind.VALID)])
    result = await run_dom_session_validation(SPEC, LIVE_STATE)
    assert result.success is True
    assert result.status is SessionStatus.SESSION_VALID
    assert len(calls) == 1
    assert result.detail["attempts"] == 1


async def test_intermittent_failure_is_retried(monkeypatch):
    """The whole point of retrying: one bad sample must not condemn a live
    session."""
    outcomes = [_outcome(ProbeKind.INVALID), _outcome(ProbeKind.VALID)]
    calls = _stub_probe(monkeypatch, outcomes)
    result = await run_dom_session_validation(SPEC, LIVE_STATE)
    assert result.success is True
    assert len(calls) == 2


async def test_three_consecutive_invalid_verdicts_settle_as_invalid(monkeypatch):
    calls = _stub_probe(monkeypatch, [_outcome(ProbeKind.INVALID)])
    result = await run_dom_session_validation(SPEC, LIVE_STATE)
    assert result.status is SessionStatus.SESSION_INVALID
    assert len(calls) == get_settings().validate_attempts == 3
    assert result.detail["attempts"] == 3


async def test_proxy_failure_stops_retrying(monkeypatch):
    """Hammering a dead proxy three times only burns the caller's budget."""
    calls = _stub_probe(monkeypatch, [_outcome(ProbeKind.PROXY_FAILED)])
    result = await run_dom_session_validation(
        SPEC, LIVE_STATE, EnvironmentConfig(proxy_url="http://proxy.example.com:8080")
    )
    assert result.status is SessionStatus.PROXY_FAILED
    assert len(calls) == 1


async def test_repeated_timeouts_report_timeout_not_invalid(monkeypatch):
    """A page that never loads says nothing about the cookie's validity."""
    _stub_probe(monkeypatch, [_outcome(ProbeKind.TIMEOUT)])
    result = await run_dom_session_validation(SPEC, LIVE_STATE)
    assert result.status is SessionStatus.TIMEOUT


async def test_unclassified_errors_report_failed(monkeypatch):
    _stub_probe(monkeypatch, [_outcome(ProbeKind.ERROR, "TargetClosedError")])
    result = await run_dom_session_validation(SPEC, LIVE_STATE)
    assert result.status is SessionStatus.FAILED


async def test_attempt_that_overruns_its_budget_is_abandoned(monkeypatch):
    """Bounded, always. An attempt that hangs must not hang the request."""
    import asyncio

    monkeypatch.setenv("BROWSER_VALIDATE_ATTEMPT_TIMEOUT_S", "1")
    monkeypatch.setenv("BROWSER_VALIDATE_ATTEMPTS", "1")
    get_settings.cache_clear()

    async def hanging_probe(spec, storage_state, env):
        await asyncio.sleep(30)

    monkeypatch.setattr(validation, "_probe_once", hanging_probe)
    result = await run_dom_session_validation(SPEC, LIVE_STATE)
    assert result.status is SessionStatus.TIMEOUT
    assert result.detail["stage"] == "attempt_budget"


async def test_exhausted_total_budget_reports_timeout(monkeypatch):
    monkeypatch.setenv("BROWSER_VALIDATE_TOTAL_TIMEOUT_S", "0")
    get_settings.cache_clear()
    calls = _stub_probe(monkeypatch, [_outcome(ProbeKind.VALID)])
    result = await run_dom_session_validation(SPEC, LIVE_STATE)
    assert result.status is SessionStatus.TIMEOUT
    assert result.detail["stage"] == "total_budget"
    assert calls == []


async def test_environment_is_forwarded_to_the_probe(monkeypatch):
    calls = _stub_probe(monkeypatch, [_outcome(ProbeKind.VALID)])
    env = EnvironmentConfig(timezone_id="Asia/Shanghai")
    await run_dom_session_validation(SPEC, LIVE_STATE, env)
    assert calls[0][2] is env
