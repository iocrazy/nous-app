# backend/app/schemas/nous.py

"""Pydantic schemas for Nous models API."""

from typing import Literal, Optional

from pydantic import BaseModel


class NousModelCreate(BaseModel):
    """Request body for creating a Nous model."""

    name: str
    display_name: str
    category: Literal["transcription", "summarization", "analysis"]
    actual_provider: str
    actual_model: str
    api_key: str
    app_id: Optional[str] = None
    base_url: Optional[str] = None
    pricing_type: Literal["per_hour", "per_request", "per_token"] = "per_hour"
    pricing_value: float = 8
    is_enabled: bool = True
    sort_order: int = 0


class NousModelUpdate(BaseModel):
    """Request body for updating a Nous model (all fields optional)."""

    name: Optional[str] = None
    display_name: Optional[str] = None
    category: Optional[Literal["transcription", "summarization", "analysis"]] = None
    actual_provider: Optional[str] = None
    actual_model: Optional[str] = None
    api_key: Optional[str] = None
    app_id: Optional[str] = None
    base_url: Optional[str] = None
    pricing_type: Optional[Literal["per_hour", "per_request", "per_token"]] = None
    pricing_value: Optional[float] = None
    is_enabled: Optional[bool] = None
    sort_order: Optional[int] = None


class NousModelResponse(BaseModel):
    """Admin response — api_key masked."""

    id: str
    name: str
    display_name: str
    category: str
    actual_provider: str
    actual_model: str
    api_key_masked: str
    app_id: Optional[str] = None
    base_url: Optional[str] = None
    pricing_type: str
    pricing_value: float
    is_enabled: bool
    sort_order: int
    created_at: Optional[str] = None
    updated_at: Optional[str] = None


class NousModelPublic(BaseModel):
    """Public response — no API key or provider details."""

    name: str
    display_name: str
    category: str
    pricing_type: str
    pricing_value: float
