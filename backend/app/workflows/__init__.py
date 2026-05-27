"""DBOS workflow registry — role-aware to avoid scheduler leakage.

Importing this package registers workflows with the DBOS singleton
(decorators run at import time). The gateway role registers only
dispatch-needed callables; the worker (and the legacy `combined` role)
registers scheduled workflows as well. See the 2026-05-27 restart-loop
investigation for the why.
"""

from __future__ import annotations

from app.agent_framework.role import ProcessRole, role_from_env
from app.workflows._dispatch_bundle import *  # noqa: F401,F403

# Worker + combined roles also register the scheduled bucket. Gateway
# stays minimal so its scheduler thread has nothing to fire. Routed
# through role_from_env() so an unknown MEDIAHUB_ROLE value warns and
# falls back to combined instead of silently treating it as gateway.
if role_from_env() != ProcessRole.GATEWAY:
    from app.workflows._scheduled_bundle import *  # noqa: F401,F403
