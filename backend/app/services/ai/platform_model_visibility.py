"""用户在 Settings「平台模型」卡上的开关，套到任何面向用户的目录列表上。

Settings 页（`AISettings.tsx::visibleNousModels`）有三层；这里**只套用户自己的两层**：
1. 用户平台卡总开关 `ai_providers.nous.enabled === false` → 一个不给；
2. 用户逐模型黑名单 `ai_providers.nous.disabled_models` → 去掉这些。

⚠️ 故意不套管理员治理总开关 `nous.user_enabled`：那个开关**默认关、库读不到也判关**
（`is_nous_globally_enabled` 为控成本 fail-closed）。把它接进模型下拉，一次 DB 抖动
就会让画布 / 封面工作室的下拉整个变空，而真正的成本闸门在派发那一层早就有了。
下拉的职责是"显示用户在 Settings 里留下的选择"，不是再当一次闸门。

这层原来只在前端 Settings 页里生效，画布 / 封面工作室拿 `/canvases/generation-models`
时看到的是没过滤的目录 —— 用户在 Settings 里关掉的模型照样出现在下拉里
（2026-08-27 用户指出）。这里把同一口径搬到服务端，让所有"给用户选模型"的列表
走同一个函数，而不是各自抄一份必然漂移的过滤。

只做过滤、不做兜底：用户把所有图片模型都关了，列表就是空的 —— 那是他在 Settings
里明确表达的意思，不该被"目录默认"悄悄绕过。
"""

from __future__ import annotations

import logging
from typing import Any, Iterable

logger = logging.getLogger(__name__)

_AI_SETTINGS_KEY = "ai_settings"


async def platform_model_gate(user_id: str) -> tuple[bool, frozenset[str]]:
    """``(any_allowed, disabled_names)`` for this user.

    ``any_allowed=False`` means the user turned the platform card's master
    switch off; ``disabled_names`` is the per-model blacklist otherwise.
    """
    from app.repositories.user_settings_repository import UserSettingsRepository

    row = await UserSettingsRepository().get_by_user_id(user_id)
    settings_json = (row or {}).get("settings_json") or {}
    ai_settings = settings_json.get(_AI_SETTINGS_KEY) or {}
    nous = (ai_settings.get("ai_providers") or {}).get("nous") or {}
    if nous.get("enabled") is False:
        return False, frozenset()
    disabled = nous.get("disabled_models") or []
    return True, frozenset(str(n) for n in disabled if n)


async def filter_platform_models_for_user(
    user_id: str, rows: Iterable[dict[str, Any]]
) -> list[dict[str, Any]]:
    """Apply the user's Settings platform-card switches to catalog rows.

    Degrades OPEN on a failed settings read: this is a preference, not an
    authorisation (owner-private rows are already excluded by
    ``list_enabled(viewer_user_id=…)``), and hiding every model because the
    settings row could not be read would turn a blip into "the picker is
    empty". Logged, never silent.
    """
    rows = list(rows)
    try:
        allowed, disabled = await platform_model_gate(user_id)
    except Exception as e:  # noqa: BLE001 — degraded, logged, not swallowed
        logger.error("platform model gate failed for user %s: %s", user_id, e)
        return rows
    if not allowed:
        return []
    return [r for r in rows if str(r.get("name") or "") not in disabled]
