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

from app.workflows.agent_workforce import (  # noqa: F401
    agent_workforce_queue,
    agent_workforce_workflow,
)
from app.workflows.ai_summary import ai_summary_workflow  # noqa: F401
from app.workflows.ai_transcription import ai_transcription_workflow  # noqa: F401
from app.workflows.analyze_l1 import analyze_l1_workflow  # noqa: F401
from app.workflows.download import download_workflow  # noqa: F401
from app.workflows.extract_audio import extract_audio_workflow  # noqa: F401
from app.workflows.issue_lifecycle import (  # noqa: F401
    execute_issue,
    respond_to_issue_reply,
)
from app.workflows.parse import parse_user_queue, parse_workflow  # noqa: F401
from app.workflows.script_outline import script_outline_workflow  # noqa: F401
from app.workflows.soda_download import (  # noqa: F401
    soda_download_queue,
    soda_download_workflow,
)
from app.workflows.soda_ugc_download import soda_ugc_download_workflow  # noqa: F401
from app.workflows.storyboard import (  # noqa: F401
    storyboard_annotation_workflow,
    storyboard_export_workflow,
    storyboard_image_batch_workflow,
    storyboard_image_grid_split_workflow,
    storyboard_image_workflow,
    storyboard_script_split_workflow,
    storyboard_video_analysis_workflow,
    storyboard_video_workflow,
)
from app.workflows.thumbnail import thumbnail_workflow  # noqa: F401
from app.workflows.transcode import transcode_workflow  # noqa: F401
from app.workflows.write_memory import write_memory_workflow  # noqa: F401
