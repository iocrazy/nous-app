# backend/app/api/admin/nous_router.py

"""
Admin API for managing Nous platform-provided AI models.
"""

from typing import List

import httpx
from fastapi import APIRouter, HTTPException
from loguru import logger

from app.core.admin_deps import AdminAuthDep
from app.repositories.nous_repository import get_nous_repository
from app.schemas.ai import TestConnectionResponse
from app.schemas.nous import (
    NousModelCreate,
    NousModelResponse,
    NousModelTestResponse,
    NousModelUpdate,
    NousProbeRequest,
)
from app.services.ai.providers.ai_provider import AIProviderFactory
from app.services.ai.providers.embedding_config import _is_multimodal

router = APIRouter()


def _mask_key(key: str) -> str:
    """Mask API key, showing only last 4 characters."""
    if not key or len(key) <= 4:
        return "****"
    return f"{'*' * (len(key) - 4)}{key[-4:]}"


def _to_response(row: dict) -> NousModelResponse:
    """Convert DB row to admin response with masked API key."""
    return NousModelResponse(
        id=str(row["id"]),
        name=row["name"],
        display_name=row["display_name"],
        type=row["type"],
        description=row.get("description"),
        actual_provider=row["actual_provider"],
        actual_model=row["actual_model"],
        api_key_masked=_mask_key(row.get("api_key", "")),
        app_id=row.get("app_id"),
        base_url=row.get("base_url"),
        pricing_type=row["pricing_type"],
        pricing_value=float(row["pricing_value"]),
        is_enabled=row["is_enabled"],
        sort_order=row["sort_order"],
        created_at=str(row.get("created_at", "")),
        updated_at=str(row.get("updated_at", "")),
        last_test_status=row.get("last_test_status"),
        last_test_detail=row.get("last_test_detail"),
        last_tested_at=(
            str(row["last_tested_at"]) if row.get("last_tested_at") else None
        ),
    )


@router.get("", response_model=List[NousModelResponse])
async def list_nous_models(auth: AdminAuthDep):
    """List all Nous models (including disabled)."""
    repo = get_nous_repository()
    rows = await repo.list_all()
    return [_to_response(r) for r in rows]


@router.post("/probe-models", response_model=TestConnectionResponse)
async def probe_nous_models(body: NousProbeRequest, auth: AdminAuthDep):
    """Self-check a provider key and return the models it exposes.

    Lets the admin pick ``actual_model`` from a fetched list instead of
    hand-typing it. Reuses the shared provider test-connection (which calls
    the provider's ``/v1/models``). Providers without an OpenAI-compatible
    model catalog (e.g. volcengine ASR) return ``success=False`` — the UI then
    falls back to manual model entry. The platform key is used server-side
    only and never echoed back.

    Edit-form fallback: the stored key is never sent to the client, so the
    edit form's API Key field is blank. When ``api_key`` is blank and ``name``
    references an existing model, reuse that model's stored key (and app_id)
    so probing works without re-typing the key.
    """
    api_key = (body.api_key or "").strip()
    app_id = body.app_id or ""
    if not api_key and body.name:
        repo = get_nous_repository()
        existing = await repo.get_by_name(body.name)
        if existing:
            api_key = existing.get("api_key") or ""
            app_id = app_id or existing.get("app_id") or ""

    result = await AIProviderFactory.test_connection(
        provider_key=body.provider_key,
        config={
            "api_key": api_key,
            "app_id": app_id,
            "base_url": body.base_url,
            "model": body.model,
        },
    )
    return TestConnectionResponse(**result)


@router.post("", response_model=NousModelResponse)
async def create_nous_model(body: NousModelCreate, auth: AdminAuthDep):
    """Create a new Nous model.

    Provider-card UX: a blank ``api_key`` inherits the key (and app_id) from an
    existing model on the same ``actual_provider`` + ``base_url`` — so the admin
    enters the key once per provider and adds more models without re-typing it.
    """
    repo = get_nous_repository()
    data = body.model_dump()
    if not (data.get("api_key") or "").strip():
        base_url = data.get("base_url") or ""
        rows = await repo.list_all()
        sibling = next(
            (
                r
                for r in rows
                if r.get("actual_provider") == data.get("actual_provider")
                and (r.get("base_url") or "") == base_url
                and (r.get("api_key") or "").strip()
            ),
            None,
        )
        if not sibling:
            raise HTTPException(
                status_code=400,
                detail=(
                    "API key required — no existing model on this provider to "
                    "inherit the key from."
                ),
            )
        data["api_key"] = sibling["api_key"]
        if not (data.get("app_id") or "") and sibling.get("app_id"):
            data["app_id"] = sibling["app_id"]

    row = await repo.create(data)
    if not row:
        raise HTTPException(status_code=500, detail="Failed to create model")
    logger.info(f"[Admin] Created Nous model: {body.name}")
    return _to_response(row)


