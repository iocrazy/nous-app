"""Model-visible text for an MCP transport failure.

When an outbound MCP call raises (network down, 4xx/5xx, a malformed
JSON-RPC reply), the runner turns the exception into a tool-result dict so
the model gets feedback instead of the run crashing. The exception's
``str()`` is **external text**: an MCP server we do not control can put
anything in an HTTP error body or a JSON-RPC ``error.message``, and that
text lands verbatim in the model's context as a tool message.

A tool message is not one of our frames, so there is no frame to close —
but the text is still untrusted, unbounded, and able to forge harness
markup (``<system-reminder>``) or extra lines. So it gets the same treatment
as a one-line prose value inside a frame we own:

* ``escape_frame_prose`` — flatten ``\\r\\n\\t`` and entity-escape ``&<>``,
  after defusing every owned frame's close marker.
* capped at ``MCP_ERROR_TEXT_MAX`` characters, raw (before escaping, so an
  entity is never cut in half). A diagnosis needs the first line of an error
  body, not all of it.

Both call sites in ``agent_runner`` (``run_turn`` and the streaming path)
go through :func:`mcp_transport_error_text`; nothing else should build this
string.
"""

from __future__ import annotations

from typing import Final

from app.boundary.frame_markers import escape_frame_prose

MCP_ERROR_TEXT_MAX: Final = 500

_PREFIX: Final = "MCP transport failure: "


def mcp_transport_error_text(exc: BaseException) -> str:
    """``"MCP transport failure: <escaped, capped exception text>"``."""
    raw = str(exc)
    if len(raw) > MCP_ERROR_TEXT_MAX:
        raw = raw[:MCP_ERROR_TEXT_MAX] + "…"
    return _PREFIX + escape_frame_prose(raw)


__all__ = ["MCP_ERROR_TEXT_MAX", "mcp_transport_error_text"]
