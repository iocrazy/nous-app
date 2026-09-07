"""Workflows the gateway must register because dispatch hands them out.

Every @DBOS.workflow whose callable appears in a Router → Service →
start_workflow_routed() call MUST be importable on the dispatching
process. Scheduled-only workflows (those decorated with @DBOS.scheduled
and never started directly) belong in _scheduled_bundle instead.

Split rationale (2026-05-27): gateway processes were re-registering every
@DBOS.scheduled callable, causing the scheduler thread to fire schedules
that only the worker container should run, leading to a CANCELLED-storm
restart loop. Splitting by role makes the gateway invisible to scheduled
ticks while preserving its ability to dispatch user-triggered work.
"""

from __future__ import annotations

from dbos import Queue

from app.workflows.agent_workforce import (  # noqa: F401
    agent_workforce_queue,
    agent_workforce_workflow,
)
from app.workflows.ai_summary import ai_summary_workflow  # noqa: F401
from app.workflows.ai_transcription import ai_transcription_workflow  # noqa: F401
from app.workflows.analyze_l1 import analyze_l1_workflow  # noqa: F401
from app.workflows.autopilot import autopilot_tick  # noqa: F401
from app.workflows.backfill_issue_scope import (  # noqa: F401
    backfill_issue_scope_workflow,
)
from app.workflows.backfill_normalize_personal_project_team_ids import (  # noqa: F401
    backfill_normalize_personal_project_team_ids_workflow,
)
from app.workflows.backfill_project_stage_issue_team_ids import (  # noqa: F401
    backfill_project_stage_issue_team_ids_workflow,
)
from app.workflows.backfill_publish_task_team_ids import (  # noqa: F401
    backfill_publish_task_team_ids_workflow,
)
from app.workflows.canvas_generation import (  # noqa: F401
    canvas_generation_workflow,
)
from app.workflows.caption_asset import caption_asset_workflow  # noqa: F401
from app.workflows.caption_slide import caption_slide_workflow  # noqa: F401
from app.workflows.classify_asset import classify_asset_workflow  # noqa: F401
from app.workflows.cover_frames import cover_frames_workflow  # noqa: F401
from app.workflows.download import (  # noqa: F401
    download_user_queue,
    download_workflow,
)
from app.workflows.extract_audio import extract_audio_workflow  # noqa: F401
from app.workflows.issue_lifecycle import (  # noqa: F401
    execute_issue,
    respond_to_issue_reply,
)
from app.workflows.parse import parse_user_queue, parse_workflow  # noqa: F401
from app.workflows.publish_distribution import (  # noqa: F401
    publish_distribution_workflow,
)
from app.workflows.script_ai_workflows import (  # noqa: F401
    script_create_branches_workflow,
    script_expand_chapter_workflow,
)
from app.workflows.script_import import script_import_workflow  # noqa: F401
from app.workflows.script_outline import script_outline_workflow  # noqa: F401
from app.workflows.script_scene_convert import (  # noqa: F401
    script_scene_convert_workflow,
)
from app.workflows.script_shot_breakdown import (  # noqa: F401
    script_shot_breakdown_workflow,
)
from app.workflows.script_shot_generate import (  # noqa: F401
    script_shot_generate_workflow,
)
from app.workflows.script_shot_video import (  # noqa: F401
    script_shot_video_workflow,
)
from app.workflows.session_login import session_login_workflow  # noqa: F401
from app.workflows.soda_download import (  # noqa: F401
    soda_download_queue,
    soda_download_workflow,
)
from app.workflows.soda_ugc_download import soda_ugc_download_workflow  # noqa: F401
from app.workflows.stage_hook import stage_hook_dispatch  # noqa: F401
from app.workflows.storage_audit import storage_audit_workflow  # noqa: F401
from app.workflows.storage_migration import (  # noqa: F401
    storage_migration_workflow,
)
from app.workflows.thumbnail import (  # noqa: F401
    thumbnail_backfill_workflow,
    thumbnail_workflow,
)
from app.workflows.transcode import transcode_workflow  # noqa: F401
from app.workflows.upload_postprocess import (  # noqa: F401
    upload_postprocess_workflow,
)
from app.workflows.write_memory import write_memory_workflow  # noqa: F401

# Shared NON-partitioned dispatch queue for the gateway → DBOSClient migration.
# Workflows that today run in-process via DBOS.start_workflow (transcode / ai_* /
# script / issue / memory) will be enqueued here once the gateway
# goes enqueue-only. Declared in the dispatch bundle so the WORKER (which imports
# this module) registers a poller for it. ADDITIVE + dormant: nothing enqueues to
# it yet. Non-partitioned (no per-user key) — it is a global dispatch lane.
dbos_dispatch = Queue("dbos_dispatch")