@router.put("/{model_id}", response_model=NousModelResponse)
async def update_nous_model(model_id: str, body: NousModelUpdate, auth: AdminAuthDep):
    """Update a Nous model."""
    repo = get_nous_repository()
    updates = body.model_dump(exclude_none=True)
    if not updates:
        raise HTTPException(status_code=400, detail="No fields to update")
    row = await repo.update(model_id, updates)
    if not row:
        raise HTTPException(status_code=404, detail="Model not found")
    logger.info(f"[Admin] Updated Nous model: {model_id}")
    return _to_response(row)


@router.delete("/{model_id}")
async def delete_nous_model(model_id: str, auth: AdminAuthDep):
    """Delete a Nous model."""
    repo = get_nous_repository()
    ok = await repo.delete(model_id)
    if not ok:
        raise HTTPException(status_code=404, detail="Model not found")
    logger.info(f"[Admin] Deleted Nous model: {model_id}")
    return {"message": "Deleted"}


async def _probe_nous_model(row: dict) -> dict:
    """Real connectivity probe for one platform model, by type.

    Unlike ``test_connection`` (which only lists ``/v1/models``), this performs
    an actual minimal inference so account-level limits surface (e.g. a
    Volcengine ``SetLimitExceeded`` 429 on a specific model). Never raises.
    """
    typ = (row.get("type") or "").strip()
    model = (row.get("actual_model") or "").strip()
    base = (row.get("base_url") or "").rstrip("/")
    key = row.get("api_key") or ""
    provider = (row.get("actual_provider") or "").strip()
    headers = {"Authorization": f"Bearer {key}", "Content-Type": "application/json"}

    try:
        if typ == "asr":
            res = await AIProviderFactory.test_connection(
                provider_key=provider,
                config={
                    "api_key": key,
                    "app_id": row.get("app_id") or "",
                    "base_url": base,
                    "model": model,
                },
            )
            return {
                "ok": bool(res.get("success")),
                "detail": "reachable" if res.get("success") else "",
                "error": res.get("error"),
                "dims": None,
            }

        if typ == "embedding":
            if _is_multimodal(model, base):
                url = (
                    base
                    if "embeddings/multimodal" in base
                    else base + "/embeddings/multimodal"
                )
                payload = {"model": model, "input": [{"type": "text", "text": "ping"}]}
            else:
                url = base + "/embeddings"
                payload = {"model": model, "input": "ping"}
            async with httpx.AsyncClient(timeout=20.0) as c:
                r = await c.post(url, headers=headers, json=payload)
            if r.status_code != 200:
                return {
                    "ok": False,
                    "detail": "",
                    "error": f"HTTP {r.status_code}: {r.text[:160]}",
                    "dims": None,
                }
            data = (r.json() or {}).get("data")
            emb = None
            if isinstance(data, dict):
                emb = data.get("embedding")
            elif isinstance(data, list) and data and isinstance(data[0], dict):
                emb = data[0].get("embedding")
            dims = len(emb) if isinstance(emb, list) else None
            return {
                "ok": dims is not None,
                "detail": f"{dims} dims" if dims else "",
                "error": None if dims else "no embedding vector in response",
                "dims": dims,
            }

        # llm (and any chat-completions provider)
        url = base + "/chat/completions"
        payload = {
            "model": model,
            "messages": [{"role": "user", "content": "ping"}],
            "max_tokens": 8,
        }
        async with httpx.AsyncClient(timeout=20.0) as c:
            r = await c.post(url, headers=headers, json=payload)
        if r.status_code != 200:
            return {
                "ok": False,
                "detail": "",
                "error": f"HTTP {r.status_code}: {r.text[:160]}",
                "dims": None,
            }
        ok = bool((r.json() or {}).get("choices"))
        return {
            "ok": ok,
            "detail": "chat ok" if ok else "",
            "error": None if ok else "no choices in response",
            "dims": None,
        }
    except Exception as e:  # noqa: BLE001 — probe is best-effort
        return {"ok": False, "detail": "", "error": str(e)[:200], "dims": None}


@router.post("/{model_id}/test", response_model=NousModelTestResponse)
async def test_nous_model(model_id: str, auth: AdminAuthDep):
    """Run a real connectivity probe for one platform model (chat/embedding/asr).

    The result is persisted on the row (last_test_status / detail / tested_at)
    so the admin's status dot + "last tested" hint survive navigation.
    """
    repo = get_nous_repository()
    rows = await repo.list_all()
    row = next((r for r in rows if str(r.get("id")) == str(model_id)), None)
    if not row:
        raise HTTPException(status_code=404, detail="Model not found")
    result = await _probe_nous_model(row)

    status = "ok" if result.get("ok") else "fail"
    detail = result.get("detail") or (result.get("error") or "")
    saved = await repo.record_test_result(model_id, status, detail[:200])
    tested_at = (
        str(saved["last_tested_at"]) if saved and saved.get("last_tested_at") else None
    )
    return NousModelTestResponse(**result, tested_at=tested_at)
