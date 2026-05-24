"""Shared pgvector text-literal parsing for the memory subsystem.

The SQLAlchemy engine returns a pgvector column as its text literal
``[0.1,0.2,...]`` when selected via ``CAST(embedding AS text)`` — we avoid
registering an asyncpg vector codec on the shared hot-path engine. That
literal is valid JSON, so ``json.loads`` parses it. Tolerates an
already-decoded list too.

Single source of truth for both writers/readers of agent_memories
embeddings (memory writer + consolidation sweep), so the parse semantics
can't drift between them.
"""

from __future__ import annotations

import json
from typing import Any


def parse_embedding_text(raw: Any) -> list[float] | None:
    """Parse a pgvector text literal (or an already-decoded sequence) into a
    list of floats. Returns None on empty/missing/malformed input."""
    if not raw:
        return None
    try:
        seq = json.loads(raw) if isinstance(raw, str) else raw
        return [float(x) for x in seq]
    except (ValueError, TypeError):
        return None
