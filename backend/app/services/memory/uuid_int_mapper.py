"""UUID ↔ short-int mapping for LLM-facing memory references.

Mem Zero observation: LLMs are bad at long UUID strings — they hallucinate
characters, drop digits, swap segments. When the model needs to reference
"the third memory", it should see "[2]" not
"3f2504e0-4f89-11d3-9a0c-0305e82c3301".

Usage in retriever:

    mapper = UuidIntMapper.from_records(candidates)
    prompt = mapper.render_for_llm()      # gives the model "[0]: ...\\n[1]: ..."
    decision = await sonnet_filter(prompt)  # model returns e.g. "keep [0], [2]"
    selected = mapper.resolve_int_refs(decision)  # back to UUIDs

Stateless and per-call: a fresh mapper per recall avoids cross-session
collisions. The mapping itself is never persisted — it's purely a
prompt-rendering aid.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Iterable
from uuid import UUID

# Match [N] anywhere in LLM output. Tolerates whitespace, supports up to 9999.
_INT_REF_PATTERN = re.compile(r"\[(\d{1,4})\]")


@dataclass(frozen=True)
class UuidIntMapper:
    """Bidirectional map between display ints and real UUIDs.

    The display int is the position in the input list, 0-indexed. We never
    re-order: ``ids[0]`` is always shown to the LLM as ``[0]``, regardless
    of what scoring or sorting happened upstream.
    """

    ids: tuple[UUID, ...]

    @classmethod
    def from_records(cls, records: Iterable) -> "UuidIntMapper":
        """Build from any iterable of objects exposing ``.id`` (e.g. MemoryRecord)."""
        ids = tuple(getattr(r, "id") for r in records)
        return cls(ids=ids)

    @classmethod
    def from_uuids(cls, uuids: Iterable[UUID]) -> "UuidIntMapper":
        return cls(ids=tuple(uuids))

    def to_int(self, uuid_value: UUID) -> int:
        """Look up the display int for a UUID. Raises ValueError if not present."""
        try:
            return self.ids.index(uuid_value)
        except ValueError as exc:
            raise ValueError(f"UUID not in mapper: {uuid_value}") from exc

    def to_uuid(self, int_ref: int) -> UUID:
        """Look up the UUID for a display int."""
        if int_ref < 0 or int_ref >= len(self.ids):
            raise IndexError(f"int_ref {int_ref} out of range (size {len(self.ids)})")
        return self.ids[int_ref]

    def resolve_int_refs(self, text: str) -> list[UUID]:
        """Parse [N] tokens from LLM output and resolve to UUIDs.

        - Out-of-range refs are silently dropped + would be logged at the
          caller (not here — keep this pure).
        - Duplicate refs in the text yield duplicate UUIDs (caller dedupes
          if needed).
        - The result preserves the order in which refs appeared in the text.
        """
        result: list[UUID] = []
        for match in _INT_REF_PATTERN.finditer(text):
            try:
                ref = int(match.group(1))
            except ValueError:
                continue
            if 0 <= ref < len(self.ids):
                result.append(self.ids[ref])
        return result

    def __len__(self) -> int:
        return len(self.ids)


__all__ = ["UuidIntMapper"]
