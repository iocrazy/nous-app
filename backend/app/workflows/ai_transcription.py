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

    return {
        "audio_path": audio_path,
        "resource_id": str(media_row["resource_id"]),
        "platform_id": media_row["platform_id"],
        "provider_key": whisper_provider,
        "provider_config": provider_cfg,
        "language": ai_settings.get("preferred_language", "auto"),
    }


@DBOS.step(retries_allowed=True, max_attempts=2)
def run_whisper(
    audio_path: str,
    resource_id: str,
    *,
    provider_key: str,
    provider_config: dict[str, Any],
    language: str,
) -> dict[str, Any]:
    """Invoke WhisperService.transcribe_and_save synchronously inside the DBOS
    step. The service is async; we drive it with asyncio.run because each
    DBOS step body runs in its own thread (DBOS launches step bodies on a
    worker thread pool — no enclosing event loop).
    """
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
    inputs = load_transcribe_inputs(parsed_media_id, user_id)
    summary = run_whisper(
        audio_path=inputs["audio_path"],
        resource_id=inputs["resource_id"],
        provider_key=inputs["provider_key"],
        provider_config=inputs["provider_config"],
        language=inputs["language"],
    )
    mark_transcript_completed(parsed_media_id)
    return {"parsed_media_id": parsed_media_id, **summary}
