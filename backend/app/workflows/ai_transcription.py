"""ai_transcription DBOS workflow — port of legacy
`ai_tasks.transcribe_audio_task`.

Strategy: thin wrapper around existing WhisperService so we don't reimplement
the whisper provider chain / segment serialization. The DBOS layer adds:
    - workflow_id-keyed memoization (rerun returns cached result)
    - per-step retry policy on the network call
    - cross-table writes via the SQLAlchemy engine (privileged Supavisor
      connection; no SET ROLE needed)

This module does NOT duplicate WhisperService.transcribe_and_save; it calls
into it. The whole port reduces to "wrap the async service call in a DBOS
workflow + lifecycle status updates on parsed_media / resources".
"""

from __future__ import annotations

import json
import os
from typing import Any

from dbos import DBOS
from loguru import logger


@DBOS.step()
async def load_transcribe_inputs(parsed_media_id: int, user_id: str) -> dict[str, Any]:
    """Resolve the audio file path + the user's whisper provider config."""
    from app.db import engine as db_engine

    media_row = await db_engine.fetch_one(
        "SELECT pm.id, pm.download_path, pm.extract_audio_path, pm.platform_id, "
        "r.id AS resource_id "
        "FROM public.parsed_media pm "
        "JOIN public.resources r ON r.media_id = pm.id "
        "WHERE pm.id = :pid LIMIT 1",
        {"pid": parsed_media_id},
    )
    if not media_row:
        raise RuntimeError(f"no parsed_media for id={parsed_media_id}")

    settings_row = await db_engine.fetch_one(
        "SELECT settings_json FROM public.user_settings WHERE user_id = :uid",
        {"uid": user_id},
    )

    audio_path = media_row.get("extract_audio_path") or media_row.get("download_path")
    if not audio_path:
        raise RuntimeError(f"no audio_path for parsed_media={parsed_media_id}")

    # ── Governance gate ─────────────────────────────────────────────────
    # Check BEFORE consulting user settings so a locked module short-circuits
    # without depending on the user having settings configured.
    from app.services.ai.adapters.factory import provider_key_for_model
    from app.services.ai.governance.ai_governance import get_module_governance

    governance = await get_module_governance("transcription")
    if not governance.allowed:
        if not governance.api_key_present:
            logger.error(
                "[governance] transcription is admin-locked but no admin api_key "
                "is configured; failing closed — WhisperService has no env fallback"
            )
            raise RuntimeError(
                "AI module 'transcription' is admin-locked but no admin API key "
                "is configured. Contact your platform administrator."
            )
        # Derive provider key from admin model prefix.  Volcengine is a special
        # ASR path; unknown prefix falls back to "openai" (Whisper API).
        try:
            admin_provider_key = (
                provider_key_for_model(governance.model)
                if governance.model
                else "openai"
            )
        except ValueError:
            admin_provider_key = "openai"
        logger.info(
            "[governance] transcription locked by admin; using admin config "
            "(provider_key=%r model=%r)",
            admin_provider_key,
            governance.model,
        )
        return {
            "audio_path": audio_path,
            "resource_id": str(media_row["resource_id"]),
            "platform_id": media_row["platform_id"],
            "provider_key": admin_provider_key,
            "provider_config": {
                "api_key": governance.api_key,
                "base_url": governance.base_url,
                "model": governance.model,
            },
            "language": "auto",
            "task_assignment": "",
        }
    # ── End governance gate ─────────────────────────────────────────────

    if not settings_row:
        raise RuntimeError(f"no user_settings for {user_id}")
    settings = settings_row["settings_json"]
    if isinstance(settings, str):
        settings = json.loads(settings)
    ai_settings = settings.get("ai_settings", {})
    providers = ai_settings.get("ai_providers", {}) or {}
    whisper_provider = ai_settings.get("whisper_provider", "openai")
    provider_cfg = providers.get(whisper_provider) or {}

    # task_assignment.transcription carries the model selection like
    # 'volcengine:bigasr' or 'volcengine:seed-asr'. Required for the
    # Volcengine path because the API key may only have one resource
    # granted — picking the wrong one returns 45000030 'resource not
    # granted'. master read this same field in ai_tasks.py.
    task_assignment = ai_settings.get("task_assignment", {}).get("transcription") or ""

    return {
        "audio_path": audio_path,
        "resource_id": str(media_row["resource_id"]),
        "platform_id": media_row["platform_id"],
        "provider_key": whisper_provider,
        "provider_config": provider_cfg,
        "language": ai_settings.get("preferred_language", "auto"),
        "task_assignment": task_assignment,
    }


