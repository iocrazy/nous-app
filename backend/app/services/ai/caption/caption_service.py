"""CaptionService — runs the `caption` agent via AgentRunner.

Reverse-engineers a generation prompt from a local image file (IC-port
P1-2, 2026-06-12; upgraded to the three-format contract 2026-07-28).
Mirrors the multimodal half of VisualAnalysisService: encode the image
as a data-URL content block, compose the agent's IDENTITY/SOUL/AGENT
prompt, run one turn, parse the structured JSON.

The strict-JSON contract asks the agent for, in one call:
``{prompt_en, prompt_zh, prompt_json: {subject, style, composition,
lighting, color, text?, aspect_ratio}, tags: [{en, zh}, ...],
category, aspect_ratio}``. Parsing is tolerant — a response that fails
the structured shape (bad JSON, wrong type, or a legacy ``{"en","zh"}``
reply) degrades to the plain two-field prompt instead of failing the
caller; ``parse_caption_json`` is kept as that legacy/fallback parser
(also still covers pre-upgrade agent configs).

The agent slug is resolved by ``resolve_task_ai_config`` (``task_key=
"caption"``, called directly by the workflow's ``resolve_caption_provider``
step — bypassing the ``resolve_task_provider_config`` tuple shim so
``fallback_models`` rides along), so users pick the vision model/provider
in Settings → AI like every other task — the prompt agent and the model
agent are the same one (#622/#623 rule).

The LLM call runs through :func:`build_fallback_llm` (spec
2026-08-11-batch-llm-fallback / 2026-08-12-batch-fallback-rollout §1-F1)
instead of a bare per-instance adapter, so a primary-model outage fails
over to the resolved agent's ``fallback_models`` pool.
"""

from __future__ import annotations

import asyncio
import base64
import io
import json
from typing import Any, Dict, List, Optional
from uuid import UUID

from loguru import logger

from app.repositories.agent_repository import get_agent_repository
from app.repositories.skill_repository import get_skill_repository
from app.services.ai.adapters.factory import provider_key_for_model
from app.services.ai.prompts.prompt_composer import (
    PromptComposer,
    background_composer_input,
)
from app.services.ai.runner.agent_runner import AgentRunner
from app.services.ai.runner.run_recorder import AgentPausedError, RunRecorder
from app.services.ai.skills.skill_tool_service import SkillToolService

DEFAULT_AGENT_SLUG = "caption"

# Downscale bound before base64-encoding — a 20MB original PNG would blow
# the request payload; 1024px on the long side is plenty for prompt
# reverse-engineering (same bound Infinite-Canvas uses).
MAX_IMAGE_SIDE = 1024
JPEG_QUALITY = 85

# The six-dim structured-prompt keys (``text`` is the optional 7th slot —
# only present when the image contains rendered text worth calling out).
PROMPT_JSON_KEYS = (
    "subject",
    "style",
    "composition",
    "lighting",
    "color",
    "text",
    "aspect_ratio",
)
# Semantic tags per caption call — mirrors classify's per-dimension cap,
# kept small since these are a flat unstructured set, not 12 dimensions.
MAX_CAPTION_TAGS = 6

_INSTRUCTION = (
    "Reverse-engineer the generation prompt for the attached image per "
    "your AGENT spec. Return ONLY strict JSON — no markdown fences, no "
    "commentary — matching exactly this shape: "
    '{"prompt_en": "<EN SD-style prompt>", '
    '"prompt_zh": "<ZH rendering of the same prompt>", '
    '"prompt_json": {"subject": "...", "style": "...", '
    '"composition": "...", "lighting": "...", "color": "...", '
    '"text": "... (omit if none)", "aspect_ratio": "e.g. 16:9"}, '
    '"tags": [{"en": "...", "zh": "..."}, ...] (3-6 short semantic '
    'tags), "category": "<one short category label>", '
    '"aspect_ratio": "same as prompt_json.aspect_ratio"}.'
)


