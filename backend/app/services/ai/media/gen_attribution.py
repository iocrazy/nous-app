"""生成结果 → 登记行的 (provider, model)。

``ImageGenResult`` / ``VideoGenResult``（``video_providers/base.py:6-27``）都带
这两个字段且三个 adapter 都填了真值；请求侧只是兜底——一边是目录行名，一边是
``_DEFAULT_MODEL`` 哨兵，都不是模型真名。哨兵 ``dall-e-3`` 的含义是「用目录行的
actual_model」（``image_generation_service.py:120-127``），写进登记行会让查价
落空，所以宁可 None——None 说「不知道」，哨兵说「就是这个模型」。
"""

from __future__ import annotations

#: ``image_generation_service._DEFAULT_IMAGE_MODEL`` 的同一个值。此处复制而不
#: import，是为了不让登记侧反向依赖生成服务；同源守卫在测试里。
DEFAULT_MODEL_SENTINEL = "dall-e-3"


def resolved_attribution(
    raw: dict | None,
    *,
    requested_provider: str | None,
    requested_model: str | None,
) -> tuple[str | None, str | None]:
    """``(provider, model)``：adapter 报的优先，请求值兜底，哨兵作废。"""
    provider = ((raw or {}).get("provider") or "").strip() or (
        requested_provider or ""
    ).strip()
    model = ((raw or {}).get("model") or "").strip() or (requested_model or "").strip()
    if model == DEFAULT_MODEL_SENTINEL:
        model = ""
    return (provider or None, model or None)


__all__ = ["DEFAULT_MODEL_SENTINEL", "resolved_attribution"]
