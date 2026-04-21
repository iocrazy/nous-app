"""
Volcengine Seed-ASR service — 火山引擎录音文件识别大模型

Async submit → poll workflow:
1. POST /submit  → submit audio URL for recognition
2. POST /query   → poll until result is ready

Supports both old console (App-Key + Access-Key) and new console (X-Api-Key).
Supports model 1.0 (volc.bigasr.auc) and 2.0 (volc.seedasr.auc).

Docs: https://www.volcengine.com/docs/6561/1354868
"""

import asyncio
import uuid
from typing import Optional

import httpx
from loguru import logger

from app.repositories.ai_repository import AIRepository
from app.services.ai_provider import TranscriptResult, TranscriptSegment

SUBMIT_URL = "https://openspeech.bytedance.com/api/v3/auc/bigmodel/submit"
QUERY_URL = "https://openspeech.bytedance.com/api/v3/auc/bigmodel/query"

# Resource IDs (model versions)
RESOURCE_V1 = "volc.bigasr.auc"  # 豆包录音文件识别模型 1.0
RESOURCE_V2 = "volc.seedasr.auc"  # 豆包录音文件识别模型 2.0


class VolcengineASRService:
    """火山引擎 Seed-ASR 录音文件识别服务

    Authentication modes:
    - New console: only api_key → sent as X-Api-Key header
    - Old console: app_id + api_key → sent as X-Api-App-Key + X-Api-Access-Key
    """

    def __init__(
        self,
        app_id: str = "",
        access_token: str = "",
        asr_resource_id: str = RESOURCE_V2,
    ):
        self._app_id = app_id
        self._access_token = access_token
        self._asr_resource_id = asr_resource_id
        self._repo = AIRepository()

    def _build_headers(self, request_id: str, include_sequence: bool = False) -> dict:
        headers = {
            "Content-Type": "application/json",
            "X-Api-Resource-Id": self._asr_resource_id,
            "X-Api-Request-Id": request_id,
        }

        if self._app_id:
            # Old console: App-Key + Access-Key
            headers["X-Api-App-Key"] = self._app_id
            headers["X-Api-Access-Key"] = self._access_token
        else:
            # New console: single X-Api-Key
            headers["X-Api-Key"] = self._access_token

        if include_sequence:
            headers["X-Api-Sequence"] = "-1"
        return headers

    async def transcribe(
        self,
        audio_url: str,
        audio_format: str = "mp3",
        max_poll_seconds: int = 300,
        poll_interval: int = 3,
    ) -> TranscriptResult:
        """Transcribe audio via Volcengine Seed-ASR.

        Args:
            audio_url: Publicly accessible URL of the audio file.
            audio_format: Audio format (mp3, wav, ogg).
            max_poll_seconds: Maximum time to wait for result.
            poll_interval: Seconds between poll requests.

        Returns:
            TranscriptResult with text, segments, language, duration.
        """
        request_id = str(uuid.uuid4())

        # 1. Submit task
        submit_payload = {
            "user": {"uid": "mediahub"},
            "audio": {
                "format": audio_format,
                "url": audio_url,
            },
            "request": {
                "model_name": "bigmodel",
                "enable_itn": True,
                "enable_punc": True,
                "enable_ddc": True,
            },
        }

        async with httpx.AsyncClient(timeout=30) as client:
            submit_headers = self._build_headers(request_id, include_sequence=True)
            auth_mode = "X-Api-Key" if not self._app_id else "App-Key+Access-Key"
            logger.info(
                f"[VolcASR] Submitting ({auth_mode}, resource={self._asr_resource_id}): "
                f"{audio_url[:80]}..."
            )
            resp = await client.post(
                SUBMIT_URL,
                headers=submit_headers,
                json=submit_payload,
            )

            status_code = resp.headers.get("X-Api-Status-Code", "")
            if status_code != "20000000" and resp.status_code != 200:
                error_msg = resp.headers.get("X-Api-Message", resp.text[:200])
                raise RuntimeError(
                    f"Volcengine ASR submit failed: {status_code} {error_msg}"
                )

            logger.info(f"[VolcASR] Submitted, request_id={request_id}")

            # 2. Poll for result
            elapsed = 0
            while elapsed < max_poll_seconds:
                await asyncio.sleep(poll_interval)
                elapsed += poll_interval

                query_resp = await client.post(
                    QUERY_URL,
                    headers=self._build_headers(request_id),
                    json={},
                )

                resp_status = query_resp.headers.get("X-Api-Status-Code", "")

                if resp_status == "20000000":
                    # Success
                    result_data = query_resp.json()
                    logger.info(f"[VolcASR] Completed in {elapsed}s")
                    return self._parse_result(result_data)
                elif resp_status in ("20000001", "20000002"):
                    # Still processing
                    logger.debug(f"[VolcASR] Still processing... ({elapsed}s)")
                    continue
                else:
                    error_msg = query_resp.headers.get("X-Api-Message", "")
                    raise RuntimeError(
                        f"Volcengine ASR query failed: {resp_status} {error_msg}"
                    )

            raise TimeoutError(f"Volcengine ASR timed out after {max_poll_seconds}s")

    def _parse_result(self, data: dict) -> TranscriptResult:
        """Parse Volcengine ASR response into TranscriptResult."""
        result = data.get("result", {})
        full_text = result.get("text", "")
        utterances = result.get("utterances", [])

        segments = []
        for utt in utterances:
            segments.append(
                TranscriptSegment(
                    start=utt.get("start_time", 0) / 1000.0,
                    end=utt.get("end_time", 0) / 1000.0,
                    text=utt.get("text", ""),
                )
            )

        # Estimate duration from last segment
        duration = segments[-1].end if segments else 0.0

        return TranscriptResult(
            text=full_text,
            segments=segments,
            language=result.get("language", "zh"),
            duration=duration,
        )

    async def transcribe_and_save(
        self,
        resource_id: str,
        audio_url: str,
        audio_format: str = "mp3",
    ) -> Optional[TranscriptResult]:
        """Transcribe and persist result to database.

        Args:
            resource_id: ID of the resource to associate the transcript with.
            audio_url: Publicly accessible URL of the audio file.
            audio_format: Audio format (mp3, wav, ogg).
        """
        try:
            result = await self.transcribe(audio_url, audio_format)

            segments_json = [
                {"start": s.start, "end": s.end, "text": s.text}
                for s in result.segments
            ]

            await self._repo.save_transcript(
                resource_id,
                {
                    "language": result.language,
                    "full_text": result.text,
                    "segments": segments_json,
                    "whisper_model": f"volcengine-{self._asr_resource_id}",
                    "duration_seconds": result.duration,
                },
            )

            logger.info(f"[VolcASR] Transcript saved for resource {resource_id}")
            return result

        except Exception as e:
            logger.error(f"[VolcASR] Failed for resource {resource_id}: {e}")
            raise
