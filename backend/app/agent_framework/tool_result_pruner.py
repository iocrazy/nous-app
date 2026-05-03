"""Tool-result aging + dedupe — pre-compaction noise reduction.

Wave 5a (A4). Two cheap-but-high-impact passes that run BEFORE the
LLM-summarizer compaction step. They reduce token count without losing
semantic information that the agent actually needs:

  1. **Dedupe** — when the agent calls (tool_name, args) multiple times
     and gets the same/similar result, keep the first body and replace
     subsequent ones with a reference. Typical scenarios:
       - LLM re-reads the same file 5 times in a long thread
       - Loop guard slipped, same Skill called 3x
       - User asked "show me X" then forgot, asked again
     Saves 70-90% on duplicate tool results.

  2. **Aging** — tool_result bodies older than ``aging_after_turns``
     get squashed to a 1-line gist (first ~80 chars + length tag).
     Old tool calls are usually just context that the agent has
     already incorporated; the body's verbatim form is no longer needed.

Both passes preserve the tool_use ↔ tool_result PAIR — they only edit
the result body, never drop a result entirely. So API invariants the
existing ``_safe_split_index`` enforces are unaffected.

Both pass functions return a NEW messages list — caller's input is
not mutated.
"""
from __future__ import annotations

import hashlib
from dataclasses import dataclass


# Reference text that replaces a duplicate tool_result body. Includes
# the original tool_call_id so the agent can trace back if needed.
DUPLICATE_REFERENCE = (
    "[duplicate of earlier tool result tool_call_id={original_tcid}; "
    "body elided to save tokens]"
)

# Aging template: 1-line gist + length tag.
AGING_GIST = "[aged: {gist}... (original body was {chars} chars)]"


@dataclass(frozen=True)
class PruneStats:
    """Telemetry for a prune pass — useful for `agent_runs.metadata_json`."""

    duplicates_replaced: int = 0
    aged_results: int = 0
    chars_dropped: int = 0


def _hash_tool_call(call: dict) -> str:
    """Stable hash of (tool_name, canonicalized args)."""
    fn = call.get("function") or {}
    name = str(fn.get("name") or "")
    # Sort args by key inside the JSON if it's an object — same args in
    # different key order should hash identically.
    args = str(fn.get("arguments") or "")
    return hashlib.sha1(
        (name + "\n" + args).encode("utf-8"), usedforsecurity=False
    ).hexdigest()[:16]


def dedupe_tool_results(
    messages: list[dict],
) -> tuple[list[dict], PruneStats]:
    """Replace 2nd+ occurrences of (tool_name, args) tool_results with a
    reference to the first occurrence's tool_call_id.

    Walks messages in order:
      - Records hash → first tool_call_id seen on assistant tool_calls
      - For tool replies: looks up its OWN call's hash via tool_call_id
        and (if seen before) rewrites its content
    """
    # Map: tool_call_id (issued by assistant) → its hash
    call_hash_by_tcid: dict[str, str] = {}
    # Map: hash → FIRST tool_call_id we saw with that hash
    first_tcid_by_hash: dict[str, str] = {}
    duplicates_replaced = 0
    chars_dropped = 0

    new_msgs: list[dict] = []
    for msg in messages:
        if msg.get("role") == "assistant" and msg.get("tool_calls"):
            for call in msg["tool_calls"]:
                tcid = call.get("id") or ""
                h = _hash_tool_call(call)
                call_hash_by_tcid[tcid] = h
                if h not in first_tcid_by_hash:
                    first_tcid_by_hash[h] = tcid
            new_msgs.append(msg)
            continue

        if msg.get("role") == "tool":
            tcid = msg.get("tool_call_id") or ""
            h = call_hash_by_tcid.get(tcid)
            first = first_tcid_by_hash.get(h) if h else None
            if first and first != tcid:
                # 2nd+ occurrence — replace with reference
                original = msg.get("content") or ""
                if isinstance(original, str) and original:
                    chars_dropped += len(original)
                    new_msgs.append(
                        {
                            **msg,
                            "content": DUPLICATE_REFERENCE.format(
                                original_tcid=first
                            ),
                        }
                    )
                    duplicates_replaced += 1
                    continue
            new_msgs.append(msg)
            continue

        new_msgs.append(msg)

    return new_msgs, PruneStats(
        duplicates_replaced=duplicates_replaced,
        chars_dropped=chars_dropped,
    )


def age_old_tool_results(
    messages: list[dict],
    *,
    aging_after_turns: int = 10,
    max_body_chars: int = 1000,
    gist_chars: int = 80,
) -> tuple[list[dict], PruneStats]:
    """Squash tool_result bodies older than ``aging_after_turns`` to a
    1-line gist.

    "Older" = the message's index is more than ``aging_after_turns`` away
    from the END of the messages list. Bodies under ``max_body_chars``
    are left alone (they're already small).
    """
    n = len(messages)
    aged_results = 0
    chars_dropped = 0

    new_msgs: list[dict] = []
    for i, msg in enumerate(messages):
        if msg.get("role") != "tool":
            new_msgs.append(msg)
            continue
        if (n - 1 - i) < aging_after_turns:
            new_msgs.append(msg)
            continue
        content = msg.get("content")
        if not isinstance(content, str) or len(content) <= max_body_chars:
            new_msgs.append(msg)
            continue
        # Squash to 1-line gist
        gist = " ".join(content.split())[:gist_chars]
        chars_dropped += len(content) - len(gist) - len(AGING_GIST)
        new_msgs.append(
            {
                **msg,
                "content": AGING_GIST.format(gist=gist, chars=len(content)),
            }
        )
        aged_results += 1

    return new_msgs, PruneStats(
        aged_results=aged_results,
        chars_dropped=chars_dropped,
    )


def prune(
    messages: list[dict],
    *,
    aging_after_turns: int = 10,
    max_body_chars: int = 1000,
    gist_chars: int = 80,
) -> tuple[list[dict], PruneStats]:
    """Convenience: run dedupe then aging in sequence. Returns combined
    stats."""
    deduped, dedupe_stats = dedupe_tool_results(messages)
    aged, age_stats = age_old_tool_results(
        deduped,
        aging_after_turns=aging_after_turns,
        max_body_chars=max_body_chars,
        gist_chars=gist_chars,
    )
    return aged, PruneStats(
        duplicates_replaced=dedupe_stats.duplicates_replaced,
        aged_results=age_stats.aged_results,
        chars_dropped=dedupe_stats.chars_dropped + age_stats.chars_dropped,
    )


__all__ = [
    "AGING_GIST",
    "DUPLICATE_REFERENCE",
    "PruneStats",
    "age_old_tool_results",
    "dedupe_tool_results",
    "prune",
]