def _encode_image_sync(file_path: str) -> Optional[str]:
    """Open + downscale + JPEG-encode an image, return a base64 data URL.

    Sync (Pillow) — call via ``asyncio.to_thread``. Returns None on any
    read/decode failure (corrupt upload must not raise here).
    """
    try:
        from PIL import Image

        with Image.open(file_path) as img:
            img = img.convert("RGB")
            img.thumbnail((MAX_IMAGE_SIDE, MAX_IMAGE_SIDE))
            buf = io.BytesIO()
            img.save(buf, format="JPEG", quality=JPEG_QUALITY)
        b64 = base64.b64encode(buf.getvalue()).decode("utf-8")
        return f"data:image/jpeg;base64,{b64}"
    except Exception as e:
        logger.error(f"[Caption] failed to encode image {file_path}: {e}")
        return None


def parse_caption_json(text: str) -> Dict[str, str]:
    """Parse the agent's ``{"en", "zh"}`` payload, tolerating ``` fences.

    Returns a dict containing only the non-empty string sides — empty
    dict when nothing usable came back.
    """
    cleaned = (text or "").strip()
    if cleaned.startswith("```"):
        lines = cleaned.split("\n")
        if lines[-1].strip() == "```":
            cleaned = "\n".join(lines[1:-1])
        else:
            cleaned = "\n".join(lines[1:])
    try:
        data = json.loads(cleaned)
    except json.JSONDecodeError:
        logger.warning("[Caption] JSON parse failed on agent output")
        return {}
    if not isinstance(data, dict):
        return {}
    out: Dict[str, str] = {}
    for key in ("en", "zh"):
        value = data.get(key)
        if isinstance(value, str) and value.strip():
            out[key] = value.strip()
    return out


def _strip_fences(text: str) -> str:
    cleaned = (text or "").strip()
    if not cleaned.startswith("```"):
        return cleaned
    lines = cleaned.split("\n")
    if lines[-1].strip() == "```":
        return "\n".join(lines[1:-1])
    return "\n".join(lines[1:])


def _parse_prompt_json(raw: Any) -> Dict[str, str]:
    """Keep only the known six-dim keys with non-empty string values.

    Missing keys are simply absent from the result (tolerant — a
    partially-filled structured prompt is still useful), unknown keys
    are dropped.
    """
    if not isinstance(raw, dict):
        return {}
    out: Dict[str, str] = {}
    for key in PROMPT_JSON_KEYS:
        value = raw.get(key)
        if isinstance(value, str) and value.strip():
            out[key] = value.strip()
    return out


def _parse_caption_tags(raw: Any) -> List[Dict[str, str]]:
    """Normalize the ``tags`` list: non-empty ``en`` required, dedup
    case-insensitively, capped at ``MAX_CAPTION_TAGS``."""
    if not isinstance(raw, list):
        return []
    out: List[Dict[str, str]] = []
    seen: set[str] = set()
    for item in raw:
        if not isinstance(item, dict):
            continue
        en_raw = item.get("en")
        zh_raw = item.get("zh")
        en = en_raw.strip() if isinstance(en_raw, str) else ""
        zh = zh_raw.strip() if isinstance(zh_raw, str) else ""
        if not en:
            continue
        dedup_key = en.lower()
        if dedup_key in seen:
            continue
        seen.add(dedup_key)
        out.append({"en": en, "zh": zh} if zh else {"en": en})
        if len(out) >= MAX_CAPTION_TAGS:
            break
    return out


