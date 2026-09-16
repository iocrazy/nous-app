"""检索投影的值对象（3c §2.1）。``search_docs`` 是**投影**不是真相：丢了可以从
``agent_runs`` / ``run_deliverables`` 重建。所以每个坐标都可空——写不全某个坐标
时该写进去，不该整条不写。必填只有身份两列与 title（「搜得到」的下限）。

BODY_MAX_BYTES 是**字节**上限。截断而不是拒绝：搜不到长正文的尾巴，比这条投影
整个缺席好——后者会让这件产出在检索里不存在。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal, Optional

#: 正文的字节上限。``_clip`` 按它截，截在码点边界上。
BODY_MAX_BYTES = 8192


@dataclass(frozen=True)
class SearchDoc:
    """一条投影行。字段与 ``app/models/search.py::SearchDocs`` 的可写列一一对应。"""

    entity_kind: Literal["run", "output"]
    #: run 行 = ``str(agent_runs.id)``；产出行 = ``projection.output_entity_id()``。
    #: 两者刻意不同一个拼法，理由见那个函数的 docstring。
    entity_id: str
    title: str
    body: Optional[str] = None
    kind: Optional[str] = None
    ref_id: Optional[str] = None
    version: Optional[int] = None
    team_id: Optional[int] = None
    project_id: Optional[int] = None
    issue_id: Optional[int] = None
    run_id: Optional[int] = None
    owner_user_id: Optional[str] = None
    agent_id: Optional[str] = None
    model: Optional[str] = None
    status: Optional[str] = None
    error_code: Optional[str] = None
