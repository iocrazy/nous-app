"""Soda timed-lyrics parser.

Port of ``SodaTimedLyricsParser`` from musicdl
(``musicdl/modules/utils/sodautils.py``), rewritten from the original dense
one-liners into clean, readable module-level functions.

Soda timed-lyrics format (one logical line per text line)::

    [start_ms,dur]<offset,dur,flag>word<offset,dur,flag>word...

- Line header regex:  ``^\\[(\\d+),(\\d+)\\]``  -> (line_start_ms, line_duration_ms)
- Token regex:        ``<(\\d+),(\\d+),(\\d+)>`` -> (offset_ms, duration_ms, flag)

The text of a line is the concatenation of the token texts (the runs of
characters that follow each ``<...>`` marker). An LRC line-level timestamp
``[MM:SS.CS]`` is derived from ``line_start_ms`` (CS = centiseconds).

No external dependencies — stdlib ``re`` only.
"""

from __future__ import annotations

import re
from typing import Any

# Matches the per-line header: [line_start_ms,line_duration_ms]
LINE_PATTERN_RE = re.compile(r"^\[(\d+),(\d+)\]")

# Matches a per-token marker: <offset_ms,duration_ms,flag>
TOKEN_PATTERN_RE = re.compile(r"<(\d+),(\d+),(\d+)>")

# Values that should be treated as "no lyrics".
_NULL_VALUES = {"NULL", "null", "None", "none"}


def parse_timed_lyrics(text: str | None) -> list[dict[str, Any]]:
    """Parse Soda timed-lyrics text into a list of line dicts.

    Each line dict has the shape::

        {
            "line_start_ms": int,
            "line_duration_ms": int,
            "line_end_ms": int,
            "text": str,                # concatenation of token texts
            "tokens": list[dict],       # see below
            "raw": str,                 # the line body after the header
        }

    Each token dict has the shape::

        {
            "text": str,
            "offset_ms": int,
            "duration_ms": int,
            "flag": int,
            "start_ms": int,            # line_start_ms + offset_ms
            "end_ms": int,              # line_start_ms + offset_ms + duration_ms
        }

    Returns ``[]`` for empty / NULL-like / ``None`` input. Lines that do not
    match the line header regex are skipped.
    """
    if not text or text in _NULL_VALUES:
        return []

    # Unescape the JSON-style angle-bracket escapes the source format uses.
    normalized = text.replace(r"\u003C", "<").replace(r"\u003E", ">")

    lines_out: list[dict[str, Any]] = []
    for raw_line in normalized.splitlines():
        stripped = raw_line.rstrip("\n").strip()
        if not stripped:
            continue

        header_match = LINE_PATTERN_RE.match(stripped)
        if not header_match:
            continue

        line_start_ms = int(header_match.group(1))
        line_duration_ms = int(header_match.group(2))
        line_end_ms = line_start_ms + line_duration_ms

        # The line body is everything after the header in the (rstripped) line.
        # Mirrors the reference, which slices the un-stripped line at the
        # header's end offset.
        body = raw_line.rstrip("\n")[header_match.end() :]

        tokens, pieces = _parse_tokens(body, line_start_ms)

        lines_out.append(
            {
                "line_start_ms": line_start_ms,
                "line_duration_ms": line_duration_ms,
                "line_end_ms": line_end_ms,
                "text": "".join(pieces),
                "tokens": tokens,
                "raw": body,
            }
        )

    return lines_out


def _parse_tokens(
    body: str, line_start_ms: int
) -> tuple[list[dict[str, Any]], list[str]]:
    """Parse the token markers within a single line body.

    Returns a tuple of ``(tokens, pieces)`` where ``pieces`` is the list of
    token texts (used to assemble the line text). Empty token texts are
    skipped, matching the reference.
    """
    markers = list(TOKEN_PATTERN_RE.finditer(body))

    tokens: list[dict[str, Any]] = []
    pieces: list[str] = []

    for index, marker in enumerate(markers):
        offset_ms = int(marker.group(1))
        duration_ms = int(marker.group(2))
        flag = int(marker.group(3))

        segment_start = marker.end()
        if index + 1 < len(markers):
            segment_end = markers[index + 1].start()
        else:
            segment_end = len(body)

        token_text = body[segment_start:segment_end].replace("\r", "")
        if token_text == "":
            continue

        start_ms = line_start_ms + offset_ms
        tokens.append(
            {
                "text": token_text,
                "offset_ms": offset_ms,
                "duration_ms": duration_ms,
                "flag": flag,
                "start_ms": start_ms,
                "end_ms": start_ms + duration_ms,
            }
        )
        pieces.append(token_text)

    return tokens, pieces


def _format_timestamp(ms: int, use_centiseconds: bool = True) -> str:
    """Format milliseconds as an LRC timestamp ``MM:SS`` or ``MM:SS.CS``."""
    minutes = ms // 60000
    seconds = (ms % 60000) // 1000
    if not use_centiseconds:
        return f"{minutes:02d}:{seconds:02d}"
    centiseconds = (ms % 1000) // 10
    return f"{minutes:02d}:{seconds:02d}.{centiseconds:02d}"


def to_lrc(parsed: list[dict[str, Any]], use_centiseconds: bool = True) -> str:
    """Render parsed lines as a line-level LRC string.

    Each line becomes ``[MM:SS.CS]text`` (timestamp derived from
    ``line_start_ms``). Lines are joined by newlines. An empty list yields
    ``""``.
    """
    if not parsed:
        return ""

    return "\n".join(
        f"[{_format_timestamp(line['line_start_ms'], use_centiseconds)}]{line['text']}"
        for line in parsed
    )


def to_plain_text(parsed: list[dict[str, Any]]) -> str:
    """Render parsed lines as newline-joined plain text.

    An empty list yields ``""``.
    """
    if not parsed:
        return ""

    return "\n".join(line["text"] for line in parsed)