def parse_caption_result(text: str) -> Dict[str, Any]:
    """Parse the caption agent's structured three-format payload.

    Happy path returns a dict that may carry:
      ``en`` / ``zh``       — bilingual prompt strings (from
                               ``prompt_en`` / ``prompt_zh``)
      ``prompt_json``       — dict with whichever of the six-dim keys
                               the model filled in
      ``tags``              — list[{"en", "zh"}], 0-6 entries
      ``category``          — str, used by the caller to bucket the
                               tags above under one tag_groups row

    Degrades to the legacy two-field parser (``parse_caption_json``) —
    and therefore to its same ``{"en", ...}`` / ``{}`` shape — when the
    response isn't valid JSON, isn't an object, or the structured
    envelope carries neither prompt side (an older/simpler agent reply
    using the bare ``{"en", "zh"}`` shape still round-trips through
    here). Never raises — the workflow treats an empty dict as "nothing
    usable" and fails there instead.
    """
    cleaned = _strip_fences(text)
    try:
        data = json.loads(cleaned)
    except json.JSONDecodeError:
        logger.warning(
            "[Caption] structured JSON parse failed — falling back to "
            "legacy two-field parse"
        )
        return parse_caption_json(text)

    if not isinstance(data, dict):
        return parse_caption_json(text)

    out: Dict[str, Any] = {}
    en_raw = data.get("prompt_en")
    zh_raw = data.get("prompt_zh")
    if isinstance(en_raw, str) and en_raw.strip():
        out["en"] = en_raw.strip()
    if isinstance(zh_raw, str) and zh_raw.strip():
        out["zh"] = zh_raw.strip()

    if not out:
        # Structured envelope, but neither prompt_en nor prompt_zh was
        # usable — try the legacy {"en", "zh"} shape on the same
        # payload before giving up (covers a model that ignored the
        # new field names but still answered sensibly).
        out = parse_caption_json(text)
        if not out:
            return {}

    prompt_json = _parse_prompt_json(data.get("prompt_json"))
    aspect_ratio = data.get("aspect_ratio")
    if isinstance(aspect_ratio, str) and aspect_ratio.strip():
        prompt_json.setdefault("aspect_ratio", aspect_ratio.strip())
    if prompt_json:
        out["prompt_json"] = prompt_json

    tags = _parse_caption_tags(data.get("tags"))
    if tags:
        out["tags"] = tags

    category = data.get("category")
    if isinstance(category, str) and category.strip():
        out["category"] = category.strip()
        # Also fold category INTO prompt_json so the persisted
        # gen_prompt_json carries it — the result card reads category from
        # there (tags/groups are not a reliable read path for it).
        if "prompt_json" in out:
            out["prompt_json"].setdefault("category", category.strip())
        else:
            out["prompt_json"] = {"category": category.strip()}

    return out


