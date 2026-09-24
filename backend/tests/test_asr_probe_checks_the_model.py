"""The ASR probe must say something about THIS model, not about the endpoint.

WHAT IT USED TO SAY
───────────────────
``AIProviderFactory.test_connection`` returns ``success=True`` the moment
``list_models()`` comes back without raising — it never looks at whether the
row's own ``actual_model`` is in the list it just fetched. The ASR branch of the
probe took that boolean as ``ok``, so a green ASR dot meant exactly one thing:
"``/v1/models`` answered".

That is not a theoretical gap. Measured against the nous-engine gateway on
2026-09-22, minutes apart:

    11:40  /v1/models → [moss-asr, wemm-embedding-4b, qwen3-8-27b]
           POST /embeddings (wemm-embedding-4b) → 503 "model is not loaded"
    11:41  /v1/models → [moss-asr, qwen3-8-27b]          ← wemm delisted
    11:42  /v1/models → [moss-asr]                       ← qwen delisted too
           POST /chat/completions (qwen3-8-27b) → 200     ← but it serves fine

The engine loads models on demand, so its list is a snapshot of what is loaded
right now, and the probe was reading "the server is up" as "your model works".
The same day, a real regression (PR #2375) was declared verified partly on the
strength of one of those green dots.

WHAT IT SAYS NOW
────────────────
Three outcomes, and the middle one is the point:

  ok          the row's model is in the provider's list
  not_probed  the endpoint answered but did not list this model — readiness
              unknown, NOT a verdict either way
  fail        the call itself failed

``not_probed`` rather than ``fail`` for the middle case is deliberate. A lazily
loaded model can be absent from the list and still serve (qwen3 above did
exactly that), so calling it broken would be the "探针够不着 ≠ 目标是坏的"
mistake that CLAUDE.md records from the dead-domain deploy probe. What the probe
genuinely knows is that it did not confirm anything — and the existing
three-state vocabulary already has a word for that.

SCOPE SINCE 2026-09-24
----------------------
For ``actual_provider='nous'`` this list-membership branch is now the ACTIVE
path only (the admin Test, ``allow_costly=True``). The hourly poll reads the
engine's ``GET /v1/models/{id}`` readiness instead, which answers "is it
loaded" directly -- see test_nous_engine_passive_probe.py.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest

from app.services.ai.nous_model_health import (
    probe_nous_model,
    probe_result_status,
)


def _asr_row(**over):
    row = {
        "name": "nous-moss-asr",
        "type": "asr",
        "actual_provider": "nous",
        "actual_model": "moss-asr",
        "api_key": "k",
        "base_url": "http://engine.invalid/v1",
        "app_id": "",
    }
    row.update(over)
    return row


def _connection(success=True, models=None, error=None):
    return patch(
        "app.services.ai.nous_model_health.AIProviderFactory.test_connection",
        new=AsyncMock(
            return_value={"success": success, "models": models, "error": error}
        ),
    )


@pytest.mark.asyncio
async def test_model_present_in_the_list_is_ok():
    with _connection(models=["moss-asr", "qwen3-8-27b"]):
        result = await probe_nous_model(_asr_row(), allow_costly=True)
    assert result["ok"] is True
    assert probe_result_status(result) == "ok"


@pytest.mark.asyncio
async def test_endpoint_answers_but_does_not_list_this_model_is_not_probed():
    """The exact production shape: the gateway answered and named OTHER models.

    Before this change the probe called that ``ok`` — an affirmative claim about
    a model the server had just declined to mention.
    """
    with _connection(models=["qwen3-8-27b"]):
        result = await probe_nous_model(_asr_row(), allow_costly=True)
    assert result["ok"] is False
    assert probe_result_status(result) == "not_probed"
    # The reason has to name both sides or it is not actionable: which model was
    # wanted, and what the server actually offered.
    detail = f"{result.get('detail') or ''} {result.get('error') or ''}"
    assert "moss-asr" in detail and "qwen3-8-27b" in detail


@pytest.mark.asyncio
async def test_empty_model_list_is_not_probed_not_ok():
    """An empty list is the strongest form of "did not confirm". It used to be
    the strongest form of success — `success=True, models=[]` sailed through.
    """
    with _connection(models=[]):
        result = await probe_nous_model(_asr_row(), allow_costly=True)
    assert probe_result_status(result) == "not_probed"


@pytest.mark.asyncio
async def test_a_provider_with_no_model_catalog_stays_ok():
    """The negative control, and the reason this is not simply `fail`.

    Some providers expose no model list at all (volcengine ASR is the documented
    case — ``test_connection`` returns ``models=None`` for it). Demanding
    membership in a list that does not exist would turn every one of those rows
    permanently red, which is the false-red failure this file is otherwise
    trying to remove.
    """
    with _connection(models=None):
        result = await probe_nous_model(_asr_row(actual_provider="volcengine"))
    assert result["ok"] is True
    assert probe_result_status(result) == "ok"


@pytest.mark.asyncio
async def test_failed_call_is_still_a_plain_failure():
    """Unchanged: a call that errored is `fail`, never the softer `not_probed`."""
    with _connection(success=False, error="Unknown provider: nous"):
        result = await probe_nous_model(_asr_row(), allow_costly=True)
    assert probe_result_status(result) == "fail"
    assert "Unknown provider" in (result.get("error") or "")