@DBOS.step()
def assert_audio_present_step(audio_path: str) -> str:
    """Defensive guard — confirm the audio file exists on disk before
    invoking the (expensive + network-bound) whisper call.

    Replaces the previous wait_for_audio_step which polled inside the
    workflow body. The structural fix is event/state, not polling:
    extract_audio_workflow runs first, only chains this workflow on its
    success, and the manual-trigger endpoints in ai_router gate on
    extract_audio_path / music_download_path being on disk. By the time
    we arrive here the file should already exist. This step is a one-shot
    assertion that catches the rare desync (file deleted between dispatch
    and execution); it raises immediately rather than sleep-waiting, and
    the workflow's top-level try/except converts the failure into a
    task_tracking row with status='failed' instead of a hung worker."""
    from app.core.config import settings

    full_path = (
        audio_path
        if os.path.isabs(audio_path)
        else os.path.join(settings.DOWNLOAD_PATH, audio_path)
    )
    try:
        if os.path.exists(full_path) and os.path.getsize(full_path) > 0:
            return audio_path
    except OSError:
        pass
    raise RuntimeError(f"audio file missing or empty at dispatch time: {audio_path}")


@DBOS.step(retries_allowed=True, max_attempts=2)
async def run_whisper(
    audio_path: str,
    resource_id: str,
    *,
    provider_key: str,
    provider_config: dict[str, Any],
    language: str,
    task_assignment: str = "",
) -> dict[str, Any]:
    """Invoke transcription inside the DBOS step.

    Volcengine has its own ASR API (bigasr / seedasr — not OpenAI-compatible),
    so it special-cases through VolcengineASRService. All other providers
    (OpenAI, etc) go through WhisperService → AIProviderFactory.

    Reason for the split: AIProviderFactory routes 'volcengine' to
    DoubaoProvider (火山引擎 chat = Doubao), but Doubao's chat-completion
    API doesn't expose audio transcription. master had this same split
    in app.tasks.ai_tasks (Celery); when D-route ported the work to a
    DBOS workflow the volcengine branch was dropped, so every transcribe
    click since failed with "DoubaoProvider does not support transcription"
    until DBOSMaxStepRetriesExceeded.

    PR #237 audit: was sync ``def`` with two ``asyncio.run()`` calls
    (one here, one in _run_volcengine_asr). Now async — same fix as
    workflow_health_sweeper / agent_runs_sweeper."""
    if provider_key == "volcengine":
        return await _run_volcengine_asr(
            audio_path=audio_path,
            resource_id=resource_id,
            provider_config=provider_config,
            language=language,
            task_assignment=task_assignment,
        )

    from app.services.ai.transcribe.whisper_service import WhisperService

    svc = WhisperService(provider_key=provider_key, provider_config=provider_config)
    result = await svc.transcribe_and_save(
        resource_id=resource_id,
        audio_path=audio_path,
        language=language,
    )
    if result is None:
        raise RuntimeError("transcribe_and_save returned None")

    return {
        "language": result.language,
        "duration_seconds": result.duration,
        "text_len": len(result.text or ""),
        "segments_count": len(result.segments or []),
    }


async def _run_volcengine_asr(
    audio_path: str,
    resource_id: str,
    provider_config: dict[str, Any],
    language: str,
    task_assignment: str = "",
) -> dict[str, Any]:
    """Volcengine ASR path — needs a public audio URL since the API pulls
    the file rather than receiving an upload. Reuses the HMAC-signed media
    token (app.api.media_auth) so the URL is short-lived (1h) and tied to
    the calling user.

    Ported verbatim from master's app.tasks.ai_tasks.transcribe_audio_task
    (the volcengine branch). The DBOS port had previously dropped this.
    """
    import time as time_mod

    from app.api.media_auth import _sign_token
    from app.core.config import settings
    from app.services.ai.transcribe.volcengine_asr_service import (
        RESOURCE_V1,
        RESOURCE_V2,
        VolcengineASRService,
    )

    # Resolve audio_path to disk so we can derive the public URL path.
    # whisper_service has the same resolution chain; mirror it here so
    # both providers behave identically wrt path.
    if not os.path.exists(audio_path):
        joined = os.path.join(settings.DOWNLOAD_PATH, audio_path)
        if os.path.exists(joined):
            audio_path = joined

    # Pull the bound user id from the provider_config caller (load_transcribe_inputs
    # passes provider_config without user_id; we need to recover it from the
    # signed-token chain). The simplest path: read the resource's creator_id.
    # Avoids threading user_id through every step signature.
    from app.db import engine as db_engine

    row = await db_engine.fetch_one(
        "SELECT creator_id FROM public.resources WHERE id = :rid",
        {"rid": int(resource_id)},
    )
    if not row:
        raise RuntimeError(f"resource {resource_id} not found for volcengine asr")
    user_id = str(row["creator_id"])

    # Build signed media URL (4-part HMAC, 1h TTL — same scheme as <video src>).
    now = int(time_mod.time())
    expires_at = now + 3600
    media_token = _sign_token(user_id, now, expires_at)

    media_public_url = getattr(
        settings, "MEDIA_PUBLIC_URL", "https://mediahubserver.heygo.cn:88"
    )
    # The /media route serves files by file-path under DOWNLOAD_PATH;
    # use the path relative to download root to build the URL.
    rel_path = audio_path
    download_root = settings.DOWNLOAD_PATH.rstrip("/")
    if audio_path.startswith(download_root + "/"):
        rel_path = audio_path[len(download_root) + 1 :]
    audio_url = f"{media_public_url}/media/{rel_path}?token={media_token}"

    ext = os.path.splitext(audio_path)[1].lstrip(".").lower()
    audio_format = ext if ext in ("mp3", "wav", "ogg") else "wav"

    # Pick API resource based on the model the user assigned in
    # Settings → AI → Transcription, e.g. 'volcengine:bigasr' or
    # 'volcengine:seed-asr'. master derived this from
    # transcription_assignment; we now thread the same string through
    # task_assignment. Falls back to V1 (bigasr) since most accounts
    # only have that resource granted (V2 seed-asr requires a separate
    # entitlement and returns 45000030 'resource not granted' otherwise).
    model_part = (
        task_assignment.split(":", 1)[1] if ":" in task_assignment else task_assignment
    ) or (provider_config.get("model") or "")
    asr_resource = (
        RESOURCE_V2
        if ("seed" in model_part.lower() or "2.0" in model_part)
        else RESOURCE_V1
    )

    service = VolcengineASRService(
        app_id=provider_config.get("app_id", ""),
        access_token=provider_config.get("api_key", ""),
        asr_resource_id=asr_resource,
    )
    result = await service.transcribe_and_save(
        resource_id=resource_id,
        audio_url=audio_url,
        audio_format=audio_format,
    )
    if result is None:
        raise RuntimeError("Volcengine ASR returned None")
    return {
        "language": result.language,
        "duration_seconds": result.duration,
        "text_len": len(result.text or ""),
        "segments_count": len(result.segments or []),
    }


