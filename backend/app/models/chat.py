"""Chat / conversations domain ORM models.

Added table-by-table as each raw-SQL repo converges on the ORM-model style:

  * ``ConversationMemory`` — conversation_memory (mig 330; rolling
    head-summary sidecar, one row per conversation)
  * ``Conversations`` / ``ConversationMembers`` / ``Messages`` /
    ``MessageAttachments`` — the canonical conversation timeline (mig 327;
    style batch 3b)

No scope mixin — these are backend-only service-role tables; membership
checks live in the service layer, so the choke point stays inert.

conversation_repository keeps its text() SQL bodies (atomic seq allocation,
TOCTOU owner transfer, joined-gate keyset reads — SQL is the semantics per
the convergence doctrine) and runs them on read_scope()/write_scope()
sessions; these models document the schema and serve future ORM statements.
"""

from __future__ import annotations

import datetime
import uuid

from sqlalchemy import (
    BigInteger,
    Boolean,
    DateTime,
    ForeignKeyConstraint,
    Integer,
    PrimaryKeyConstraint,
    Text,
    UniqueConstraint,
    Uuid,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.db.orm_base import Base


class ConversationMemory(Base):
    """Rolling head-summary for a conversation (mig 330). PK = conversation_id
    (one sidecar row per conversation)."""

    __tablename__ = "conversation_memory"
    __table_args__ = (
        ForeignKeyConstraint(
            ["conversation_id"],
            ["public.conversations.id"],
            ondelete="CASCADE",
            name="conversation_memory_conversation_id_fkey",
        ),
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


class Conversations(Base):
    """Conversation heads (mig 327). ``title`` is the DB column; the public
    repo interface bridges it to the ``name`` dict key at the SQL boundary."""

    __tablename__ = "conversations"
    __table_args__ = (
        ForeignKeyConstraint(
            ["scope_id"],
            ["public.teams.id"],
            ondelete="CASCADE",
            name="conversations_scope_id_fkey",
        ),
        PrimaryKeyConstraint("id", name="conversations_pkey"),
        {"schema": "public"},
    )

    id: Mapped[int] = mapped_column(
        BigInteger, primary_key=True, server_default=text("generate_snowflake_id()")
    )
    type: Mapped[str] = mapped_column(Text, nullable=False)
    scope_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    project_id: Mapped[int | None] = mapped_column(BigInteger)
    title: Mapped[str | None] = mapped_column(Text)
    topic: Mapped[str | None] = mapped_column(Text)
    history_mode: Mapped[str] = mapped_column(
        Text, nullable=False, server_default=text("'shared'::text")
    )
    last_seq: Mapped[int] = mapped_column(
        BigInteger, nullable=False, server_default=text("0")
    )
    created_by: Mapped[uuid.UUID] = mapped_column(Uuid, nullable=False)
    archived_at: Mapped[datetime.datetime | None] = mapped_column(DateTime(True))
    created_at: Mapped[datetime.datetime] = mapped_column(
        DateTime(True), nullable=False, server_default=text("now()")
    )


class ConversationMembers(Base):
    """Dual-FK membership rows (mig 327): (member_type='user', user_id) or
    (member_type='agent', agent_id).

    The DB table has NO primary key — uniqueness is the expression index
    ``uq_conversation_members (conversation_id, member_type,
    COALESCE(user_id, agent_id))``, which SQLAlchemy cannot declare. The
    composite PK below is mapping-level only (the mapper requires one);
    all live queries are text() SQL, so it never drives identity logic.
    """

    __tablename__ = "conversation_members"
    __table_args__ = (
        ForeignKeyConstraint(
            ["agent_id"],
            ["public.ai_agents.id"],
            ondelete="CASCADE",
            name="conversation_members_agent_id_fkey",
        ),
        ForeignKeyConstraint(
            ["conversation_id"],
            ["public.conversations.id"],
            ondelete="CASCADE",
            name="conversation_members_conversation_id_fkey",
        ),
        {"schema": "public"},
    )

    conversation_id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    member_type: Mapped[str] = mapped_column(Text, primary_key=True)
    user_id: Mapped[uuid.UUID | None] = mapped_column(Uuid, primary_key=True)
    agent_id: Mapped[uuid.UUID | None] = mapped_column(Uuid, primary_key=True)
    role: Mapped[str] = mapped_column(
        Text, nullable=False, server_default=text("'member'::text")
    )
    last_read_seq: Mapped[int] = mapped_column(
        BigInteger, nullable=False, server_default=text("0")
    )
    mention_count: Mapped[int] = mapped_column(
        Integer, nullable=False, server_default=text("0")
    )
    notify_level: Mapped[str] = mapped_column(
        Text, nullable=False, server_default=text("'all'::text")
    )
    open: Mapped[bool] = mapped_column(
        Boolean, nullable=False, server_default=text("true")
    )
    added_by: Mapped[uuid.UUID | None] = mapped_column(Uuid)
    joined_at: Mapped[datetime.datetime] = mapped_column(
        DateTime(True), nullable=False, server_default=text("now()")
    )


class Messages(Base):
    """Conversation timeline messages (mig 327). Strict per-conversation
    ordering via the (conversation_id, seq) unique pair; seq is allocated
    atomically by ``UPDATE conversations SET last_seq = last_seq + 1``."""

    __tablename__ = "messages"
    __table_args__ = (
        ForeignKeyConstraint(
            ["conversation_id"],
            ["public.conversations.id"],
            ondelete="CASCADE",
            name="messages_conversation_id_fkey",
        ),
        ForeignKeyConstraint(
            ["parent_id"],
            ["public.messages.id"],
            ondelete="SET NULL",
            name="messages_parent_id_fkey",
        ),
        PrimaryKeyConstraint("id", name="messages_pkey"),
        UniqueConstraint(
            "conversation_id", "seq", name="messages_conversation_id_seq_key"
        ),
        {"schema": "public"},
    )

    id: Mapped[int] = mapped_column(
        BigInteger, primary_key=True, server_default=text("generate_snowflake_id()")
    )
    conversation_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    seq: Mapped[int] = mapped_column(BigInteger, nullable=False)
    parent_id: Mapped[int | None] = mapped_column(BigInteger)
    sender_type: Mapped[str] = mapped_column(
        Text, nullable=False, server_default=text("'user'::text")
    )
    sender_id: Mapped[uuid.UUID | None] = mapped_column(Uuid)
    from_agent_id: Mapped[uuid.UUID | None] = mapped_column(Uuid)
    type: Mapped[str] = mapped_column(
        Text, nullable=False, server_default=text("'text'::text")
    )
    body: Mapped[dict] = mapped_column(
        JSONB, nullable=False, server_default=text("'{}'::jsonb")
    )
    edited_at: Mapped[datetime.datetime | None] = mapped_column(DateTime(True))
    deleted_at: Mapped[datetime.datetime | None] = mapped_column(DateTime(True))
    created_at: Mapped[datetime.datetime] = mapped_column(
        DateTime(True), nullable=False, server_default=text("now()")
    )


class MessageAttachments(Base):
    """Message → generated_media attachment junction (mig 327).
    PK (message_id, generated_media_id); ``ord`` preserves picker order."""

    __tablename__ = "message_attachments"
    __table_args__ = (
        ForeignKeyConstraint(
            ["message_id"],
            ["public.messages.id"],
            ondelete="CASCADE",
            name="message_attachments_message_id_fkey",
        ),
        PrimaryKeyConstraint(
            "message_id", "generated_media_id", name="message_attachments_pkey"
        ),
        {"schema": "public"},
    )

    message_id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    generated_media_id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    ord: Mapped[int] = mapped_column(Integer, nullable=False, server_default=text("0"))


class ConversationAiMeta(Base):
    """AI-session decoration for direct_agent conversations (mig 333 retired
    ai_sessions in favour of this sidecar). PK = conversation_id (one row per
    direct_agent conversation)."""

    __tablename__ = "conversation_ai_meta"
    __table_args__ = (
        ForeignKeyConstraint(
            ["conversation_id"],
            ["public.conversations.id"],
            ondelete="CASCADE",
            name="conversation_ai_meta_conversation_id_fkey",
        ),
        PrimaryKeyConstraint("conversation_id", name="conversation_ai_meta_pkey"),
        {
            "comment": (
                "AI-session decoration for direct_agent conversations (agent "
                "binding, token/message counters, client grouping hints). Phase 2 "
                "sidecar; one row per direct_agent conversation. Backend-only "
                "(service-role RLS)."
            ),
            "schema": "public",
        },
    )

    conversation_id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    agent_slug: Mapped[str] = mapped_column(Text, nullable=False)
    total_tokens: Mapped[int] = mapped_column(
        BigInteger, nullable=False, server_default=text("0")
    )
    message_count: Mapped[int] = mapped_column(
        Integer, nullable=False, server_default=text("0")
    )
    updated_at: Mapped[datetime.datetime] = mapped_column(
        DateTime(True), nullable=False, server_default=text("now()")
    )
    agent_id: Mapped[uuid.UUID | None] = mapped_column(Uuid)
    context_type: Mapped[str | None] = mapped_column(Text)
    context_id: Mapped[str | None] = mapped_column(Text)


class MessageRefs(Base):
    """Message → arbitrary entity references (@-mentions, linked resources).
    ``ref_id`` is text so it can carry both snowflake ids and slugs."""

    __tablename__ = "message_refs"
    __table_args__ = (
        ForeignKeyConstraint(
            ["message_id"],
            ["public.messages.id"],
            ondelete="CASCADE",
            name="message_refs_message_id_fkey",
        ),
        PrimaryKeyConstraint(
            "message_id", "ref_type", "ref_id", name="message_refs_pkey"
        ),
        {"schema": "public"},
    )

    message_id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    ref_type: Mapped[str] = mapped_column(Text, primary_key=True)
    ref_id: Mapped[str] = mapped_column(Text, primary_key=True)
