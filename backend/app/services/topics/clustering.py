"""Cross-source hotspot clustering parameters + decision.

A new hotspot is folded into an existing topic_group when its embedding's cosine
similarity to the group centroid clears the threshold, within a recent window.
Tight threshold + short window: prefer missing a merge over wrongly merging two
distinct events that happen to share words.
"""

from __future__ import annotations

# Cosine similarity at/above which two hotspots are "the same topic".
SIMILARITY_THRESHOLD = 0.85
# Only cluster against groups/hotspots seen within this many hours — hotspots are
# time-bound; a same-named topic days apart is usually a different event.
WINDOW_HOURS = 48
# Hotspots to (attempt to) cluster per tick. The feed catches up over ticks.
CLUSTER_MAX_ITEMS = 60


def is_match(similarity: float | None, threshold: float = SIMILARITY_THRESHOLD) -> bool:
    """True when similarity clears the merge threshold. None/garbage → False."""
    try:
        return similarity is not None and float(similarity) >= threshold
    except (TypeError, ValueError):
        return False
