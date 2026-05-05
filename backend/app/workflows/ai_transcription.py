"""ai_transcription DBOS workflow — port of legacy
`ai_tasks.transcribe_audio_task`.

Strategy: thin wrapper around existing WhisperService so we don't reimplement
the whisper provider chain / segment serialization. The DBOS layer adds:
    - workflow_id-keyed memoization (rerun returns cached result)
    - per-step retry policy on the network call
    - service_role bypass for cross-table writes

This module does NOT duplicate WhisperService.transcribe_and_save; it calls
into it. The whole port reduces to "wrap the async service call in a DBOS
workflow + lifecycle status updates on parsed_media / resources".
"""

from __future__ import annotations

import asyncio
import json
import os
from typing import Any

import psycopg
from dbos import DBOS


def _dsn() -> str:
    url = os.environ.get("DBOS_DATABASE_URL")
    if not url:
        raise RuntimeError("DBOS_DATABASE_URL not configured")
    return url + ("&" if "?" in url else "?") + "sslmode=disable"


@DBOS.step()
def load_transcribe_inputs(parsed_media_id: int, user_id: str) -> dict[str, Any]:
    """Resolve the audio file path + the user's whisper provider config.

    Mirrors the path-resolution logic in transcribe_audio_task: prefers an
    extracted MP3 next to the download_path, falls back to deriving from the
    video file. Returns minimal inputs for the actual transcribe step.
    """
    with psycopg.connect(_dsn(), row_factory=psycopg.rows.dict_row) as conn:
        conn.execute("SET ROLE service_role")
        cur = conn.execute(
            """SELECT pm.id, pm.download_path, pm.extract_audio_path,
                      pm.platform_id, r.id AS resource_id
               FROM public.parsed_media pm
               JOIN public.resources r ON r.media_id = pm.id
               WHERE pm.id = %s
               LIMIT 1""",
            (parsed_media_id,),
        )
        media_row = cur.fetchone()
        if not media_row:
            raise RuntimeError(f"no parsed_media for id={parsed_media_id}")

        cur = conn.execute(
            "SELECT settings_json FROM public.user_settings WHERE user_id = %s",
            (user_id,),
        )
        settings_row = cur.fetchone()

    audio_path = media_row.get("extract_audio_path") or media_row.get("download_path")
    if not audio_path:
        raise RuntimeError(f"no audio_path for parsed_media={parsed_media_id}")

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
    task_assignment = (
        ai_settings.get("task_assignment", {}).get("transcription") or ""
    )

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
    workflow body. Polling was the wrong abstraction: the right one is
    event/state — `maybe_chain_ai_pipeline` and the manual trigger
    endpoints now check `parsed_media.music_download_status='completed'`
    BEFORE dispatching this workflow, so by the time we arrive here the
    file should already be on disk. This step is a one-shot assertion
    that catches the rare desync (file deleted between dispatch and
    execution); it raises immediately rather than sleep-waiting, and
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
def run_whisper(
    audio_path: str,
    resource_id: str,
    *,
    provider_key: str,
    provider_config: dict[str, Any],
    language: str,
    task_assignment: str = "",
) -> dict[str, Any]:
    """Invoke transcription synchronously inside the DBOS step.

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
    """
    if provider_key == "volcengine":
        return _run_volcengine_asr(
            audio_path=audio_path,
            resource_id=resource_id,
            provider_config=provider_config,
            language=language,
            task_assignment=task_assignment,
        )

    from app.services.whisper_service import WhisperService

    svc = WhisperService(provider_key=provider_key, provider_config=provider_config)
    result = asyncio.run(
        svc.transcribe_and_save(
            resource_id=resource_id,
            audio_path=audio_path,
            language=language,
        )
    )
    if result is None:
        raise RuntimeError("transcribe_and_save returned None")

    return {
        "language": result.language,
        "duration_seconds": result.duration,
        "text_len": len(result.text or ""),
        "segments_count": len(result.segments or []),
    }


def _run_volcengine_asr(
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
    import hashlib
    import hmac as hmac_mod
    import time as time_mod

    from app.api.media_auth import _get_secret
    from app.core.config import settings
    from app.services.volcengine_asr_service import (
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
    with psycopg.connect(_dsn(), row_factory=psycopg.rows.dict_row) as conn:
        conn.execute("SET ROLE service_role")
        cur = conn.execute(
            "SELECT creator_id FROM public.resources WHERE id = %s",
            (resource_id,),
        )
        row = cur.fetchone()
    if not row:
        raise RuntimeError(f"resource {resource_id} not found for volcengine asr")
    user_id = str(row["creator_id"])

    # Build signed media URL (HMAC, 1h TTL — same scheme as <video src>).
    expires_at = int(time_mod.time()) + 3600
    payload = f"{user_id}.{expires_at}"
    sig = hmac_mod.new(
        _get_secret().encode(), payload.encode(), hashlib.sha256
    ).hexdigest()[:32]
    media_token = f"{payload}.{sig}"

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
        task_assignment.split(":", 1)[1]
        if ":" in task_assignment
        else task_assignment
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
    result = asyncio.run(
        service.transcribe_and_save(
            resource_id=resource_id,
            audio_url=audio_url,
            audio_format=audio_format,
        )
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
def mark_transcript_completed(parsed_media_id: int) -> None:
    """Flip resources.transcript_status='completed' for downstream consumers."""
    with psycopg.connect(_dsn()) as conn:
        conn.execute("SET ROLE service_role")
        conn.execute(
            "UPDATE public.resources SET transcript_status = 'completed' WHERE media_id = %s",
            (parsed_media_id,),
        )
        conn.commit()


@DBOS.workflow()
def ai_transcription_workflow(parsed_media_id: int, user_id: str) -> dict[str, Any]:
    """DBOS port of transcribe_audio_task. Same input/output contract:
    parsed_media_id + user_id → transcript persisted to resource_transcripts +
    resources.transcript_status='completed'.

    Workflow_id idempotency: re-running with the same workflow_id returns the
    cached result; the actual whisper call (expensive) runs once.
    """
    from app.workflows._failure_handler import record_workflow_failure

    try:
        inputs = load_transcribe_inputs(parsed_media_id, user_id)
        # Cheap on-disk assertion (one stat call). Dispatcher already
        # gated on parsed_media.music_download_status='completed', so
        # this is a defense-in-depth check, not a wait loop.
        audio_path = assert_audio_present_step(inputs["audio_path"])
        summary = run_whisper(
            audio_path=audio_path,
            resource_id=inputs["resource_id"],
            provider_key=inputs["provider_key"],
            provider_config=inputs["provider_config"],
            language=inputs["language"],
            task_assignment=inputs.get("task_assignment", ""),
        )
        mark_transcript_completed(parsed_media_id)
        return {"parsed_media_id": parsed_media_id, **summary}
    except Exception as e:  # noqa: BLE001
        return record_workflow_failure(
            workflow_id=DBOS.workflow_id,
            error=e,
            context={
                "workflow": "ai_transcription",
                "parsed_media_id": parsed_media_id,
                "user_id": user_id,
            },
        )
