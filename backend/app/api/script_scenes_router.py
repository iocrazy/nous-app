"""Script Scenes Router — scene CRUD + versioned element ops (Phase B P2).

Endpoints:
  GET  /scripts/{script_id}/scenes        — verify_script_read_access
  POST /scripts/{script_id}/scenes        — verify_script_access
  GET    /scenes/{scene_id}               — verify_scene_read_access
  PATCH  /scenes/{scene_id}               — verify_scene_access
  DELETE /scenes/{scene_id}               — verify_scene_access
  POST /scenes/{scene_id}/elements/ops    — verify_scene_access
  POST /scenes/{scene_id}/move            — verify_scene_access

The GET routes use the *_read_access variants (team membership OR an
explicit project_members row on the parent project — 2026-08-12 fix); the
write routes keep the team-only gate unchanged.

The ops endpoint is the optimistic-concurrency heart: it requires an
``If-Match: <content_version>`` header (missing → 428), maps ``VersionConflict``
→ 409 and ``OpError`` → 422 with the exact body shapes the editor rebases on.
"""

import uuid
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends, Header, HTTPException
from fastapi.responses import JSONResponse
from loguru import logger

from app.core.config import settings
from app.core.deps import AuthDep
from app.core.scope_guards import (
    verify_scene_access,
    verify_scene_read_access,
    verify_script_access,
    verify_script_read_access,
)
from app.repositories.script_scene_repository import (
    UNSET,
    VersionConflict,
    get_script_scene_repository,
)
from app.schemas.script import (
    CopilotOpsRequest,
    SceneCreate,
    SceneCreateAfterLock,
    SceneMetaUpdate,
    SceneMoveRequest,
    SceneOpsRequest,
)
from app.services.script.scene_ops import OpError, apply_ops

# Anchor / element-id keys in a copilot op whose ``el_new_*`` placeholders the
# server rewrites to real ids before dry-run + return.
_OP_ID_KEYS = ("element_id", "before_id", "after_id")
_PLACEHOLDER_PREFIX = "el_new_"

# Copilot LLM output is untrusted. Only these payload keys survive into an op:
# a smuggled ``payload.id`` would shadow ``element_id`` (scene_ops builds
# ``{"id": element_id, **payload}`` — payload wins → duplicate id + Undo inverse
# mismatch), and any arbitrary key is dropped. This whitelist is applied ONLY on
# the copilot path; the shared ``scene_ops.apply_ops`` deliberately keeps its
# permissive shape for the manual ``/elements/ops`` endpoint, so the boundary
# choice lives here (in the router), not in scene_ops.py.
_ALLOWED_PAYLOAD_KEYS = ("type", "text", "character_id")

# Hard safety caps on an LLM-generated batch (checked before dry-run). A batch
# exceeding either is rejected 422 rather than truncated — an honest failure the
# editor surfaces, not a silently-mangled edit.
MAX_COPILOT_OPS = 200
MAX_COPILOT_TEXT_LEN = 4000

router = APIRouter()

# Snowflake BIGINT id/FK fields on a script_scenes row (mirrors the
# repository's `_SCENE_BIGINT_FIELDS` write-side coercion; `id` itself is the
# PK and always bigint). The repository deliberately keeps these NATIVE INT
# on read (strategy-C parity, see script_scene_repository.py) for internal
# comparisons — this stringifies ONLY at the JSON response boundary. Same
# fix family as script_shots_router._to_response (2026-08 P0: a Snowflake id
# serialized as a JSON number risks JS precision loss and string-keyed
# reconciliation mismatches on the frontend).
_SCENE_ID_FIELDS = ("id", "script_id", "chapter_id", "location_id")


