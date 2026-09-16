"""``GET /api/v1/search`` 的响应形状（3c 契约 §1）。

**为什么不在 ``schemas/search.py`` 里。** 那个模块已经有一个 ``SearchResponse``
——资源库的语义/混合检索（``/search/semantic`` 等五条）的响应，字段完全不同
（``results`` / ``videos`` / ``total`` / ``search_type``）。契约把本组模型的名字
定成 ``SearchResponse``，两者同名同模块不可能并存，而改任何一侧的名字都要动一批
既有调用方。分成两个模块是唯一不伤既有契约的做法：导入点各自写明白自己要的是
哪一个，不存在"猜是哪个 SearchResponse"的余地。

三组各自成列表而不是一条混排的流：三类东西的相关度不可比——议题的 trgm 相似度
和产出正文的 trgm 相似度不是同一把尺子上的数，混排等于用一个假的全序把用户
最想要的那一组压到下面。分组之后每组内部各自排序，读者自己挑组。
"""

from __future__ import annotations

from typing import Any, Dict, List, Literal, Optional

from pydantic import BaseModel, Field


class SearchHit(BaseModel):
    """一条命中。``id`` 是**不透明身份键**，一律字符串：issue / run 是
    Snowflake BIGINT（落进 JSON number 会在浏览器里丢精度），output 是
    ``kind:ref_id:version``（契约补充——一版一行，而「一版」没有可用的单列 id）。
    客户端只拿它做 React key 与去重，**不解析**：产出的三个坐标在 ``meta`` 里各占
    一个键，切 id 等于把拼法复制成第二份。``deep_link`` 是**空串**而不是 null 时
    表示「这条命中没有可跳转的页面」（无议题的个人 run），前端渲染不可点的行。
    ``meta`` —— run: {status, model, error_code}；output: {kind, ref_id, version}；
    issue: {status, assignee_user_id, assignee_agent_id}。

    ⚠️ 契约 §1 把议题的第三个键写作 ``assignee_name``，**本实现给的是两个 id
    而不是名字**：``issues`` 上只有 ``assignee_user_id`` / ``assignee_agent_id``，
    全仓没有任何地方产出过指派人的显示名。造一个永远是 ``null`` 的
    ``assignee_name`` 会让前端以为「这条没指派」，而真相是「这一层拿不到名字」
    ——两者在 UI 上长得一样，可差别正是用户会不会去点它。同理 run 的
    ``cost_cents`` / ``agent_name`` 与产出的 ``cited_count`` 也不在这里：
    ``search_docs`` 没有那几列（投影表的列见 ``models/search.py``），
    ``cited_count`` 属于 Task 16 的引用镜像。
    """

    kind: Literal["issue", "run", "output"]
    id: str
    title: str
    snippet: Optional[str] = None
    deep_link: str = ""
    issue_key: Optional[str] = None
    issue_id: Optional[str] = None
    meta: Dict[str, Any] = Field(default_factory=dict)


class SearchGroups(BaseModel):
    """三组命中。缺省是三个空列表，所以「一次什么都没搜到」和「这一组没请求」
    在形状上一样——区分它们的是调用方传了哪些 ``kinds``，不是响应里少一个键。"""

    issues: List[SearchHit] = Field(default_factory=list)
    runs: List[SearchHit] = Field(default_factory=list)
    outputs: List[SearchHit] = Field(default_factory=list)


class SearchTotals(BaseModel):
    """每组服务端一共有多少条匹配。议题组是 repository 的 total
    （精确）。run / 产出两组是**裁剪后这一页**的条数——投影表上没有便宜的 count，
    而一个会骗人的总数比没有更糟。UI 只对议题组显示「N of M」。"""

    issues: int = 0
    runs: int = 0
    outputs: int = 0


class UnifiedSearchResponse(BaseModel):
    """``took_ms`` 是服务端自己量的墙钟，含两道可见性门。它不是性能装饰：
    检索是交互式的，慢下来时读者要能分清是网络还是这个端点。

    类名带 ``Unified`` 前缀而契约写的是 ``SearchResponse``：见模块 docstring，
    ``schemas/search.py`` 那个名字已经被资源库检索占了。本模块导出
    ``SearchResponse`` 作为别名，所以按契约那个名字 import 也拿得到同一个类。
    """

    groups: SearchGroups
    totals: SearchTotals
    took_ms: int


#: 契约 §1 的拼法。同一个类，两个名字——别名让契约里的写法可用，而带前缀的
#: 真名让它在 ``schemas/search.py`` 的 ``SearchResponse`` 旁边不产生歧义。
SearchResponse = UnifiedSearchResponse

__all__ = [
    "SearchGroups",
    "SearchHit",
    "SearchResponse",
    "SearchTotals",
    "UnifiedSearchResponse",
]
