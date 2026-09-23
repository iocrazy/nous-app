"""转写 workflow 的扣费接线：成功收尾时扣一次，失败不扣，扣费出错不伤转写。

三条入口（按资源手动 / 按 platform_id 手动 / 下载后自动）最终都派发同一个
``ai_transcription_workflow``，所以这里是唯一的扣费点；每条入口"恰好扣一次"
归结为「这个 workflow 每次成功只调一次扣费步骤」+「扣费步骤按 workflow_id 幂等」。
"""

from __future__ import annotations

import inspect
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

INPUTS = {
    "audio_path": "d/audio.m4a",
    "resource_id": "900",
    "platform_id": "pf-1",
    "provider_key": "nous",
    "provider_config": {},
    "language": "auto",
    "task_assignment": "nous:moss-asr",
    "catalog_model": "nous-moss-asr",
    "media_duration_seconds": 600.0,
    "title": "My Video",
}


def _run(m, *, inputs=None, summary=None, whisper_exc=None, charge=None):
    manager = AsyncMock()
    dbos = MagicMock()
    dbos.workflow_id = "wf-9"
    charge = charge or AsyncMock(return_value={"status": "charged", "points": 1})
    whisper = (
        AsyncMock(side_effect=whisper_exc)
        if whisper_exc
        else AsyncMock(
            return_value=summary
            or {
                "language": "en",
                "duration_seconds": 1800.0,
                "text_len": 10,
                "segments_count": 2,
            }
        )
    )
    patches = [
        patch.object(m, "DBOS", dbos),
        patch.object(
            m, "load_transcribe_inputs", AsyncMock(return_value=inputs or INPUTS)
        ),
        patch.object(
            m, "assert_audio_present_step", AsyncMock(return_value="d/audio.m4a")
        ),
        patch.object(m, "run_whisper", whisper),
        patch.object(m, "mark_transcript_completed", AsyncMock()),
        patch.object(m, "mark_transcript_failed", AsyncMock()),
        patch.object(m, "charge_transcription_step", charge),
        patch(
            "app.services.infra.unified_task_manager.get_task_manager",
            return_value=manager,
        ),
        patch(
            "app.workflows._failure_handler.record_workflow_failure",
            AsyncMock(return_value={}),
        ),
        patch("app.tasks.download_helpers.chain_summary_for_tags", AsyncMock()),
        patch("app.tasks.download_helpers.consume_summary_follow_up", AsyncMock()),
    ]
    return patches, charge


async def _invoke(m):
    return await inspect.unwrap(m.ai_transcription_workflow)(
        parsed_media_id=1, user_id="u-1"
    )


async def test_success_charges_once_with_the_asr_duration():
    from app.workflows import ai_transcription as m

    patches, charge = _run(m)
    for p in patches:
        p.start()
    try:
        out = await _invoke(m)
    finally:
        for p in reversed(patches):
            p.stop()

    charge.assert_awaited_once()
    assert charge.await_args.kwargs == {
        "workflow_id": "wf-9",
        "user_id": "u-1",
        "catalog_model": "nous-moss-asr",
        "duration_seconds": 1800.0,
        "title": "My Video",
    }
    assert out["billing"] == {"status": "charged", "points": 1}


async def test_missing_asr_duration_falls_back_to_media_metadata():
    from app.workflows import ai_transcription as m

    patches, charge = _run(
        m,
        summary={
            "language": "en",
            "duration_seconds": None,
            "text_len": 1,
            "segments_count": 1,
        },
    )
    for p in patches:
        p.start()
    try:
        await _invoke(m)
    finally:
        for p in reversed(patches):
            p.stop()

    assert charge.await_args.kwargs["duration_seconds"] == 600.0


async def test_failed_transcription_is_never_charged():
    from app.workflows import ai_transcription as m

    patches, charge = _run(m, whisper_exc=RuntimeError("asr 500"))
    for p in patches:
        p.start()
    try:
        with pytest.raises(RuntimeError, match="asr 500"):
            await _invoke(m)
    finally:
        for p in reversed(patches):
            p.stop()

    charge.assert_not_awaited()


async def test_inputs_checkpointed_before_this_change_charge_nothing():
    """只向前不追扣：部署前已开始、load_transcribe_inputs 已落检查点的 workflow
    重放时没有 catalog_model 键 —— 按非平台模型处理。"""
    from app.workflows import ai_transcription as m

    legacy = {
        k: v
        for k, v in INPUTS.items()
        if k not in ("catalog_model", "media_duration_seconds", "title")
    }
    patches, charge = _run(m, inputs=legacy)
    for p in patches:
        p.start()
    try:
        await _invoke(m)
    finally:
        for p in reversed(patches):
            p.stop()

    assert charge.await_args.kwargs["catalog_model"] == ""


async def test_charge_step_delegates_to_the_billing_service(monkeypatch):
    from app.services.billing import transcription_billing as tb
    from app.workflows import ai_transcription as m

    outcome = tb.ChargeOutcome(status="charged", points=3)
    svc = AsyncMock(return_value=outcome)
    monkeypatch.setattr(tb, "charge_transcription", svc)

    out = await inspect.unwrap(m.charge_transcription_step)(
        workflow_id="wf-9",
        user_id="u-1",
        catalog_model="nous-moss-asr",
        duration_seconds=1800.0,
        title="My Video",
    )

    svc.assert_awaited_once_with(
        workflow_id="wf-9",
        user_id="u-1",
        catalog_model="nous-moss-asr",
        duration_seconds=1800.0,
        title="My Video",
    )
    assert out == {"status": "charged", "points": 3, "reason": None}
