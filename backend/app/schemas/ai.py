# backend/app/schemas/ai.py

"""
AI settings request/response schemas.
"""

from typing import Any, Dict, List, Optional

from pydantic import BaseModel, Field


class AISettingsUpdate(BaseModel):
    """Request body for saving AI settings."""

    ai_providers: Optional[Dict[str, Any]] = Field(
        default=None,
        description="Provider configs keyed by provider name, e.g. {'openai': {'api_key': '...', 'model': '...'}}",
    )
    whisper_provider: Optional[str] = Field(
        default=None,
        description="Whisper provider: 'openai_api' or 'local'",
    )
    default_summary_model: Optional[str] = Field(
        default=None,
        description="Default LLM model key for summaries",
    )
    default_analysis_model: Optional[str] = Field(
        default=None,
        description="Default LLM model key for visual analysis",
    )
    # Frontend-specific fields persisted for UI state
    ai_enabled: Optional[bool] = None
    preferred_language: Optional[str] = None
    task_assignment: Optional[Dict[str, str]] = Field(
        default=None,
        description=(
            "Task-to-handler routing. As of migration 142 (Phase 2 PR 2.8b), "
            "the summarization / visual_analysis / script_generation keys "
            "store an AI Library agent slug (e.g. 'summarize', 'analyze', "
            "'storyboard'). The transcription key still stores a legacy "
            "'provider:model' string (e.g. 'volcengine:bigasr') because "
            "transcription does not route through the agent framework."
        ),
    )


class AISettingsResponse(BaseModel):
    """Response body for AI settings.

    Returns the full settings blob so the frontend can render
    providers, task_assignment, toggles, etc. without field mapping.
    """

    ai_providers: Dict[str, Any] = Field(default_factory=dict)
    whisper_provider: str = "openai_api"
    default_summary_model: str = "gpt-4o-mini"
    default_analysis_model: str = "gpt-4o"
    # Frontend-consumed fields
    ai_enabled: bool = True
    preferred_language: str = "auto"
    task_assignment: Dict[str, str] = Field(
        default_factory=lambda: {
            "transcription": "",
            "summarization": "",
            "visual_analysis": "",
        },
        description=(
            "Task-to-handler routing. As of migration 142 (Phase 2 PR 2.8b), "
            "summarization / visual_analysis / script_generation hold an "
            "AI Library agent slug (e.g. 'summarize', 'analyze', 'storyboard'). "
            "transcription remains a 'provider:model' string."
        ),
    )


class TestConnectionRequest(BaseModel):
    """Request body for testing an AI provider connection."""

    provider_key: str = Field(
        ...,
        description="Provider key: openai, deepseek, doubao, volcengine, ollama, lmstudio",
    )
    api_key: Optional[str] = Field(default="", description="API key for the provider")
    app_id: Optional[str] = Field(default="", description="App ID (for volcengine)")
    base_url: Optional[str] = Field(default="", description="Custom base URL")
    model: Optional[str] = Field(default="", description="Model to use")


class TestConnectionResponse(BaseModel):
    """Response body for connection test."""

    success: bool
    models: Optional[List[str]] = None
    error: Optional[str] = None


# ------------------------------------------------------------------
# Transcript / Summary response schemas (for API endpoints)
# ------------------------------------------------------------------


class TranscriptSegmentSchema(BaseModel):
    """A single timed segment from a transcript."""

    start: float
    end: float
    text: str


class TranscriptResponse(BaseModel):
    """Response body for a video transcript."""

    media_id: str
    language: Optional[str] = None
    full_text: Optional[str] = None
    segments: Optional[List[TranscriptSegmentSchema]] = None
    whisper_model: Optional[str] = None
    duration_seconds: Optional[float] = None
    created_at: Optional[str] = None


class SummaryResponse(BaseModel):
    """Response body for a video summary."""

    media_id: str
    summary_type: Optional[str] = None
    summary_text: Optional[str] = None
    key_points: Optional[List[str]] = None
    topics: Optional[List[str]] = None
    llm_model: Optional[str] = None
    llm_provider: Optional[str] = None
    created_at: Optional[str] = None
