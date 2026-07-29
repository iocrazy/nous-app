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
        "SELECT pm.id, pm.download_path, pm.extract_audio_path, "
        "pm.music_download_path, pm.platform_id, "
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

    # Audio-source precedence:
    #   1. extract_audio_path — ffmpeg-extracted audio from a video (present
    #      for the video download path).
    #   2. music_download_path — background music fetched separately. This is
    #      the ONLY on-disk audio for image galleries (douyin 图文, media_type
    #      68): galleries have no video, so extract_audio_path is always empty
    #      and the true audio lives at <dir>/audio.mp3. Dropping this fell back
    #      to download_path, which for galleries is a DIRECTORY — the ASR then
    #      failed with Volcengine 45000006 "Invalid audio URI".
    #   3. download_path — last resort (single-file video downloads).
    audio_path = (
        media_row.get("extract_audio_path")
        or media_row.get("music_download_path")
        or media_row.get("download_path")
    )
    if not audio_path:
        raise RuntimeError(f"no audio_path for parsed_media={parsed_media_id}")

    # Resolve the provider config through the shared typed resolver (A2 lift).
    # It owns the governance gate (platform-catalog first), the "no
    # user_settings" raise, the gated nous:<model> path, and the BYOK fallback.
    # The transcription model-selection STRING (provider:model / raw picker) is
    # carried on ResolvedAIConfig.model — it becomes this dict's task_assignment,
    # threaded downstream to run_whisper. `settings_row["settings_json"]` is
    # passed to avoid a second DB read.
    from app.services.ai.providers.ai_provider_helpers import (
        resolve_transcription_config,
    )

    cfg = await resolve_transcription_config(
        user_id,
        settings_json=(settings_row.get("settings_json") if settings_row else None),
    )

    # language: governance short-circuits to "auto" (user settings not
    # consulted); the user path reads preferred_language from the same settings
    # row (guaranteed present here — the resolver raises otherwise).
    if cfg.origin == "governance":
        language = "auto"
    else:
        settings = settings_row["settings_json"]
        if isinstance(settings, str):
            settings = json.loads(settings)
        language = settings.get("ai_settings", {}).get("preferred_language", "auto")

    return {
        "audio_path": audio_path,
        "resource_id": str(media_row["resource_id"]),
        "platform_id": media_row["platform_id"],
        "provider_key": cfg.provider_key,
        "provider_config": cfg.provider_config,
        "language": language,
        "task_assignment": cfg.model,
    }


@DBOS.step()
async def assert_audio_present_step(audio_path: str) -> str:
    """Defensive guard — confirm the audio is actually present before
    invoking the (expensive + network-bound) whisper call.

    Replaces the previous wait_for_audio_step which polled inside the
    workflow body. The structural fix is event/state, not polling:
    extract_audio_workflow runs first, only chains this workflow on its
    success, and the manual-trigger endpoints in ai_router gate on
    extract_audio_path / music_download_path being on disk. By the time
    we arrive here the audio should already exist. This step is a one-shot
    assertion that catches the rare desync (file/object deleted between
    dispatch and execution); it raises immediately rather than
    sleep-waiting, and the workflow's top-level try/except converts the
    failure into a task_tracking row with status='failed' instead of a
    hung worker.

    Object-store dispatch (Task C7, storage-full-s3-migration PR-1): C3
    made extract_audio_path/music_download_path routinely sb:// after
    migration, and the plain-filesystem AudioSourceResolver would raise on
    those before this workflow ever reached C4's object-store-aware ASR
    branches. Dispatch on the resolved source: sb:// short-circuits via
    ObjectStore.exists()/get_size() (no on-disk isdir concept for a single
    object — nothing to glob), returning the ORIGINAL audio_path unchanged
    so run_whisper/_run_volcengine_asr keep receiving the sb:// form they
    already know how to handle. Filesystem sources keep going through the
    shared AudioSourceResolver unchanged (still owns isdir / glob-fallback
    semantics for whisper_service.py too — not duplicated here)."""
    from app.services.library.media_storage import ObjectStore, resolve_media_source

    loc = resolve_media_source(audio_path)
    if loc.is_object_store:
        store = ObjectStore(loc.bucket)
        # exists() before get_size() is the short-circuit — get_size() raises
        # on a missing key, so the `and` must never evaluate get_size() first.
        if await store.exists(loc.key) and await store.get_size(loc.key) > 0:
            return audio_path
        raise RuntimeError(
            f"audio object missing or empty at dispatch time: {audio_path}"
        )

    from app.services.media.audio_source import AudioSourceResolver

    return AudioSourceResolver().assert_playable(audio_path)


