"""Process role — gateway / worker / combined.

Sprint 5 primitive. Today the FastAPI process serves HTTP requests AND
runs the DBOS worker pool in the same Python interpreter. That works
for a single-NAS deployment but creates two practical problems:

1. Long-running worker tasks (transcription, video analysis) compete
   for the same event loop / GIL as HTTP request handlers. A spike in
   worker activity stalls API latency.
2. Crashes / OOMs in worker code take down HTTP serving with them.

Splitting the process physically into a "gateway" container (FastAPI
only, no DBOS pool) and a "worker" container (DBOS pool only, minimal
HTTP for health/bounds) isolates these failure modes. Both still share
the same DBOS PG queue, so dispatch is unchanged — only the consumer
side moves.

The ``MEDIAHUB_ROLE`` env var picks the mode:
  - ``gateway``  — serve HTTP, do NOT launch DBOS pool (dispatch only)
  - ``worker``   — launch DBOS pool, serve minimal HTTP (/health + /internal/bounds)
  - ``combined`` — both. Default for single-process dev / single-NAS prod.

Default is ``combined`` so existing deployments keep working unchanged
when this primitive lands.
"""
from __future__ import annotations

import os
from enum import Enum


class ProcessRole(str, Enum):
    """How this Python process behaves at startup."""

    GATEWAY = "gateway"
    WORKER = "worker"
    COMBINED = "combined"

    @property
    def serves_http_api(self) -> bool:
        """Should the full /api/v1 router be mounted?"""
        # Worker still serves HTTP for /health + /internal/bounds, but
        # NOT the public API surface. Callers gate on this property.
        return self in (ProcessRole.GATEWAY, ProcessRole.COMBINED)

    @property
    def runs_dbos_workers(self) -> bool:
        """Should DBOS.launch() be called (i.e. consume queue)?"""
        return self in (ProcessRole.WORKER, ProcessRole.COMBINED)

    @property
    def runs_inprocess_schedulers(self) -> bool:
        """Should asyncio-tick schedulers (workforce, sweepers) run here?

        Schedulers are conceptually worker-side: they dispatch jobs by
        polling state. Running them on every gateway replica would
        multiply the load. Combined keeps them on for back-compat.
        """
        return self in (ProcessRole.WORKER, ProcessRole.COMBINED)


_ENV_VAR = "MEDIAHUB_ROLE"


def role_from_env(env: dict[str, str] | None = None) -> ProcessRole:
    """Read MEDIAHUB_ROLE. Falls back to COMBINED for unknown / unset values.

    Defensive: an unrecognized value WARN-logs but does not crash startup
    — ops typo'd config shouldn't take down the whole process. The
    returned default keeps current behavior.
    """
    src = env if env is not None else os.environ
    raw = (src.get(_ENV_VAR) or "").strip().lower()
    if not raw:
        return ProcessRole.COMBINED
    try:
        return ProcessRole(raw)
    except ValueError:
        # Unknown value — log via stderr (logger may not be configured
        # yet during early startup) and fall back to combined.
        import sys

        print(
            f"[agent_framework.role] unrecognized {_ENV_VAR}={raw!r} — "
            f"falling back to {ProcessRole.COMBINED.value}",
            file=sys.stderr,
        )
        return ProcessRole.COMBINED


__all__ = ["ProcessRole", "role_from_env"]
