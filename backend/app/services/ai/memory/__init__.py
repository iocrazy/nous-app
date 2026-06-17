"""AI memory layers.

The home-grown L1 ``agent_memories`` layer has been removed. The two
durable memory layers live here as standalone modules:

    honcho_memory  — Honcho user model (working representation / observations)
    graph_memory   — Graphiti temporal knowledge graph

``memory_prefs`` holds the per-user learn/inject toggles shared by both
the write path (write_memory workflow) and the chat recall wiring.
"""

from __future__ import annotations
