"""Wire models for the产出血缘 endpoints (三期 3a spec §4).

Three shapes, one rule: **every id leaves as a string**. ``run_deliverables``
ids, ``run_id`` and ``issue_id`` are Snowflake BIGINTs, and PostgREST-style
JSON numbers above 2^53 lose precision the moment the browser parses them
(CLAUDE.md「Snowflake BIGINT 精度丢失」). The repository already stringifies
them; these models make that part of the contract instead of an accident of
one query.

``cost_cents`` is a float and may be ``None``, and ``cost_kind`` says where it
came from (3b §3.1): media rows carry the catalog per-call price they were
registered with (``exact``), text rows carry a read-time share of the step that
produced them (``allocated``), and a version with neither reports ``None`` on
both — the UI shows ``—`` rather than a fabricated 0.
"""

from __future__ import annotations

from datetime import datetime
from typing import List, Literal, Optional

from pydantic import BaseModel, ConfigDict, Field


class CitedIn(BaseModel):
    """一次引用发生在哪里（3c §2.2）。

    ``cited_in`` 只列**调用方看得见的那几条**：一条引用发生在一件议题上，而
    议题可见性是 ``visible_issue_ids`` 那把尺子的事。看不见的那几条整条不出现
    —— 让它们带着空 ``issue_id`` 留下，等于把 ``message_id`` / ``user_id``
    （谁、在哪条消息里引了它）交出去，而那正是可见性要挡的东西。

    计数不受这次裁剪影响：``OutputVersion.cited_count`` 是**全量**，所以
    「被引 3 次、你只看得见 1 条」是允许且正确的答案。

    两个字段仍是可空的：``issue_key`` 在一件没有 identifier 的议题上就是空的，
    而 ``issue_id`` 留出空位是给「引用不挂在任何议题上」那天用的——今天到不了
    这里（聊天面板的引用一律被拒），所以它不是一个可以依赖的分支。
    """

    issue_id: Optional[str] = None
    issue_key: Optional[str] = None
    message_id: str
    user_id: str
    at: datetime


class OutputVersion(BaseModel):
    """One row of ``run_deliverables``, decorated with the issue its run
    belonged to. All three issue fields are ``None`` for a run that answers to
    no issue (a canvas or chat lane run).

    ``issue_key`` (``MH-n``) and ``deep_link`` landed in 3a Task 3b. The panel
    cannot route on ``issue_id``: the issue page is ``/team/{team}/todolist/
    {key}``, so an id-only URL 404s or lands on an unrelated issue. The backend
    therefore hands over a finished link or none at all — built by the same
    ``issue_deep_link`` the Generated inbox card uses, so the two never drift::

        "issue_key": "MH-91",
        "deep_link": "/team/424242424242/todolist/MH-91?step=3&turn=2"

    ``deep_link`` is ``None`` when the run has no issue, when the issue has no
    identifier, or when it has no team — never a URL assembled from the
    snowflake. The team's own id does not cross the wire.
    """

    id: str
    version: int
    parent_version: Optional[int] = None
    run_id: Optional[str] = None
    #: 3b：人手登记的作者（回退是唯一的人手占号路径）。CHECK
    #: ``run_deliverables_run_or_actor`` 是 **OR 不是 XOR**：它只保证
    #: 「run_id 与 actor_user_id 不同时为空」，两个都非空是允许的（今天没有
    #: 写入方这么做，但契约上别当互斥读）。run_id 为空 ⇒ 这一列必非空。
    actor_user_id: Optional[str] = None
    #: 这一版是从哪一版回退来的。None = 正常前进的一版。
    reverted_from_version: Optional[int] = None
    issue_id: Optional[str] = None
    issue_key: Optional[str] = None
    deep_link: Optional[str] = None
    # The ``deliverable`` event's own seq on the run's transcript — the
    # pointer from this row to the moment it was registered. Written by
    # ``registry._stamp_seq`` right after the event lands (the row exists
    # first, so it cannot be known at insert time), and ``None`` whenever
    # no event was persisted: a run with no live writer, or an insert that
    # failed. Never a guess — null means 'not known', not 'step zero'.
    seq: Optional[int] = None
    turn: Optional[int] = None
    step: Optional[int] = None
    title: Optional[str] = None
    model: Optional[str] = None
    cost_cents: Optional[float] = None
    # 这个花费怎么来的（3b §3.1）。``exact`` 是登记时就知道的真价（媒体类的
    # 目录每次调用价）；``allocated`` 是产出它那一步的 LLM 花费按该步产出件数
    # 均摊出来的参考值，UI 显示 ``≈¢0.09``，**不是计费输入**；``None`` 与
    # ``cost_cents=None`` 同时出现，意思是没有花费可报。
    cost_kind: Optional[Literal["allocated", "exact"]] = None
    created_at: Optional[str] = None
    #: 这一版被引用过几次，**全量**（3c §2.2）。cited_in 只列调用方看得见的
    #: 那几条，所以「被引 3 次、你能看 1 条」是允许且正确的答案。
    #:
    #: **0 不是 None**：这两个字段由端点合成（不是登记行上的列），一条从没被
    #: 引用过的版本的诚实答案是「零次」，不是「不知道」。
    cited_count: int = 0
    cited_in: List[CitedIn] = []


