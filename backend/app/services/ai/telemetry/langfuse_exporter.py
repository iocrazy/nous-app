"""Langfuse trace exporter (Phase 4.5-6 — canvas plan).

Ships one trace + one generation per finished agent run to the
self-hosted Langfuse (NAS, see reference_langfuse_nas) via the public
ingestion API — plain httpx, no langfuse SDK dependency. RunRecorder
already aggregates everything worth tracing (model, tokens, cost,
summaries), so the exporter is a thin fire-and-forget mapper on top of
the run it just finished.

Configuration (env, default OFF; bootstrap fallback — see from_settings):
    FEATURE_LANGFUSE      — master flag
    LANGFUSE_HOST         — e.g. http://192.168.50.9:3100
    LANGFUSE_PUBLIC_KEY   — project public key (pk-lf-…)
    LANGFUSE_SECRET_KEY   — project secret key (sk-lf-…)

env→DB migration wave 2 (mirrors HonchoMemoryConfig / GraphMemoryConfig):
the admin-manageable + secret-bearing config now lives in system_settings
(``telemetry.langfuse.*``, DB wins when present) with the env vars above as
the bootstrap fallback — see ``from_settings``.

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

# system_settings key (admin-set, DB) → env-var fallback. Lets the Langfuse
# gate + connection config be configured from an admin UI instead of editing
# prod compose env (which Watchtower does not reload). Mirrors
# _HONCHO_SETTINGS_MAP / graph_memory.py's _SETTINGS_MAP.
_LANGFUSE_SETTINGS_MAP: dict[str, tuple[str, str]] = {
    "enabled": ("telemetry.langfuse.enabled", "FEATURE_LANGFUSE"),
    "host": ("telemetry.langfuse.host", "LANGFUSE_HOST"),
    "public_key": ("telemetry.langfuse.public_key", "LANGFUSE_PUBLIC_KEY"),
    "secret_key": ("telemetry.langfuse.secret_key", "LANGFUSE_SECRET_KEY"),
}


async def _langfuse_settings_reader(key: str) -> Optional[object]:
    """Read one system_settings JSONB value (service-role engine). None on
    miss/error. Returns the native JSONB-deserialised value — a stored JSON
    ``true`` comes back as Python ``True``, not the string ``"true"``.

    SECRETS: the value passes through ``secure_settings.reveal`` before
    returning — a no-op for ``enabled``/``host``, but transparently decrypts
    ``telemetry.langfuse.public_key`` / ``telemetry.langfuse.secret_key``
    (encrypted at write time by ``SystemSettingsRepository``). Fail-soft —
    see ``reveal``'s docstring."""
    try:
        from app.core.secure_settings import reveal
        from app.db import engine as db_engine

        if not db_engine.is_configured():
            return None
        value = await db_engine.fetch_val(
            "SELECT value FROM public.system_settings WHERE key = :k", {"k": key}
        )
        return reveal(value) if value is not None else None
    except Exception:  # noqa: BLE001 — settings read must never raise
        logger.warning(f"[langfuse] system_settings read failed: {key}")
        return None


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

    @classmethod
    async def from_settings(cls, *, reader=None, env=None) -> "LangfuseConfig":
        """Resolve from system_settings with per-field env fallback (DB > env >
        default). Behaviour-neutral when the telemetry.langfuse.* keys are
        absent: every field falls back to the same env var from_env() reads.
        Never raises."""
        read = reader if reader is not None else _langfuse_settings_reader
        environ = env if env is not None else os.environ

        async def read_db(field_key: str):
            db_key, _env_key = _LANGFUSE_SETTINGS_MAP[field_key]
            try:
                return await read(db_key)
            except Exception:  # noqa: BLE001
                logger.warning(f"[langfuse] settings read failed: {db_key}")
                return None

        async def resolve_str(field_key: str) -> str:
            db_val = await read_db(field_key)
            if db_val is not None and str(db_val).strip():
                return str(db_val).strip()
            _db_key, env_key = _LANGFUSE_SETTINGS_MAP[field_key]
            env_val = environ.get(env_key)
            return env_val.strip() if isinstance(env_val, str) else ""

        async def resolve_enabled() -> bool:
            db_val = await read_db("enabled")
            if isinstance(db_val, bool):
                return db_val  # native JSONB bool — including explicit False
            if isinstance(db_val, str) and db_val.strip():
                return db_val.strip().lower() in _TRUTHY
            _db_key, env_key = _LANGFUSE_SETTINGS_MAP["enabled"]
            env_val = environ.get(env_key)
            return isinstance(env_val, str) and env_val.strip().lower() in _TRUTHY

        enabled = await resolve_enabled()
        host = (await resolve_str("host")).rstrip("/")
        public_key = await resolve_str("public_key")
        secret_key = await resolve_str("secret_key")
        return cls(
            enabled=enabled, host=host, public_key=public_key, secret_key=secret_key
        )

    def operative(self) -> bool:
        return self.enabled and bool(self.host and self.public_key and self.secret_key)


@dataclass
class LangfuseExporter:
    """Lazy httpx client; inject ``client`` in tests."""

    config: LangfuseConfig = field(default_factory=LangfuseConfig.from_env)
    client: Optional[httpx.AsyncClient] = None
    # Whether config has been (re)loaded from system_settings. The env-sourced
    # default_factory keeps construction cheap; the DB load happens once on
    # first real async use via _ensure_config — mirrors HonchoMemoryService /
    # GraphMemoryService.
    _config_loaded: bool = False

    async def _ensure_config(self) -> None:
        """Swap the env-default config for the DB-sourced one on first use.
        Skipped when a client is injected (tests set their own config) or
        after the first load. Never raises — a failed settings load keeps env
        config."""
        if self.client is not None or self._config_loaded:
            return
        self._config_loaded = True  # set first: no retry-storm, no double-load
        try:
            self.config = await LangfuseConfig.from_settings()
        except Exception:  # noqa: BLE001
            logger.warning("[langfuse] from_settings failed; keeping env config")

    async def reload(self) -> None:
        """Drop the cached httpx client + config-loaded flag so the next call
        re-reads system_settings and reconnects with fresh host/keys — mirrors
        HonchoMemoryService/GraphMemoryService reload semantics (an admin
        config edit applies without a process restart). Never raises."""
        if self.client is not None:
            try:
                await self.client.aclose()
            except Exception:  # noqa: BLE001
                logger.warning("[langfuse] client close failed during reload")
            self.client = None
        self._config_loaded = False

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
        await self._ensure_config()
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