@DBOS.step()
async def mark_transcript_completed(parsed_media_id: int) -> None:
    """Flip resources.transcript_status='completed' for downstream consumers."""
    from app.db import engine as db_engine

    await db_engine.execute(
        "UPDATE public.resources SET transcript_status = 'completed' "
        "WHERE media_id = :pid",
        {"pid": parsed_media_id},
    )


@DBOS.workflow()
async def ai_transcription_workflow(
    parsed_media_id: int, user_id: str
) -> dict[str, Any]:
    """DBOS port of transcribe_audio_task. Same input/output contract:
    parsed_media_id + user_id → transcript persisted to resource_transcripts +
    resources.transcript_status='completed'.

    Workflow_id idempotency: re-running with the same workflow_id returns the
    cached result; the actual whisper call (expensive) runs once.
    """
    from app.services.infra.unified_task_manager import get_task_manager
    from app.workflows._failure_handler import record_workflow_failure

    manager = get_task_manager()
    wf_id = DBOS.workflow_id

    try:
        inputs = await load_transcribe_inputs(parsed_media_id, user_id)
        # Cheap on-disk assertion (one stat call). The chain dispatcher
        # only fires us after extract_audio_workflow succeeds, and the
        # manual trigger gate checks extract_audio_path/music_download_path
        # on disk, so this is a defense-in-depth check, not a wait loop.
        audio_path = assert_audio_present_step(inputs["audio_path"])
        await manager.update_progress(wf_id, 40, subtitle="Transcribing audio...")
        summary = await run_whisper(
            audio_path=audio_path,
            resource_id=inputs["resource_id"],
            provider_key=inputs["provider_key"],
            provider_config=inputs["provider_config"],
            language=inputs["language"],
            task_assignment=inputs.get("task_assignment", ""),
        )
        await mark_transcript_completed(parsed_media_id)
        await manager.update_progress(wf_id, 100, subtitle="Transcription complete")
        # Chain ai_summary AFTER transcript completes (was concurrent in
        # download_helpers.chain_transcript_summary_for_tags pre-this-fix —
        # ai_summary fired alongside transcript and failed with "no
        # transcript" because transcript wasn't written yet. QA 2026-05-17
        # task #24.) Best-effort; transcript success is what counts.
        try:
            from app.db.scope import Scope, request_scope
            from app.tasks.download_helpers import chain_summary_for_tags

            # §2.4b: chain_summary_for_tags is now async-native and reads
            # `resources` by awaiting the repo directly. Set the ambient USER
            # scope HERE and await it inside — the contextvar is naturally
            # visible to the awaited read (same async context; no copy_context
            # thread hop). `user_id` is a required workflow arg (always
            # present). INERT until SCOPE_ENFORCE_RESOURCES flips.
            async with request_scope(Scope(user_id=user_id)):
                await chain_summary_for_tags(parsed_media_id, user_id)
        except Exception as e:
            logger.warning(
                f"[ai_transcription] post-success summary chain failed for "
                f"parsed_media_id={parsed_media_id}: {type(e).__name__}: {e!r}"
            )
        return {"parsed_media_id": parsed_media_id, **summary}
    except Exception as e:  # noqa: BLE001
        return await record_workflow_failure(
            workflow_id=DBOS.workflow_id,
            error=e,
            context={
                "workflow": "ai_transcription",
                "parsed_media_id": parsed_media_id,
                "user_id": user_id,
            },
        )
