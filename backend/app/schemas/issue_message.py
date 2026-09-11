"""IssueMessage API schemas (A8 paperclip-style chat thread).

Mirrors public.issue_messages from migration 205. Three message kinds in
one timeline; per-kind required fields enforced at the DB CHECK level.
"""

from __future__ import annotations

from datetime import datetime
from enum import Enum
from typing import Any, List, Optional
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from app.schemas.ai_library_chat import AttachmentRequest


class IssueMessageKind(str, Enum):
    COMMENT = "comment"
    AGENT_RUN = "agent_run"
    SYSTEM_STATUS = "system_status"


class IssueMessageAttachment(BaseModel):
    """One attachment as STORED on a message, handed back on every read.

    Field-for-field the whitelist ``ConversationsAiStore._DISPLAY_ATTACHMENT_KEYS``
    persists under ``body['attachments']`` — nothing here is invented, and the
    write-side names come from ``AttachmentRequest``. What that whitelist
    deliberately drops stays dropped: no ``data_url`` (bytes never enter the
    store), no ``url``, no ``scope``.

    Every stored kind shares this one shape: ``image`` / ``video`` / ``pdf``
    (file bubbles), ``resource_ref``, ``asset_ref``, and — 三期 3a Task 4 —
    ``output_ref``, whose ``ref_kind`` / ``ref_id`` / ``version`` / ``title``
    are the citation chip's whole input. ``title`` is the snapshot taken at
    post time, which is why the thread needs no second query to render it.

    EVERY field is optional, ``kind`` included. This is a READ model over rows
    written by several versions of several writers: one legacy or malformed
    row must degrade to nulls, never make an entire issue's history 500. For
    the same reason ``coerce_numbers_to_str`` is on — a Snowflake that reached
    JSONB as a number (``ref_id`` / ``resource_id`` / ``asset_id``) comes back
    as the string the rest of the API speaks.
    """

    model_config = ConfigDict(coerce_numbers_to_str=True)

    kind: Optional[str] = None
    resource_id: Optional[str] = None
    asset_id: Optional[str] = None
    loadout_id: Optional[str] = None
    mime: Optional[str] = None
    alt_text: Optional[str] = None
    name: Optional[str] = None
    # kind='output_ref' — the cited version's coordinates + its title snapshot.
    ref_kind: Optional[str] = None
    ref_id: Optional[str] = None
    version: Optional[int] = None
    title: Optional[str] = None


class IssueMessage(BaseModel):
    # agent_run_id is a BIGINT Snowflake (mig 232) modelled as str; DB rows
    # deliver it as an int, so opt into int→str coercion.
    model_config = ConfigDict(coerce_numbers_to_str=True)

    # `id` was a real UUID PK under the retired Supabase-backed ai_messages
    # store; under ConversationsAiStore (the sole store since Conversations
    # Phase 3 Task 6) it's public.messages.id, a BIGINT snowflake. Widened
    # to str (mirrors MessageOut.id in schemas/ai_library_chat.py) so both
    # shapes validate — the legacy path (issue_messages table, still a real
    # UUID PK) and optimistic rows (uuid.uuid4()) keep working unchanged.
    id: str
    issue_id: int
    kind: IssueMessageKind
    author_user_id: Optional[UUID] = None
    author_agent_id: Optional[UUID] = None
    body: Optional[str] = None
    meta: dict[str, Any] = Field(default_factory=dict)
    duration_seconds: Optional[int] = None
    # agent_runs.id is BIGINT Snowflake (mig 232) → numeric string, not UUID.
    agent_run_id: Optional[str] = None
    from_status: Optional[str] = None
    to_status: Optional[str] = None
    created_at: datetime
    # Display attachments stored with the message (user-role rows only).
    # ``None`` — never ``[]`` — when the row has none: the store itself
    # returns ``body.get("attachments") or None``, and every message written
    # before 3a has no such key, so "absent" is the honest answer rather than
    # "had some, none survived". Pinned by
    # tests/test_issue_message_mapper.py::test_a_row_without_attachments_reads_back_as_null.
    attachments: Optional[List[IssueMessageAttachment]] = None

    @field_validator("id", mode="before")
    @classmethod
    def _coerce_id(cls, v: Any) -> str:
        # coerce_numbers_to_str handles bigint ints; this covers the other
        # shape callers still pass — a real uuid.UUID object (legacy rows /
        # the optimistic-comment placeholder in issue_messages_router.py).
        return str(v)


