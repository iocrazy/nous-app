"""Scheduled workflows + sweepers that ONLY the worker role should register.

Importing this module triggers @DBOS.scheduled decorators, which install
cron entries on the running process. Run it on the gateway and the
gateway will start firing those schedules — that is exactly what we
do NOT want (2026-05-27 restart-loop investigation).
"""

from __future__ import annotations

from app.workflows.agent_runs_sweeper import agent_runs_sweeper_workflow  # noqa: F401
from app.workflows.liveness_scanner import (  # noqa: F401
    liveness_scan_scheduled,
    reconcile_stranded_runs,
)
from app.workflows.scheduled_cleanup import (  # noqa: F401
    cleanup_old_task_tracking_workflow,
    cleanup_temp_files_workflow,
    cleanup_trashed_resources_workflow,
)
from app.workflows.scheduled_commitment_sweeper import (  # noqa: F401
    commitment_sweeper_workflow,
)
from app.workflows.scheduled_health import (  # noqa: F401
    health_check_workflow,
    update_system_status_workflow,
)
from app.workflows.scheduled_master import scheduled_master_workflow  # noqa: F401
from app.workflows.scheduled_memory_archival import (  # noqa: F401
    memory_archival_workflow,
)
from app.workflows.scheduled_memory_consolidation import (  # noqa: F401
    memory_consolidation_workflow,
)
from app.workflows.scheduled_quotas import (  # noqa: F401
    grant_daily_free_points_workflow,
    reclaim_daily_free_points_workflow,
    reset_monthly_quotas_workflow,
)
from app.workflows.scheduled_recovery import (  # noqa: F401
    reap_stuck_pending_tasks_workflow,
    recover_stale_orchestrator_locks_workflow,
    retry_failed_downloads_workflow,
)
from app.workflows.temp_resource_sweeper import (  # noqa: F401
    sweep_temp_resources,
    temp_resource_sweeper_scheduled,
)
from app.workflows.workflow_health_sweeper import (  # noqa: F401
    workflow_health_sweeper_workflow,
)
from app.workflows.workforce_dispatch import (  # noqa: F401
    inbox_dispatch_workflow,
    outbox_dispatch_workflow,
)
