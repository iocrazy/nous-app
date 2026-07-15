"""Chat / conversations domain ORM models.

Added table-by-table as each raw-SQL repo converges on the ORM-model style:

  * ``ConversationMemory`` — conversation_memory (mig 330; rolling
    head-summary sidecar, one row per conversation)

The conversations / messages / conversation_members / message_attachments
models follow when conversation_repository converges (style batch 3). No scope
mixin — these are backend-only service-role tables; membership checks live in
the service layer, so the choke point stays inert.
"""

from __future__ import annotations

import datetime

from sqlalchemy import (
    BigInteger,
    DateTime,
    PrimaryKeyConstraint,
    Text,
    text,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.db.orm_base import Base


class ConversationMemory(Base):
    """Rolling head-summary for a conversation (mig 330). PK = conversation_id
    (one sidecar row per conversation)."""

    __tablename__ = "conversation_memory"
    __table_args__ = (
        PrimaryKeyConstraint("conversation_id", name="conversation_memory_pkey"),
        {"schema": "public"},
    )

    conversation_id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    summary_md: Mapped[str] = mapped_column(
        Text, nullable=False, server_default=text("''::text")
    )
    last_seq_summarized: Mapped[int] = mapped_column(
        BigInteger, nullable=False, server_default=text("0")
    )
    model: Mapped[str | None] = mapped_column(Text)
    updated_at: Mapped[datetime.datetime] = mapped_column(
        DateTime(True), nullable=False, server_default=text("now()")
    )
