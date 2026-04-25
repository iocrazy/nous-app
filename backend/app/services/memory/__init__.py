"""Memory v1 — namespace types + record DTO.

5-layer namespace (M1.B writes only ``agent_user``; others reserved for M2/M3
per plan-eng-review Cross-Model Tension #2):

    session       — per ai_session, ephemeral working memory
    agent_user    — per (user × agent), durable user-facing memory  ← M1.B writes
    user_global   — per user, cross-agent (e.g. style preferences)
    team_agent    — per (team × agent), shared org knowledge
    root_tree     — per workforce root run, cross-agent within one delegation tree

Embedding strategy (RemiMem pattern):
    The embedding vector is built on ``when_to_use``, NOT on ``summary``.
    Reasoning: user queries semantically resemble "when would this fact help?"
    more than they resemble the fact itself. Higher recall.

Salience scoring (MemU pattern):
    final_score = 0.7 * cosine_similarity + 0.3 * log(1 + reinforcement_count)
    Each recall ++ reinforcement_count and updates last_recalled_at.

Extraction lineage (Mem Zero pattern):
    extracted_from in {'user_msg', 'assistant_msg'} prevents the agent's
    self-narration from polluting user-facing memory.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import Optional
from uuid import UUID


class MemoryScope(str, Enum):
    """Namespace layer. Stored as TEXT in agent_memories.scope."""

    SESSION = "session"
    AGENT_USER = "agent_user"
    USER_GLOBAL = "user_global"
    TEAM_AGENT = "team_agent"
    ROOT_TREE = "root_tree"


class ExtractedFrom(str, Enum):
    USER_MSG = "user_msg"
    ASSISTANT_MSG = "assistant_msg"


@dataclass(frozen=True)
class MemoryRecord:
    """In-memory representation of one agent_memories row.

    Frozen to keep the retrieval pipeline pure — composing/sorting
    candidates never mutates the underlying data.
    """

    id: UUID
    agent_id: UUID
    user_id: UUID
    scope: MemoryScope
    summary: str
    when_to_use: str
    extracted_from: Optional[ExtractedFrom]
    reinforcement_count: int
    last_recalled_at: Optional[datetime]
    created_at: datetime
    metadata: dict = field(default_factory=dict)


__all__ = [
    "ExtractedFrom",
    "MemoryRecord",
    "MemoryScope",
]
