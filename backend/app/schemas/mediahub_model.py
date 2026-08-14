# backend/app/schemas/mediahub_model.py

"""Pydantic schemas for Nous models API."""

from typing import List, Literal, Optional

from pydantic import BaseModel

MediahubModelType = Literal["llm", "embedding", "tts", "asr"]


class MediahubModelCreate(BaseModel):
    """Request body for creating a Mediahub model."""

    name: str
    display_name: str
    type: MediahubModelType
    description: Optional[str] = None
    actual_provider: str
    actual_model: str
    # Blank → inherit from an existing model on the same provider+base_url
    # (provider-card UX: enter the key once, add more models without re-typing).
    api_key: str = ""
    app_id: Optional[str] = None
    base_url: Optional[str] = None
    pricing_type: Literal["per_hour", "per_request", "per_token"] = "per_hour"
    pricing_value: float = 8
    is_enabled: bool = True
    sort_order: int = 0


class MediahubModelUpdate(BaseModel):
    """Request body for updating a Mediahub model (all fields optional)."""

    name: Optional[str] = None
    display_name: Optional[str] = None
    type: Optional[MediahubModelType] = None
    description: Optional[str] = None
    actual_provider: Optional[str] = None
    actual_model: Optional[str] = None
    api_key: Optional[str] = None
    app_id: Optional[str] = None
    base_url: Optional[str] = None
    pricing_type: Optional[Literal["per_hour", "per_request", "per_token"]] = None
    pricing_value: Optional[float] = None
    is_enabled: Optional[bool] = None
    sort_order: Optional[int] = None


class MediahubModelResponse(BaseModel):
    """Admin response — api_key masked."""

    id: str
    name: str
    display_name: str
    type: str
    description: Optional[str] = None
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
    # Last connectivity-test result (persisted; drives the status dot + the
    # "last tested" hint across navigation). NULL = never tested.
    last_test_status: Optional[str] = None
    last_test_detail: Optional[str] = None
    last_tested_at: Optional[str] = None


class MediahubModelPublic(BaseModel):
    """Public response — no API key or provider details."""

    name: str
    display_name: str
    type: str
    description: Optional[str] = None
    pricing_type: str
    pricing_value: float


class MediahubModelProbeRequest(BaseModel):
    """Admin 'Test & Load Models' request.

    ``name`` is the existing model being edited (optional): when ``api_key`` is
    left blank in the edit form (the stored key is never sent to the client),
    the backend falls back to that model's stored key so probing works without
    re-typing the key.
    """

    provider_key: str
    api_key: Optional[str] = ""
    app_id: Optional[str] = ""
    base_url: Optional[str] = ""
    model: Optional[str] = ""
    name: Optional[str] = None


class MediahubModelTestResponse(BaseModel):
    """Result of a real per-model connectivity probe (chat / embedding / asr)."""

    ok: bool
    # True when the probe has no protocol for this model's type and therefore
    # checked nothing (image / video / tts). ``ok`` stays False — nothing
    # succeeded — so this is the only signal that separates "didn't check" from
    # "checked and failed", and the admin page needs it to render a neutral
    # badge instead of a red one.
    not_probed: bool = False
    detail: str = ""
    error: Optional[str] = None
    dims: Optional[int] = None
    # ISO timestamp the result was persisted at (lets the UI show "tested just
    # now" without re-fetching the whole list).
    tested_at: Optional[str] = None


class ProviderProtocolItem(BaseModel):
    """One provider protocol for the admin dropdown."""

    key: str
    label: str
    description: str
    model_types: List[str]
    aliases: List[str]
    is_default: bool


class ProviderProtocolListResponse(BaseModel):
    protocols: List[ProviderProtocolItem]
