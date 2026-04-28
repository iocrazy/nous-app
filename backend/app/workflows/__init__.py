"""DBOS workflow registry.

Importing this package registers every workflow with the DBOS singleton
(decorators run at import time). The orchestrator imports this module once
during FastAPI lifespan startup, before calling `DBOS.launch()`.
"""
from __future__ import annotations

# Import workflow modules so their @DBOS.workflow decorators register.
# Order doesn't matter as long as `dbos_orchestrator.init_dbos` ran first.

from app.workflows.issue_lifecycle import execute_issue  # noqa: F401
from app.workflows.ai_summary import ai_summary_workflow  # noqa: F401
from app.workflows.ai_transcription import ai_transcription_workflow  # noqa: F401
from app.workflows.thumbnail import thumbnail_workflow  # noqa: F401
from app.workflows.analyze_l1 import analyze_l1_workflow  # noqa: F401
from app.workflows.script_outline import script_outline_workflow  # noqa: F401
from app.workflows.agent_runs_sweeper import agent_runs_sweeper_workflow  # noqa: F401

__all__ = [
    "execute_issue",
    "ai_summary_workflow",
    "ai_transcription_workflow",
    "thumbnail_workflow",
    "analyze_l1_workflow",
    "script_outline_workflow",
    "agent_runs_sweeper_workflow",
]