class OutputObject(BaseModel):
    """Every version of ONE object. The panel's unit is the object, not the
    row: three revisions of one shot are one entry with three versions."""

    kind: str
    ref_id: str
    title: Optional[str] = None
    latest_version: int
    versions: List[OutputVersion]


class IssueOutputsResponse(BaseModel):
    items: List[OutputObject]


class OutputLineageResponse(BaseModel):
    kind: str
    ref_id: str
    latest_version: int
    #: newest registration row id of the chain, as a string; monotonic; the
    #: client compares with BigInt and only uses it to refuse overwriting a
    #: newer response with an older one.
    #:
    #: 一把尺子到底：``max(run_deliverables.id)``。刻意**不是** transcript
    #: ``seq`` —— 那是每个 run 内部的小整数，人手登记的版本根本没有，混用会让
    #: 「agent → 人手回退 → agent」这条链上的水位变小（3b §4，fix 轮 0）。
    #: 字段名保留 ``as_of_seq``：它对客户端的含义（这份响应有多新）没有变。
    #:
    #: **字符串出口**，同这个模型里每一个 id：Snowflake 超过 2^53，JSON number
    #: 一进浏览器就掉精度，而掉了精度的水位会把相邻两版判成同一版。
    as_of_seq: str
    versions: List[OutputVersion]


class OutputDiffMedia(BaseModel):
    """The媒体类 side of a diff: the row plus the two same-origin URLs the UI
    already knows how to render."""

    id: str
    media_kind: Optional[str] = None
    mime: Optional[str] = None
    cover_url: Optional[str] = None
    stream_url: Optional[str] = None


class OutputDiffSide(BaseModel):
    """One version, as content.

    ``available`` is its own field rather than "text is None": a version whose
    snapshot cannot be reconstructed and a version whose text is genuinely
    empty are different facts, and ``unavailable_reason`` says which one this
    is instead of leaving the panel to guess.
    """

    version: int
    #: 回退版没有 run（作者是人）。两侧都要能画，所以这里也是可空的。
    run_id: Optional[str] = None
    issue_id: Optional[str] = None
    #: 人手版自己答不出归属，由**这条链**补上（``newest_with_a_run``）——回退响应
    #: 与血缘端点早就这么做，diff 是第三个读者，不补的话同一版在面板上一会儿有
    #: 归属一会儿没有（3b fix A / Task 9 旁证 C）。
    issue_key: Optional[str] = None
    created_at: Optional[str] = None
    model: Optional[str] = None
    cost_cents: Optional[float] = None
    title: Optional[str] = None
    text: Optional[str] = None
    media: Optional[OutputDiffMedia] = None
    available: bool = True
    unavailable_reason: Optional[str] = None


class OutputDiffResponse(BaseModel):
    """``from`` is a Python keyword, so the field is ``from_`` with an alias.
    FastAPI serialises response models by alias, so the wire key is ``from``."""

    model_config = ConfigDict(populate_by_name=True)

    kind: str
    ref_id: str
    content_type: Literal["text", "media"]
    from_: OutputDiffSide = Field(alias="from")
    to: OutputDiffSide


# ── 回退（3b spec §2.3） ──────────────────────────────────────────────────


class RevertRequest(BaseModel):
    """``expected_latest`` 是乐观锁，不是可选的礼貌：两人同时回退同一个对象时，
    没有它后者会静默覆盖前者刚登记的那一版。"""

    to_version: int = Field(ge=1)
    expected_latest: int = Field(ge=1)


class RevertResponse(BaseModel):
    """``kept_version`` = 回退前把未登记的人手编辑登记成的那一版（spec §2.3）。
    多数回退是 None——只在当前内容与最新登记版不同时才出现。"""

    version: OutputVersion
    kept_version: Optional[OutputVersion] = None


__all__ = [
    "CitedIn",
    "IssueOutputsResponse",
    "OutputDiffMedia",
    "OutputDiffResponse",
    "OutputDiffSide",
    "OutputLineageResponse",
    "OutputObject",
    "OutputVersion",
    "RevertRequest",
    "RevertResponse",
]