def _to_response(scene: Optional[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
    """Stringify a scene row's bigint id/FK fields for the JSON response.

    Returns a NEW dict (never mutates the repository's row). ``None`` passes
    through unchanged."""
    if scene is None:
        return None
    out = dict(scene)
    for field in _SCENE_ID_FIELDS:
        if field in out and out[field] is not None:
            out[field] = str(out[field])
    return out


def _to_response_list(scenes: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """``_to_response`` applied to every row in a list."""
    return [_to_response(s) for s in scenes]


@router.get("/scripts/{script_id}/scenes")
async def list_scenes(
    script_id: str,
    auth: AuthDep,
    _guard: None = Depends(verify_script_read_access),
) -> Dict[str, Any]:
    """List all scenes for a script (chapter_id NULLS LAST, then sort_order)."""
    try:
        scenes = await get_script_scene_repository().list_by_script(script_id)
        return {"success": True, "data": _to_response_list(scenes)}
    except Exception as exc:
        logger.error(f"[Scenes] list for script {script_id} failed: {exc}")
        raise HTTPException(status_code=500, detail="Failed to list scenes")


@router.post("/scripts/{script_id}/scenes")
async def create_scene(
    script_id: str,
    auth: AuthDep,
    body: SceneCreate,
    _guard: None = Depends(verify_script_access),
) -> Dict[str, Any]:
    """Create a scene under a script. sort_order auto-assigns to MAX+STEP
    within the (script_id, chapter_id) group when omitted."""
    try:
        data = body.model_dump(exclude_none=True)
        data["script_id"] = script_id
        scene = await get_script_scene_repository().create(data)
        return {"success": True, "data": _to_response(scene)}
    except Exception as exc:
        logger.error(f"[Scenes] create for script {script_id} failed: {exc}")
        raise HTTPException(status_code=500, detail="Failed to create scene")


@router.post("/scripts/{script_id}/scenes/after-lock")
async def create_scene_after_lock(
    script_id: str,
    auth: AuthDep,
    body: SceneCreateAfterLock,
    _guard: None = Depends(verify_script_access),
) -> Dict[str, Any]:
    """Create a scene in an ALREADY-LOCKED script (agent-layer spec §4.2
    "锁定后插入"): positions it (bisecting between ``before_scene_id`` /
    ``after_scene_id``, or appended at the tail when neither is given) and
    assigns its ``scene_number`` — a letter suffix for a genuine insert
    between two locked scenes, or the next plain integer for a tail append.
    404 if the script's numbering isn't locked yet (use the plain create
    endpoint pre-lock)."""
    try:
        data = body.model_dump(
            exclude_none=True, exclude={"before_scene_id", "after_scene_id"}
        )
        data["script_id"] = script_id
        scene = await get_script_scene_repository().create_after_lock(
            data,
            before_scene_id=body.before_scene_id,
            after_scene_id=body.after_scene_id,
        )
        return {"success": True, "data": _to_response(scene)}
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc))
    except Exception as exc:
        logger.error(f"[Scenes] create_after_lock for script {script_id} failed: {exc}")
        raise HTTPException(status_code=500, detail="Failed to create scene")


@router.get("/scenes/{scene_id}")
async def get_scene(
    scene_id: str,
    auth: AuthDep,
    _guard: None = Depends(verify_scene_read_access),
) -> Dict[str, Any]:
    """Get a single scene by id."""
    try:
        scene = await get_script_scene_repository().get_by_id(scene_id)
        if scene is None:
            raise HTTPException(status_code=404, detail="Scene not found")
        return {"success": True, "data": _to_response(scene)}
    except HTTPException:
        raise
    except Exception as exc:
        logger.error(f"[Scenes] get {scene_id} failed: {exc}")
        raise HTTPException(status_code=500, detail="Failed to get scene")


@router.patch("/scenes/{scene_id}")
async def update_scene(
    scene_id: str,
    auth: AuthDep,
    body: SceneMetaUpdate,
    _guard: None = Depends(verify_scene_access),
) -> Dict[str, Any]:
    """Update scene header fields / canvas coords. NEVER touches content*."""
    try:
        scene = await get_script_scene_repository().update_meta(
            scene_id, body.model_dump(exclude_none=True)
        )
        if scene is None:
            raise HTTPException(status_code=404, detail="Scene not found")
        return {"success": True, "data": _to_response(scene)}
    except HTTPException:
        raise
    except Exception as exc:
        logger.error(f"[Scenes] update {scene_id} failed: {exc}")
        raise HTTPException(status_code=500, detail="Failed to update scene")


@router.delete("/scenes/{scene_id}")
async def delete_scene(
    scene_id: str,
    auth: AuthDep,
    _guard: None = Depends(verify_scene_access),
) -> Dict[str, Any]:
    """Delete a scene — hard delete pre-lock (ops cascade via FK ON DELETE
    CASCADE); once the script's numbering is locked, this instead OMITS the
    scene in place (kept row + scene_number, ``omitted_at`` stamped) so the
    number is never reused (agent-layer spec §4.2 "删除保留号标 OMITTED")."""
    try:
        result = await get_script_scene_repository().delete(scene_id)
        result = dict(result)
        result["scene"] = _to_response(result.get("scene"))
        return {"success": True, "data": result}
    except Exception as exc:
        logger.error(f"[Scenes] delete {scene_id} failed: {exc}")
        raise HTTPException(status_code=500, detail="Failed to delete scene")


@router.post("/scenes/{scene_id}/elements/ops")
async def apply_element_ops(
    scene_id: str,
    auth: AuthDep,
    body: SceneOpsRequest,
    if_match: Optional[str] = Header(None, alias="If-Match"),
    _guard: None = Depends(verify_scene_access),
):
    """Apply a batch of anchor-based element ops under optimistic concurrency.

    Requires ``If-Match: <content_version>``. On success returns 200 with the
    new content_version + elements; on a version race returns 409 with the
    current version + elements for the editor to rebase; on a protocol
    violation returns 422 with the OpError code.
    """
    if if_match is None:
        raise HTTPException(status_code=428, detail="If-Match header required")
    try:
        expected_version = int(if_match)
    except (TypeError, ValueError):
        raise HTTPException(
            status_code=400, detail="If-Match must be an integer content_version"
        )

    try:
        result = await get_script_scene_repository().apply_element_ops(
            scene_id,
            body.ops,
            expected_version=expected_version,
            actor=auth.user_id,
        )
        return {"success": True, "data": result}
    except VersionConflict as vc:
        return JSONResponse(
            status_code=409,
            content={
                "success": False,
                "code": "version_conflict",
                "current_version": vc.current_version,
                "elements": vc.elements,
            },
        )
    except OpError as oe:
        return JSONResponse(
            status_code=422,
            content={
                "success": False,
                "code": oe.code,
                "detail": str(oe),
            },
        )
    except Exception as exc:
        logger.error(f"[Scenes] apply ops on {scene_id} failed: {exc}")
        raise HTTPException(status_code=500, detail="Failed to apply element ops")


def _replace_placeholder_ids(ops: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Rewrite every ``el_new_*`` placeholder to a fresh real ``el_<8hex>`` id.

    Returns a NEW list of NEW op dicts (immutability contract) — the payloads
    are shared by reference (never mutated downstream). A placeholder maps to
    the SAME real id everywhere it appears (element_id and any anchor), so an
    inserted element and a later anchor pointing at it stay consistent.
    """
    mapping: Dict[str, str] = {}

    def _real(pid: str) -> str:
        if pid not in mapping:
            mapping[pid] = f"el_{uuid.uuid4().hex[:8]}"
        return mapping[pid]

    rewritten: List[Dict[str, Any]] = []
    for op in ops:
        new_op = dict(op)
        for key in _OP_ID_KEYS:
            val = new_op.get(key)
            if isinstance(val, str) and val.startswith(_PLACEHOLDER_PREFIX):
                new_op[key] = _real(val)
        rewritten.append(new_op)
    return rewritten


def _whitelist_op_payloads(ops: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Strip each op's ``payload`` to the whitelisted keys (new dicts, immutable).

    Drops a smuggled ``payload.id`` (which would override the op's ``element_id``)
    and any arbitrary key the LLM invented, before the batch is dry-run or
    returned to the client.
    """
    cleaned: List[Dict[str, Any]] = []
    for op in ops:
        new_op = dict(op)
        payload = new_op.get("payload")
        if isinstance(payload, dict):
            new_op["payload"] = {
                k: payload[k] for k in _ALLOWED_PAYLOAD_KEYS if k in payload
            }
        cleaned.append(new_op)
    return cleaned


def _copilot_cap_violation(ops: List[Dict[str, Any]]) -> Optional[tuple]:
    """Return ``(code, detail)`` when ``ops`` breaches a safety cap, else None."""
    if len(ops) > MAX_COPILOT_OPS:
        return (
            "too_many_ops",
            f"reconciler produced {len(ops)} ops (max {MAX_COPILOT_OPS})",
        )
    for op in ops:
        payload = op.get("payload")
        if isinstance(payload, dict):
            text = payload.get("text")
            if isinstance(text, str) and len(text) > MAX_COPILOT_TEXT_LEN:
                return (
                    "text_too_long",
                    f"an op's text exceeds {MAX_COPILOT_TEXT_LEN} characters",
                )
    return None


@router.post("/scenes/{scene_id}/copilot-ops")
async def copilot_ops(
    scene_id: str,
    auth: AuthDep,
    body: CopilotOpsRequest,
    _guard: None = Depends(verify_scene_access),
):
    """Reconcile a free-text instruction into element ops (flag-gated).

    An LLM turns ``body.instruction`` into an anchor-based op batch against the
    scene's CURRENT elements; the server rewrites placeholder ids, dry-runs the
    batch through ``apply_ops`` (ONE retry with the OpError context on failure),
    and returns the validated ops — it NEVER writes. The editor dispatches them
    through the existing ``/elements/ops`` If-Match channel, so this adds zero
    new concurrency surface.

    - flag ``FEATURE_COPILOT_OPS`` off → 404 (endpoint existence hidden).
    - ``read_version`` behind the scene's current version → ``proposal: true``
      with ops regenerated against the current elements (spec v3 §2.2 stale
      handling); the editor reviews before applying.
    - dry-run still failing after the retry → 422 with the OpError code.
    """
    # LOW#5 (known, accepted): ``verify_scene_access`` is a Depends and runs
    # BEFORE this body, so the flag check can't precede it — a caller WITHOUT
    # scene access gets the guard's 403 whether or not the flag is on. That
    # leaks nothing about the flag (403 is access-scoped, not existence-scoped)
    # and costs nothing extra, so the 404-hides-existence guarantee still holds
    # for callers WITH access.
    if not settings.FEATURE_COPILOT_OPS:
        raise HTTPException(status_code=404, detail="Not Found")

    from app.services.ai.providers.ai_provider_helpers import (
        resolve_script_provider_config,
    )
    from app.services.storyboard.script.script_ai_service import ScriptAIService

    scene = await get_script_scene_repository().get_by_id(scene_id)
    if scene is None:
        raise HTTPException(status_code=404, detail="Scene not found")
    elements = scene.get("content_json") or []
    current_version = scene.get("content_version") or 0
    is_proposal = body.read_version < current_version

    provider_key, provider_config, _model, agent_slug = (
        await resolve_script_provider_config(auth.user_id)
    )
    service = ScriptAIService(
        user_id=auth.user_id,
        agent_slug=agent_slug,
        provider_key=provider_key,
        provider_config=provider_config,
    )

    error_context: Optional[str] = None
    last_error: Optional[OpError] = None
    # One generation + up to one retry seeded with the failing OpError context.
    for _attempt in range(2):
        try:
            generated = await service.instruction_to_element_ops(
                elements, body.instruction, error_context=error_context
            )
        except Exception as exc:
            logger.error(f"[Copilot] generate ops for scene {scene_id} failed: {exc}")
            raise HTTPException(
                status_code=502, detail="Copilot could not generate edits"
            )

        ops = _replace_placeholder_ids(generated.get("ops") or [])
        # Untrusted-output hardening (before dry-run): strip payloads to the
        # whitelist, then enforce the hard batch caps.
        ops = _whitelist_op_payloads(ops)
        cap = _copilot_cap_violation(ops)
        if cap is not None:
            # A cap breach is a safety limit, not a fixable protocol error —
            # reject immediately (no retry).
            return JSONResponse(
                status_code=422,
                content={"success": False, "code": cap[0], "detail": cap[1]},
            )
        summary = generated.get("summary") or ""
        try:
            apply_ops(elements, ops)  # dry-run only — result discarded.
        except OpError as oe:
            last_error = oe
            error_context = f"{oe.code}: {oe.message}"
            logger.warning(
                f"[Copilot] scene {scene_id} dry-run rejected "
                f"({oe.code}); attempt {_attempt + 1}/2"
            )
            continue

        data: Dict[str, Any] = {
            "ops": ops,
            # base_version = the scene's CURRENT version. Returned for reference /
            # the proposal review UI; the editor dispatches with its OWN live
            # version via If-Match and does not read base_version to drive writes.
            "base_version": current_version,
            "summary": summary,
        }
        if is_proposal:
            data["proposal"] = True
        return {"success": True, "data": data}

    return JSONResponse(
        status_code=422,
        content={
            "success": False,
            "code": last_error.code if last_error else "invalid_op",
            "detail": str(last_error) if last_error else "dry-run failed",
        },
    )


@router.post("/scenes/{scene_id}/move")
async def move_scene(
    scene_id: str,
    auth: AuthDep,
    body: SceneMoveRequest,
    _guard: None = Depends(verify_scene_access),
) -> Dict[str, Any]:
    """Reorder (and optionally reparent) a scene. ``chapter_id`` omitted keeps
    the current chapter; supplied (incl. null) reparents."""
    try:
        chapter_id = body.chapter_id if "chapter_id" in body.model_fields_set else UNSET
        scene = await get_script_scene_repository().move_scene(
            scene_id,
            chapter_id=chapter_id,
            before_scene_id=body.before_scene_id,
            after_scene_id=body.after_scene_id,
        )
        return {"success": True, "data": _to_response(scene)}
    except Exception as exc:
        logger.error(f"[Scenes] move {scene_id} failed: {exc}")
        raise HTTPException(status_code=500, detail="Failed to move scene")
