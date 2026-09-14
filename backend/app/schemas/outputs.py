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

from typing import List, Literal, Optional

from pydantic import BaseModel, ConfigDict, Field


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
    # 这条链的水位：最新登记行的 transcript seq。人手登记的版本不落事件、没有
    # seq，退回它的行 id——两者都单调，前端只拿它比大小丢过期信号（3b §4）。
    as_of_seq: int
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
