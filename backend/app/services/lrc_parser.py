"""Parse LRC lyrics text into the {lrc, lines} shape the frontend LyricsView
consumes. Tolerates metadata tags ([ar:]/[ti:]/[al:]/[by:]/[offset:]),
multi-timestamp lines, 2- or 3-digit milliseconds, and plain (untimed) text."""

import re
from typing import Optional, TypedDict

_TS = re.compile(r"\[(\d{1,2}):(\d{2})(?:[.:](\d{1,3}))?\]")
_META = re.compile(r"^\[[a-zA-Z]+:")


class LyricLine(TypedDict):
    text: str
    line_start_ms: Optional[int]


def _ts_to_ms(m: int, s: int, frac: Optional[str]) -> int:
    ms = 0
    if frac is not None:
        ms = int(frac.ljust(3, "0")[:3])
    return (m * 60 + s) * 1000 + ms


def parse_lrc(raw: str) -> dict:
    if not raw or not raw.strip():
        raise ValueError("empty lyrics")
    lines: list[LyricLine] = []
    has_timestamp = False
    for line in raw.splitlines():
        stamps = list(_TS.finditer(line))
        text = _TS.sub("", line).strip()
        if stamps:
            has_timestamp = True
            for st in stamps:
                lines.append(
                    {
                        "text": text,
                        "line_start_ms": _ts_to_ms(
                            int(st.group(1)), int(st.group(2)), st.group(3)
                        ),
                    }
                )
        else:
            stripped = line.strip()
            if not stripped or _META.match(stripped):
                continue
            lines.append({"text": stripped, "line_start_ms": None})
    if not lines:
        raise ValueError("no lyric lines parsed")
    if has_timestamp:
        lines.sort(key=lambda x: (x["line_start_ms"] is None, x["line_start_ms"] or 0))
    return {"lrc": raw, "lines": lines}