class CaptionService:
    AGENT_SLUG: str = DEFAULT_AGENT_SLUG

    def __init__(
        self,
        provider_key: str = "",
        provider_config: Optional[Dict[str, Any]] = None,
        agent_slug: str = DEFAULT_AGENT_SLUG,
    ) -> None:
        self._provider_key = (provider_key or "").strip()
        self._provider_config: Dict[str, Any] = dict(provider_config or {})
        self.AGENT_SLUG = (agent_slug or DEFAULT_AGENT_SLUG).strip() or (
            DEFAULT_AGENT_SLUG
        )
        self.model = self._provider_config.get("model") or ""
        # The RESOLVED model — ``resolve_task_ai_config`` writes it into
        # ``provider_config["model"]`` next to the api_key/base_url it resolved
        # FOR that model, so this is the one string that may be dialed. Empty
        # when the resolver produced nothing (bare / smoke path). It gets its
        # own attribute rather than reusing ``self.model`` because that one is
        # per-service: VisualAnalysisService defaults it to a ``"gpt-4o"``
        # cost-estimate placeholder that must never reach the wire.
        self._resolved_model = (self._provider_config.get("model") or "").strip()

    async def caption(
        self,
        *,
        file_path: str,
        user_id: Optional[Any],
        resource_id: Optional[str] = None,
        task_id: Optional[str] = None,
        fallback_models: Optional[list[str]] = None,
    ) -> Optional[Dict[str, str]]:
        """Generate a bilingual/structured prompt from a local image.

        Returns ``parse_caption_result``'s dict — ``{"en"?, "zh"?,
        "prompt_json"?, "tags"?, "category"?}`` (the two-field legacy
        shape when the structured contract didn't parse), or None on
        failure. Signature is unchanged from the pre-upgrade version;
        only the return dict grew optional keys, so existing callers
        that only read ``en``/``zh`` keep working unmodified.

        ``fallback_models``: platform-preset fallback pool from the
        resolved agent row (spec 2026-08-11-batch-llm-fallback §4),
        threaded into :func:`build_fallback_llm` so a primary-model
        outage fails over instead of erroring the whole caption run.
        """
        data_url = await asyncio.to_thread(_encode_image_sync, file_path)
        if not data_url:
            return None

        composer = PromptComposer(get_agent_repository(), get_skill_repository())
        composed = await composer.compose(
            background_composer_input(
                agent_slug=self.AGENT_SLUG,
                request_instructions=_INSTRUCTION,
                resolved_model=self._resolved_model,
            )
        )

        # 单源收口:解析器给的模型即最终模型。``background_composer_input`` 已经
        # 把它交给 composer 了,这里再把它钉回 ``composed`` —— AgentRunner 从
        # ``composed.model`` 读要拨的号,下面的 fallback 链从这里取
        # ``primary_model``,两条路因此不可能各走各的(#622/#623)。空值(裸 /
        # smoke 路径什么都没解析出来)保留 agent 行自己的模型。
        if self._resolved_model:
            composed = composed.model_copy(update={"model": self._resolved_model})

        from app.services.ai.llm.fallback_wiring import build_fallback_llm

        model_for_chain = composed.model or self.model
        adapter = await build_fallback_llm(
            primary_model=model_for_chain,
            fallback_models=list(fallback_models or []),
            user_provider_config=self._provider_config,
            # self._provider_config is the NARROWED flat single-provider
            # shape ({"model","api_key","base_url"}), not the provider-keyed
            # dict get_adapter_for_user expects — provider_key tells
            # build_fallback_llm to wrap it per-attempt (final-review C1,
            # spec 2026-08-11-batch-llm-fallback). "caption" matches
            # resolve_task_ai_config's own task_key for this module so the
            # pre-resolved platform-catalog gate agrees with the primary
            # model's resolver.
            provider_key=self._provider_key,
            module="caption",
        )
        runner = AgentRunner(
            adapter=adapter,
            skill_tool=SkillToolService(get_skill_repository()),
        )
        user_messages = [
            {
                "role": "user",
                "content": [
                    {
                        "type": "image_url",
                        "image_url": {"url": data_url, "detail": "high"},
                    }
                ],
            }
        ]

        if user_id is None:
            try:
                result = await runner.run_turn(
                    composed, user_messages=user_messages, recorder=None
                )
                if result.get("error"):
                    logger.warning(f"[Caption] runner error: {result.get('error')}")
                    return None
                return parse_caption_result(result.get("content") or "") or None
            except Exception as e:
                logger.error(f"[Caption] bare run failed: {e}")
                return None

        uid = user_id if isinstance(user_id, UUID) else UUID(str(user_id))
        model = composed.model or self.model
        if self._provider_key:
            provider = self._provider_key
        else:
            try:
                provider = provider_key_for_model(model) if model else "openai"
            except ValueError:
                provider = "openai"

        try:
            async with RunRecorder(
                agent_id=composed.agent_id,
                user_id=uid,
                trigger="prompt_caption",
                task_id=task_id,
                model=model or None,
                provider=provider,
                input_summary=f"caption image {file_path.rsplit('/', 1)[-1]}"[:200],
                metadata={"resource_id": resource_id},
            ) as recorder:
                result = await runner.run_turn(
                    composed,
                    user_messages=user_messages,
                    recorder=recorder,
                )
                content = result.get("content") or ""
                recorder.set_summaries(output_summary=content[:500])
                if result.get("error"):
                    logger.warning(f"[Caption] runner error: {result.get('error')}")
                    return None
                return parse_caption_result(content) or None
        except AgentPausedError as err:
            logger.warning(f"[Caption] agent paused: {err}")
            return None
        # LLM 类异常(AllModelsFailed/LLMCallError 及其他意外)一律 propagate:
        # caption_asset_workflow / caption_slide_workflow 的 tail except 会先
        # await record_ai_error_code(wf_id, e)(classify_ai_error 落
        # task_tracking.metadata.error_code),再 record_workflow_failure +
        # raise(PR #1743 route-C rule 4)——吞成 None 会让 workflow 只看到合成
        # RuntimeError,两个机制都够不着真因(本次接线的动机,spec §1-F1)。
