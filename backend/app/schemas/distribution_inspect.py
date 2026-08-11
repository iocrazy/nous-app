"""只读页面勘探（T0）的 admin 入口契约。

上下界刻意**不在这里复述**：浏览器侧的 ``InspectRequest`` 已经把每个旋钮的
范围写死了，在这里再写一遍就是第二处声明 —— 而"同一件事声明在三个地方、靠
纪律同步"正是本次设计要连根拔掉的病（设计文档 §D1）。这里只声明 admin 这一
跳独有的东西：账号 id、以及探针文件用 **resource_id** 而不是 URL。

为什么探针文件收 resource_id 而不是 URL
=====================================
浏览器容器会去 GET 那个地址。收任意 URL 等于给一个持有用户 cookie 的容器加
一条"访问我指定的任意地址"的能力 —— 内网里那是 SSRF。收 resource_id 则让
URL 由 ``PublishTasksRepository.get_resource_media_url`` 生成（对象存储签名
URL / HMAC 签名的 /media/ URL），与发布链**同一条**解析路径，没有第二处签名
逻辑。
"""

from __future__ import annotations

from typing import Any, Optional

from pydantic import BaseModel, Field


class SessionInspectRequest(BaseModel):
    account_id: int
    url: str = Field(min_length=1, max_length=2048)
    # 为了让"先传图才渲染"的表单渲染出来而上传的探针文件。顺序即上传顺序。
    seed_resource_ids: list[int] = Field(default_factory=list, max_length=12)
    # 要数**精确匹配数**的文案。基础设计 §7.4.0 的口径：不等于 1 就是错的。
    text_probes: list[str] = Field(default_factory=list, max_length=40)
    selector_probes: list[str] = Field(default_factory=list, max_length=40)
    # 下面这些原样透传给浏览器侧，由那边的模型校验范围。None = 用那边的默认。
    seed_selector: Optional[str] = None
    seed_input_index: Optional[int] = None
    seed_wait_ms: Optional[int] = None
    settle_ms: Optional[int] = None
    excerpt_chars: Optional[int] = None
    budget_s: Optional[int] = None

    def passthrough_options(self) -> dict[str, Any]:
        return {
            "seed_selector": self.seed_selector,
            "seed_input_index": self.seed_input_index,
            "seed_wait_ms": self.seed_wait_ms,
            "settle_ms": self.settle_ms,
            "excerpt_chars": self.excerpt_chars,
            "budget_s": self.budget_s,
        }


class SessionInspectResponse(BaseModel):
    """§7.8 信封 + 观测摘要。

    **没有 storage_state 字段，而且不能有。** 明文会话只在 backend 进程内存里
    存在（spec §7.6）；``session_refreshed`` 是这条链允许对外说的全部 ——
    平台续期了、我们写回了。
    """

    success: bool
    status: str
    message: str
    detail: dict[str, Any] = Field(default_factory=dict)
    observation: dict[str, Any] = Field(default_factory=dict)
    session_refreshed: bool = False
