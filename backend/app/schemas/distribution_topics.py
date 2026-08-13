"""Distribution — 话题建议的响应形状。

失败**不走这里**：任何失败都是一个带 ``detail.reason`` 的 HTTP 错误（见
``services/distribution/topic_suggest.py`` 的类型化 reason），因为一个 200 +
空列表在 UI 上与"这个词没有话题"完全无法区分。
"""

from __future__ import annotations

from pydantic import BaseModel, Field


class TopicSuggestionOut(BaseModel):
    """一条平台话题建议。"""

    name: str
    # 平台的话题实体 id（抖音 ``cid``）。空串 = 平台还没有这个实体，此时
    # ``is_new`` 为 true —— 两者是同一件事的两种读法，保留 is_new 是为了让前端
    # 不必去理解"空串意味着什么"。
    topic_id: str = ""
    # 累计播放量原始整数。格式化（亿/万）是前端的事。
    view_count: int = 0
    is_new: bool = False


class TopicSuggestResponse(BaseModel):
    platform: str
    keyword: str
    suggestions: list[TopicSuggestionOut] = Field(default_factory=list)
    # 这一次是不是命中了进程内短 TTL 缓存。纯可观测信息，UI 不依赖它。
    cached: bool = False
