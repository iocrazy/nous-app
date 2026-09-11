"""``output_ref`` 附件——人指着一个产出的**某一版**说话（三期 3a Task 4）。

与它并排的两个解析器（``resource_ref`` / ``asset_ref``）交付的是**东西**：
一份文件、一个资产实体，以及模型可以去取的坐标。这一个交付的是**引用**：
「你上次产的那一版」。所以它渲染出来的框（``<referenced_outputs>``）里只有
坐标和标题，**没有内容**——内容用模型本来就有的工具去读。

三条各自独立的纪律，别读串：

**一、不可解析必须是类型化 400，不是静默丢。**
「触发路径必须类型化失败回显」。这里刻意**不用** ``attachment_failures``
那套「部分失败照跑」的口径：那套对「12 张图里有 4 张读不了」是对的，对引用
不成立——用户引用的是一个**具体版本**，解析不了就说明他们指的东西和我们
理解的不是一回事，让这一轮带着一条错误的引用跑下去比拒绝更糟。

**二、归属查的是 run 的 issue，不是表上的列。**
``run_deliverables`` 没有 ``issue_id`` 列，``agent_runs.issue_id`` 是唯一真相
（spec §3）。所以校验走 T3 的 ``lineage_for``——它已经把那一跳 join 做完了，
在这里重写一遍 join 就是本仓的「同一个问题两处实现」漂移。

**三、发帖时把标题抄一份到附件上。**
线程渲染因此不需要第二次查询；抄的是**那一刻**登记表里的标题，之后对象改名
不会追改这条引用——引用记录的是人当时指的那个东西。

⚠️ **解析在发帖口，投影在轮次里。** ``resolve_output_refs`` 是权威的那一次
（查库、校验、盖标题）；``output_refs_from_attachments`` 是纯投影，不查库，
读的就是发帖口盖好的那份。这个分工的安全前提写在投影函数的 docstring 里。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, List, Optional, Sequence, Tuple

from loguru import logger

from app.repositories.run_deliverables_repository import (
    get_run_deliverables_repository,
)
from app.services.deliverables.kinds import ALL_KINDS

#: 附件 wire 形状的 ``kind``。前端 composer 的引用 chip 按它写（T6 契约）。
ATTACHMENT_KIND = "output_ref"

#: 两个拒绝码。都是 400，都经 ``detail`` dict 落到 ``ErrorResponse.details``。
UNRESOLVABLE = "output_ref_unresolvable"
LIMIT_EXCEEDED = "output_ref_limit_exceeded"

#: 一条评论最多引用几个产出版本。
#:
#: **每个被引对象是一次 ``lineage_for`` 查询**（同一对象的多版合并成一次），
#: 所以这是数据库往返的上限，和 ``asset_ref`` 那个 8 是同一类理由、**不是**
#: 同一个常量——两者封的成本不同，耦在一起会让一个为另一个的理由而动。
#:
#: 上限**只活在前端选择器里**正是本仓记过的那类缺口，所以服务端也封，且超出
#: 是类型化拒绝而不是静默截断。
MAX_OUTPUT_REF_ATTACHMENTS = 8


@dataclass(frozen=True)
class ChatOutputRef:
    """一条引用：产出的四类之一 + 对象 id + 版本号 + 发帖时的标题快照。

    ``title`` 可以是 ``None``——登记表允许标题为空，渲染时整个属性省略而不是
    渲染成空串（``title=""`` 读起来像「标题就是空字符串」）。
    """

    ref_kind: str
    ref_id: str
    version: int
    title: Optional[str] = None


@dataclass(frozen=True)
class OutputRefResolution:
    """``resolve_output_refs`` 的结果。

    ``attachments`` 是**新的一份**，不是就地改过的入参——引用解析不该让调用方
    手里那份列表在他们不知情的时候变了（不可变纪律）。没有任何引用时它就是
    入参本身（无需复制，因为什么都没改）。
    """

    refs: Tuple[ChatOutputRef, ...]
    attachments: Optional[List[dict]]


class OutputRefRefused(Exception):
    """一条引用过不了校验。``code`` 是给客户端读的，``message`` 说人话。

    调用方（路由）把它翻成 400 + ``detail`` **dict**：生产把每个
    ``HTTPException`` 包进 ``ErrorResponse``，只有 dict 的 detail 会原样落到
    ``details``；字符串会塌成 ``http_400`` + "400 Bad Request"，类型化的 code
    就没了（CLAUDE.md 2026-09-09）。
    """

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message


def _is_citation(att: Any) -> bool:
    return isinstance(att, dict) and att.get("kind") == ATTACHMENT_KIND


def _coordinates(att: dict) -> Tuple[str, str, int]:
    """一条引用的三个坐标，形状不对就地拒绝。

    kind 先查、且**在碰数据库之前**查：四类之外是接线错误，不该先花一次查询
    才发现，而且一个任意 kind 字符串不该变成一次带它去查表的机会。
    """
    ref_kind = str(att.get("ref_kind") or "").strip()
    if ref_kind not in ALL_KINDS:
        raise OutputRefRefused(
            UNRESOLVABLE, f"{ref_kind!r} is not a kind of output that gets registered"
        )
    ref_id = str(att.get("ref_id") or "").strip()
    if not ref_id:
        raise OutputRefRefused(UNRESOLVABLE, f"a {ref_kind} citation carries no ref_id")
    raw_version = att.get("version")
    try:
        version = int(raw_version)
    except (TypeError, ValueError):
        raise OutputRefRefused(
            UNRESOLVABLE,
            f"{ref_kind}/{ref_id}: {raw_version!r} is not a version number — a "
            "citation names one specific version",
        ) from None
    if version < 1:
        raise OutputRefRefused(
            UNRESOLVABLE, f"{ref_kind}/{ref_id}: version {version} does not exist"
        )
    return ref_kind, ref_id, version


def _verified(
    coord: Tuple[str, str, int], chain: Sequence[dict], *, issue_id: Any
) -> ChatOutputRef:
    """版本链里挑出被引的那一版，并确认它是**本 issue** 产的。

    引别的 issue 的产出用同一个 code：对客户端而言两者都是「这条引用用不了」，
    而分出一个「存在但你不能引」的 code 等于确认了那个对象存在。消息正文说得
    出原因，那是给日志和开发者看的。
    """
    ref_kind, ref_id, version = coord
    row = next(
        (r for r in chain if int(r.get("version") or 0) == version),
        None,
    )
    if row is None:
        raise OutputRefRefused(
            UNRESOLVABLE, f"{ref_kind}/{ref_id} has no version {version}"
        )
    row_issue = row.get("issue_id")
    if row_issue is None or str(row_issue) != str(issue_id):
        raise OutputRefRefused(
            UNRESOLVABLE,
            f"{ref_kind}/{ref_id} v{version} was not produced on this issue",
        )
    title = row.get("title")
    return ChatOutputRef(
        ref_kind=ref_kind,
        ref_id=ref_id,
        version=version,
        title=str(title) if title is not None else None,
    )


def _stamped(ref: ChatOutputRef) -> dict:
    """存进消息 ``attachments`` 的形状（T6 的 chip 按它渲染）。

    只有这五个键——客户端随附件发来的任何其它字段都不进存储：存下来的东西
    是我们自己校验过的坐标，不是客户端说了什么。``title`` 为空时**整个键省略**
    （与渲染层「缺席不是空串」同一口径）。
    """
    out = {
        "kind": ATTACHMENT_KIND,
        "ref_kind": ref.ref_kind,
        "ref_id": ref.ref_id,
        "version": ref.version,
    }
    if ref.title is not None:
        out["title"] = ref.title
    return out


async def resolve_output_refs(
    attachments: Optional[Sequence[Any]], *, issue_id: Any
) -> OutputRefResolution:
    """校验本次评论里的每条引用，并把登记表的标题盖上去。

    任何一条过不了就整体 ``OutputRefRefused``——见模块 docstring 的纪律一。
    """
    items = list(attachments or [])
    cited = [(idx, att) for idx, att in enumerate(items) if _is_citation(att)]
    if not cited:
        # 一条引用都没有：原样交回去（``None`` 保持 ``None``——调用方用它区分
        # 「没有附件」和「有一份空表」）。
        return OutputRefResolution(
            refs=(), attachments=None if attachments is None else items
        )

    if len(cited) > MAX_OUTPUT_REF_ATTACHMENTS:
        raise OutputRefRefused(
            LIMIT_EXCEEDED,
            f"{len(cited)} referenced outputs in one comment — at most "
            f"{MAX_OUTPUT_REF_ATTACHMENTS} may be cited at a time",
        )

    coords = [_coordinates(att) for _idx, att in cited]

    repo = get_run_deliverables_repository()
    chains: dict[Tuple[str, str], List[dict]] = {}
    for ref_kind, ref_id, _version in coords:
        key = (ref_kind, ref_id)
        if key not in chains:
            # 同一对象被引多版只查一次：整条链一次就取回来了。
            chains[key] = list(await repo.lineage_for(kind=ref_kind, ref_id=ref_id))

    refs = tuple(
        _verified(coord, chains[(coord[0], coord[1])], issue_id=issue_id)
        for coord in coords
    )

    stamped = list(items)
    for (idx, _att), ref in zip(cited, refs):
        stamped[idx] = _stamped(ref)
    return OutputRefResolution(refs=refs, attachments=stamped)


def output_refs_from_attachments(
    attachments: Optional[Sequence[Any]],
) -> List[ChatOutputRef]:
    """本轮附件里的引用，**纯投影，不查库**。

    读的是 ``resolve_output_refs`` 在发帖口盖好的那份坐标，所以这里不重复
    校验。安全上站得住是因为这个框**只交付坐标**：即使某条路径送进来一条没
    校验过的引用，模型拿到的也只是一个 kind/id/version 和一个经
    ``escape_frame_attr`` 转义过的标题——没有内容、没有权限、没有可被伪造的
    结构。登记表校验存在的意义是**让用户被告知**，不是替模型挡内容。

    形状不成立的条目跳过并记一条 warning：它只能来自「发帖口没校验就落库」的
    接线漂移，静默跳过会让那种漂移永远不被发现。
    """
    out: List[ChatOutputRef] = []
    for att in attachments or []:
        if not _is_citation(att):
            continue
        try:
            ref_kind, ref_id, version = _coordinates(att)
        except OutputRefRefused as exc:
            logger.warning(
                f"[output_ref] dropping an unvalidated citation from a turn: {exc}"
            )
            continue
        title = att.get("title")
        out.append(
            ChatOutputRef(
                ref_kind=ref_kind,
                ref_id=ref_id,
                version=version,
                title=str(title) if title is not None else None,
            )
        )
    return out


__all__ = [
    "ATTACHMENT_KIND",
    "LIMIT_EXCEEDED",
    "MAX_OUTPUT_REF_ATTACHMENTS",
    "UNRESOLVABLE",
    "ChatOutputRef",
    "OutputRefRefused",
    "OutputRefResolution",
    "output_refs_from_attachments",
    "resolve_output_refs",
]