class IssueMessageList(BaseModel):
    messages: list[IssueMessage]
    total: int


class CommentTriggerPreviewRequest(BaseModel):
    """Draft the composer is about to send, so the preview reads the SAME body
    the POST would. `body` is optional: a bodyless preview (armed composer,
    nothing typed yet) still returns the assignee-based verdict. A `/note`
    prefix flips the verdict to a silent note — see comment_trigger.py."""

    body: Optional[str] = None


class CommentTriggerPreview(BaseModel):
    """What posting a comment on this issue would do — without posting it.

    Read-only. Distinct from DispatchPreview: that one mirrors dispatch_issue's
    guards (terminal_status / already_running / dbos_disabled), none of which
    the comment path honours. Reusing it for the composer chip would state the
    opposite of what a comment actually does on a done or mid-run issue.

    Carries the draft body (POST): the predicate now reads it so a `/note`
    prefix is disclosed as a quiet note. `is_note` records that the body was a
    `/note` command — the reason `will_wake` is False, distinct from client
    suppression — so the chip can render the quiet-note state and still name the
    agent it won't wake.
    """

    will_wake: bool
    agent_id: Optional[str] = None
    is_note: bool = False


class IssueMessagePost(BaseModel):
    """User-facing reply payload.

    Posting a comment on an issue with an assigned agent starts a billed agent
    turn — that is driven by the ISSUE's assignee_agent_id, not by anything in
    this payload. `suppress_agent_ids` is the only way to opt a single comment
    out of it, and it is subtractive: naming an id the server did not itself
    compute is a no-op.
    """

    body: str = Field(min_length=1, max_length=50000)
    # Phase 2a: this comment answers the typed question ``question_id``. The
    # body must equal one of its labels (or be free text when allowed);
    # otherwise 400 answer_shape / 409 no_open_question. Answers do not go
    # through the inbox — the message endpoint validates, records
    # ``question_answered`` on the asking run and wakes the workflow.
    answer_to: Optional[str] = Field(default=None, max_length=120)
    # DEPRECATED — never read. The handler routes on issue_row.assignee_agent_id;
    # this field has no effect on dispatch. Kept only so existing clients that
    # send it don't 422. Removing it is an API contract change (separate PR).
    agent_id: Optional[UUID] = None
    # Optional attachments forwarded to the agent turn (sub-plan 3, Task 5).
    # Serialised as model_dump() dicts before entering the DBOS workflow so
    # they stay JSON-serialisable across the workflow boundary.
    attachments: Optional[List[AttachmentRequest]] = None
    # Agents this one comment must NOT wake. Subtractive only: the server
    # computes who would wake and can merely drop from that set — a client can
    # never add a trigger. A stale id (assignee changed since the preview) is a
    # deliberate no-op rather than a silent suppression of an unseen agent.
    suppress_agent_ids: Optional[List[str]] = None

    @model_validator(mode="after")
    def _strip_body(self) -> "IssueMessagePost":
        self.body = self.body.strip()
        if not self.body:
            raise ValueError("body cannot be empty after strip")
        return self


class IssueMessagePostResponse(BaseModel):
    """The user's comment row (optimistic on the session paths; canonical on
    the legacy path). ``agent_run`` is always None today — the dispatched turn
    surfaces through GET /messages, not here — kept for wire compatibility.

    ``agent_dispatched`` (final review fix, silent no-op finding): True only
    on the Wake path where a reply turn was actually started. False on the
    Note path (suppressed / `/note` body) and the Legacy path (no assignee
    agent) — those never start a workflow, so a caller relying on this field
    (rather than the always-null ``agent_run``) can tell the difference and
    surface it instead of leaving the UI waiting on a status flip that will
    never come.
    """

    comment: IssueMessage
    agent_run: Optional[IssueMessage] = None
    agent_dispatched: bool = False
    # harness p4 §1-③: a root run was mid-turn on this issue, so the comment
    # went to its inbox (claimed at the next step boundary) instead of
    # starting a new turn. The comment row is kept either way.
    diverted_to_inbox: bool = False
    inbox_id: Optional[str] = None