def _assignment_model(
    task_assignment: str, provider_config: dict[str, Any], default: str
) -> str:
    """Derive the concrete ASR model name from the resolver's carrier string.

    ``resolve_transcription_config`` packs the transcription model selection
    onto ``ResolvedAIConfig.model`` (threaded here as ``task_assignment``) in
    three shapes (see that function's docstring):

      - governance-locked → ``""``; the real model rides in
        ``provider_config["model"]`` (the locked catalog config).
      - platform / ``nous:<model>`` pick → ``"{provider_key}:{model}"`` where
        ``<model>`` is the bare upstream ``actual_model`` (e.g. ``moss-asr``).
      - byok / env → the user's raw assignment string (bare like ``whisper-1``
        or prefixed like ``openai:whisper-1``).

    Resolution: take the part after the first ``:`` (bare names pass through
    unchanged — the split is idempotent for an already-bare name); if that is
    empty fall back to ``provider_config["model"]``; if still empty use
    ``default``. Both ASR branches share this so the user's Settings pick can
    never be silently dropped in favour of a hardcoded default.
    """
    model = (
        task_assignment.split(":", 1)[1] if ":" in task_assignment else task_assignment
    )
    if not model:
        model = provider_config.get("model") or ""
    return model or default


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
    from app.services.library.media_storage import materialize

    # Honor the user's Settings → AI → Transcription model pick. Without this
    # the branch dropped `task_assignment` on the floor and WhisperService
    # defaulted to 'whisper-1', so a nous:<model> / BYOK pick reached the
    # provider as 'whisper-1' → 404 "no active grant for service 'whisper-1'".
    # Mirrors the volcengine branch's derivation via the shared helper.
    whisper_model = _assignment_model(task_assignment, provider_config, "whisper-1")

    svc = WhisperService(provider_key=provider_key, provider_config=provider_config)
    # WhisperService ultimately opens audio_path as a local file to upload to
    # the provider. materialize() is a no-op passthrough for filesystem rows
    # (yields the real path) and streams sb:// rows to a deleted-on-exit temp
    # file — so this line is the only change needed to make the upload path
    # object-store aware (Task C4, storage-full-s3-migration PR-1).
    async with materialize(audio_path) as local_audio:
        result = await svc.transcribe_and_save(
            resource_id=resource_id,
            audio_path=str(local_audio),
            language=language,
            whisper_model=whisper_model,
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
    the file rather than receiving an upload. Two source shapes:

    - filesystem row: reuses the HMAC-signed media token (app.api.media_auth)
      so the /media route URL is short-lived (1h) and tied to the calling
      user. Ported verbatim from master's app.tasks.ai_tasks.transcribe_audio_task
      (the volcengine branch). The DBOS port had previously dropped this.
    - sb:// row (object store): the /media route doesn't serve these, so we
      hand Volcengine a Supabase Storage signed URL instead. The signed URL
      is built against the LAN-facing SUPABASE_URL, which Volcengine's cloud
      side cannot reach — the scheme+host is swapped for
      STORAGE_SIGNED_URL_PUBLIC_BASE (identical host-swap logic to
      media_serving.py::serve_stored_file). Task C4, storage-full-s3-migration
      PR-1.
    """
    import time as time_mod
    from urllib.parse import urlsplit, urlunsplit

    from app.api.media_auth import _sign_token
    from app.core.config import settings
    from app.services.ai.transcribe.volcengine_asr_service import (
        RESOURCE_V1,
        RESOURCE_V2,
        VolcengineASRService,
    )
    from app.services.library.media_storage import ObjectStore, resolve_media_source

    loc = resolve_media_source(audio_path)
    if loc.is_object_store:
        public_base = (settings.STORAGE_SIGNED_URL_PUBLIC_BASE or "").strip()
        if not public_base:
            raise RuntimeError(
                "volcengine ASR needs a publicly-reachable signed URL base "
                "for object-store audio sources, but "
                "STORAGE_SIGNED_URL_PUBLIC_BASE is not configured — refusing "
                "to build a LAN-only URL Volcengine's cloud side can't reach"
            )
        store = ObjectStore(loc.bucket)
        signed = await store.signed_url(loc.key, ttl_seconds=3600)
        parts = urlsplit(signed)
        base = urlsplit(public_base.rstrip("/"))
        audio_url = urlunsplit((base.scheme, base.netloc, parts.path, parts.query, ""))
        ext = os.path.splitext(loc.key)[1].lstrip(".").lower()
    else:
        # 解析到磁盘路径,以便下面推导对外 URL。
        from app.services.media.audio_source import AudioSourceResolver

        _resolver = AudioSourceResolver()
        resolved_path = _resolver.resolve(audio_path)

        # Pull the bound user id from the provider_config caller
        # (load_transcribe_inputs passes provider_config without user_id; we
        # need to recover it from the signed-token chain). The simplest path:
        # read the resource's creator_id. Avoids threading user_id through
        # every step signature. Only needed for the /media token — the
        # object-store branch above has no equivalent per-user gate.
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

        media_public_url = getattr(settings, "MEDIA_PUBLIC_URL", "https://cn.nous.ink:88")
        # The /media route serves files by path relative to the download root.
        rel_path = _resolver.to_relative(resolved_path)
        audio_url = f"{media_public_url}/media/{rel_path}?token={media_token}"

        ext = os.path.splitext(resolved_path)[1].lstrip(".").lower()

    audio_format = ext if ext in ("mp3", "wav", "ogg") else "wav"

    # Pick API resource based on the model the user assigned in
    # Settings → AI → Transcription, e.g. 'volcengine:bigasr' or
    # 'volcengine:seed-asr'. master derived this from
    # transcription_assignment; we now thread the same string through
    # task_assignment. Falls back to V1 (bigasr) since most accounts
    # only have that resource granted (V2 seed-asr requires a separate
    # entitlement and returns 45000030 'resource not granted' otherwise).
    # Shared derivation with the whisper branch. default="" preserves the
    # prior behavior exactly (bigasr/V1 is picked when nothing resolves).
    model_part = _assignment_model(task_assignment, provider_config, "")
    asr_resource = (
        RESOURCE_V2
        if ("seed" in model_part.lower() or "2.0" in model_part)
        else RESOURCE_V1
    )

    # NOTE: volcengine hotwords use a different mechanism (a server-side
    # boosting / hot-word table keyed to the account) — not the OpenAI
    # prompt/context knob — so provider_config["hotwords"] (if the user set any)
    # is intentionally NOT forwarded here. Wiring it up is a separate task.
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


@DBOS.step()
async def mark_transcript_failed(parsed_media_id: int) -> None:
    """Flip resources.transcript_status='failed' so the frontend Transcript
    tab stops its local "Transcribing..." spinner and surfaces the failure.

    Without this the workflow only marks task_tracking failed (via
    record_workflow_failure); the resource's transcript_status stays 'none'
    and VideoDetailPanel polls forever. Best-effort — a transient write
    hiccup here must not mask the real error we're about to record."""
    from app.db import engine as db_engine

    try:
        await db_engine.execute(
            "UPDATE public.resources SET transcript_status = 'failed' "
            "WHERE media_id = :pid AND transcript_status <> 'completed'",
            {"pid": parsed_media_id},
        )
    except Exception as e:
        logger.warning(
            f"[ai_transcription] transcript_status='failed' write for "
            f"parsed_media_id={parsed_media_id} failed (non-fatal): {e}"
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
        audio_path = await assert_audio_present_step(inputs["audio_path"])
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
        # Surface the failure on the resource so the frontend Transcript
        # tab stops spinning. Business column (route-C rule 3), written by
        # business code — not a trigger-owned task_tracking column.
        await mark_transcript_failed(parsed_media_id)
        return await record_workflow_failure(
            workflow_id=DBOS.workflow_id,
            error=e,
            context={
                "workflow": "ai_transcription",
                "parsed_media_id": parsed_media_id,
                "user_id": user_id,
            },
        )
