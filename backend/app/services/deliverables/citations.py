"""把一条评论里的 ``output_ref`` 附件镜像进 ``output_citations``（3c §2.2）。

**写点唯一。** 三条入口（议题评论、唤醒注入、聊天面板守卫）最终都经
``ConversationsAiStore.append_user_message`` 落库，所以镜像只挂在那一个地方。
每个入口各镜像一次会让「引用发生过」这件事有三份互不相认的实现，而其中任何
一份漏掉都不会报错 —— 只会让反查少一条，没人说得出少在哪。

**偏离 1：``session`` 允许 ``None``。** ``ConversationRepository.send_message``
自开自提交事务、不交出 handle，所以镜像发生在消息**落库之后**，拿不到调用方
的事务。形状 ``insert_many(rows, *, session)`` 保留：将来写点能借到事务时，改
的是这里的一个实参，不是 repository 的签名。

**偏离 2：失败只记 ERROR，不回滚消息。** 评论已经发出去了；让一次镜像写失败
把一条已经落库的评论判成失败，正是「投影不该否决内容」那条纪律（同
``SearchDocsRepository.upsert`` 的 docstring）。代价是反查可能少一条，日志说得
出是哪一条 —— 与静默吞错的区别就在这句话上。
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional, Sequence

from loguru import logger

from app.repositories.output_citations_repository import (
    get_output_citations_repository,
)
from app.services.ai.chat.output_ref_resolver import ATTACHMENT_KIND


def _rows(
    attachments: Optional[Sequence[Any]],
    *,
    message_id: Any,
    user_id: Any,
    issue_id: Any,
    conversation_id: Any,
) -> List[Dict[str, Any]]:
    """镜像行，同一批里的重复坐标去掉。

    去重在这里而不是靠 UNIQUE 兜底：让它走到数据库再撞约束，等于每次写入都
    赌 INSERT 的失败模式（而 ``ON CONFLICT DO NOTHING`` 会把一次真正的重复写
    和一次接线错误说成同一件事）。
    """
    out: List[Dict[str, Any]] = []
    seen: set = set()
    for att in attachments or []:
        if not isinstance(att, dict) or att.get("kind") != ATTACHMENT_KIND:
            continue
        try:
            key = (str(att["ref_kind"]), str(att["ref_id"]), int(att["version"]))
        except (KeyError, TypeError, ValueError):
            # 只可能来自「发帖口没校验就落库」的接线漂移。静默跳过会让那种漂移
            # 永远不被发现（与 output_refs_from_attachments 同一口径）。
            logger.warning(f"[citations] dropping an unusable citation: {att!r}")
            continue
        if key in seen:
            continue
        seen.add(key)
        out.append(
            {
                "kind": key[0],
                "ref_id": key[1],
                "version": key[2],
                "issue_id": int(issue_id) if issue_id is not None else None,
                "conversation_id": (
                    int(conversation_id) if conversation_id is not None else None
                ),
                "message_id": int(message_id),
                "cited_by_user_id": str(user_id),
            }
        )
    return out


async def record_output_citations(
    session: Any,
    *,
    message_row: Dict[str, Any],
    attachments: Optional[Sequence[Any]],
    user_id: Any,
    issue_id: Any,
    conversation_id: Any,
) -> None:
    """一条消息的引用 → 镜像表。没有引用时一次往返都不发。"""
    try:
        rows = _rows(
            attachments,
            message_id=message_row.get("id"),
            user_id=user_id,
            issue_id=issue_id,
            conversation_id=conversation_id,
        )
        if not rows:
            return
        await get_output_citations_repository().insert_many(rows, session=session)
    except Exception as exc:  # noqa: BLE001 — 见模块 docstring 的偏离 2
        logger.opt(exception=True).error(
            f"[citations] output_citations mirror FAILED for message "
            f"{message_row.get('id')}: {exc!r} — the comment posted, the "
            "back-reference did not"
        )


__all__ = ["record_output_citations"]
