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
    auto_transcribe: Optional[bool] = None
    auto_summarize: Optional[bool] = None
    preferred_language: Optional[str] = None
    task_assignment: Optional[Dict[str, str]] = None


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
    auto_transcribe: bool = False
    auto_summarize: bool = False
    preferred_language: str = "auto"
    task_assignment: Dict[str, str] = Field(
        default_factory=lambda: {
            "transcription": "",
            "summarization": "",
            "visual_analysis": "",
        }
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


# ------------------------------------------------------------------
# AI Agent schemas
# ------------------------------------------------------------------


class AgentOut(BaseModel):
    """Response model for an AI agent."""

    id: str
    name: str
    description: Optional[str] = None
    persona: str
    model: str
    temperature: float
    max_tokens: int
    rules: List[Any] = []
    enabled: bool = True


class AgentCreate(BaseModel):
    """Request model for creating an AI agent."""

    name: str = Field(..., max_length=100)
    description: Optional[str] = Field(None, max_length=500)
    persona: str = Field(..., max_length=5000)
    model: str = "qwen-max"
    temperature: float = Field(0.7, ge=0, le=2)
    max_tokens: int = Field(4096, ge=100, le=32000)
    rules: List[Any] = []
    project_id: Optional[str] = None


class AgentUpdate(BaseModel):
    """Request model for updating an AI agent."""

    name: Optional[str] = Field(None, max_length=100)
    description: Optional[str] = Field(None, max_length=500)
    persona: Optional[str] = Field(None, max_length=5000)
    model: Optional[str] = None
    temperature: Optional[float] = Field(None, ge=0, le=2)
    max_tokens: Optional[int] = Field(None, ge=100, le=32000)
    rules: Optional[List[Any]] = None
    enabled: Optional[bool] = None


class ChatRequest(BaseModel):
    """Request model for sending a chat message."""

    message: str = Field(..., max_length=10000)
    agent_id: Optional[str] = None
    context: Dict[str, Any] = {}


class AgentCallRequest(BaseModel):
    """Request model for calling an agent with a specific action."""

    action: str
    context: Dict[str, Any] = {}
    session_id: Optional[str] = None
    project_id: Optional[str] = None


class AgentResponse(BaseModel):
    """Response model from an agent call."""

    content: str
    session_id: str
    agent_id: Optional[str] = None
    prompt_tokens: int = 0
    completion_tokens: int = 0


# ------------------------------------------------------------------
# AI Session schemas
# ------------------------------------------------------------------


class SessionCreate(BaseModel):
    """Request model for creating a chat session."""

    title: str = "New Chat"
    context_type: Optional[str] = None
    context_id: Optional[str] = None
    project_id: Optional[str] = None


class SessionOut(BaseModel):
    """Response model for a chat session."""

    id: str
    title: str
    context_type: Optional[str] = None
    context_id: Optional[str] = None
    total_tokens: int = 0
    message_count: int = 0
    status: str = "active"
    created_at: str
    updated_at: str


class MessageOut(BaseModel):
    """Response model for a chat message."""

    id: str
    role: str
    content: str
    agent_id: Optional[str] = None
    prompt_tokens: int = 0
    completion_tokens: int = 0
    created_at: str
