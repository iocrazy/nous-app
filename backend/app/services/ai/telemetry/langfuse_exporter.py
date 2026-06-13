"""Langfuse trace exporter (Phase 4.5-6 — canvas plan).

Ships one trace + one generation per finished agent run to the
self-hosted Langfuse (NAS, see reference_langfuse_nas) via the public
ingestion API — plain httpx, no langfuse SDK dependency. RunRecorder
already aggregates everything worth tracing (model, tokens, cost,
summaries), so the exporter is a thin fire-and-forget mapper on top of
the run it just finished.

Configuration (env, default OFF):
    FEATURE_LANGFUSE      — master flag
    LANGFUSE_HOST         — e.g. http://192.168.50.9:3100
    LANGFUSE_PUBLIC_KEY   — project public key (pk-lf-…)
    LANGFUSE_SECRET_KEY   — project secret key (sk-lf-…)

Safety contract (same as every telemetry path): never raises, never
blocks the caller — RunRecorder schedules ``export_run`` as a background
task and a Langfuse outage only costs the trace.
"""

from __future__ import annotations

import logging
import os
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Optional

import httpx

logger = logging.getLogger(__name__)

_TRUTHY = {"1", "true", "yes", "on"}
_REQUEST_TIMEOUT_S = 10.0


@dataclass(frozen=True)
class LangfuseConfig:
    enabled: bool = False
    host: str = ""
    public_key: str = ""
    secret_key: str = ""

    @classmethod
    def from_env(cls) -> "LangfuseConfig":
        return cls(
            enabled=os.getenv("FEATURE_LANGFUSE", "").strip().lower() in _TRUTHY,
            host=os.getenv("LANGFUSE_HOST", "").strip().rstrip("/"),
            public_key=os.getenv("LANGFUSE_PUBLIC_KEY", "").strip(),
            secret_key=os.getenv("LANGFUSE_SECRET_KEY", "").strip(),
        )

    def operative(self) -> bool:
        return self.enabled and bool(self.host and self.public_key and self.secret_key)


@dataclass
class LangfuseExporter:
    """Lazy httpx client; inject ``client`` in tests."""

    config: LangfuseConfig = field(default_factory=LangfuseConfig.from_env)
    client: Optional[httpx.AsyncClient] = None

    def _get_client(self) -> Optional[httpx.AsyncClient]:
        if not self.config.operative():
            return None
        if self.client is None:
            self.client = httpx.AsyncClient(
                base_url=self.config.host,
                auth=(self.config.public_key, self.config.secret_key),
                timeout=_REQUEST_TIMEOUT_S,
            )
        return self.client

    async def export_run(
        self,
        *,
        run_id: str,
        agent_slug: str,
        status: str,
        trigger: str,
        user_id: str,
        session_id: Optional[str],
        model: Optional[str],
        provider: Optional[str],
        input_summary: Optional[str],
        output_summary: Optional[str],
        prompt_tokens: int,
        completion_tokens: int,
        cost_cents: float,
        error_message: Optional[str] = None,
    ) -> bool:
        """One trace + one generation for a finished run. Returns True
        when Langfuse accepted the batch; False otherwise (and logs)."""
        client = self._get_client()
        if client is None:
            return False
        now = datetime.now(timezone.utc).isoformat()
        trace_id = f"run-{run_id}"
        metadata = {
            "trigger": trigger,
            "provider": provider,
            "status": status,
            "agent_run_id": run_id,
        }
        if error_message:
            metadata["error"] = error_message[:500]
        batch = [
            {
                "id": str(uuid.uuid4()),
                "type": "trace-create",
                "timestamp": now,
                "body": {
                    "id": trace_id,
                    "name": agent_slug or trigger,
                    "userId": user_id,
                    "sessionId": session_id,
                    "input": input_summary,
                    "output": output_summary,
                    "metadata": metadata,
                    "tags": [trigger, status],
                },
            },
            {
                "id": str(uuid.uuid4()),
                "type": "generation-create",
                "timestamp": now,
                "body": {
                    "id": f"gen-{run_id}",
                    "traceId": trace_id,
                    "name": "agent-turn",
                    "model": model,
                    "usage": {
                        "input": prompt_tokens,
                        "output": completion_tokens,
                        "totalCost": round(cost_cents / 100.0, 6),
                    },
                    "level": "ERROR" if status == "failed" else "DEFAULT",
                    "statusMessage": error_message,
                },
            },
        ]
        try:
            response = await client.post("/api/public/ingestion", json={"batch": batch})
            if response.status_code not in (200, 201, 207):
                logger.warning(
                    "[langfuse] ingestion -> %s: %s",
                    response.status_code,
                    response.text[:200],
                )
                return False
            return True
        except Exception:  # noqa: BLE001
            logger.warning("[langfuse] export failed (non-fatal)", exc_info=True)
            return False


_exporter: Optional[LangfuseExporter] = None


def get_langfuse_exporter() -> LangfuseExporter:
    """Process-wide singleton, configured from env on first use."""
    global _exporter
    if _exporter is None:
        _exporter = LangfuseExporter()
    return _exporter


__all__ = ["LangfuseConfig", "LangfuseExporter", "get_langfuse_exporter"]
