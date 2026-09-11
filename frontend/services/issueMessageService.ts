/**
 * Issue Messages service — paperclip-style chat thread per issue (A8.3).
 *
 * Mirrors backend/app/api/issue_messages_router.py.
 * Pair with a Realtime subscription on the `issue_messages` table for
 * live updates (filter: issue_id=eq.<id>).
 */

import { getApiUrl } from '../utils/apiConfig';
import { getAuthHeaders } from './parserService';

// ── Types ──────────────────────────────────────────────────────────

export type IssueMessageKind = 'comment' | 'agent_run' | 'system_status';

/**
 * Mirrors the agent_runs.liveness_state CHECK constraint (mig 207, extended
 * by mig 406). `running`/`silent`/`stuck`/`dead` are the degradation ladder
 * the backend liveness scanner walks; `finished` and `cancelled` are terminal
 * values written by RunRecorder when the run ends normally / is cancelled.
 */
export type AgentLivenessState =
  | 'running'
  | 'silent'
  | 'stuck'
  | 'dead'
  | 'cancelled'
  | 'finished';

/**
 * Where a thread row came from, when it did not come from a person typing.
 * A wake-up writes an ORDINARY `comment` row owned by the rule's owner —
 * there is no author_kind column — so this marker inside the existing `meta`
 * jsonb is the only thing that tells the two apart.
 */
export interface IssueMessageSource {
  kind: string;
  /** `user_schedules.id` — a uuid STRING (mig 204). */
  schedule_id?: string;
  created_by?: string;
}

export interface IssueMessage {
  id: string;
  issue_id: number;
  kind: IssueMessageKind;
  author_user_id: string | null;
  author_agent_id: string | null;
  body: string | null;
  meta: Record<string, unknown> & { source?: IssueMessageSource };
  duration_seconds: number | null;
  agent_run_id: string | null;
  /** Optional: surfaced when the chat row was emitted by the agent_runs
   * bridge trigger (mig 206) AND the run carried a liveness state in
   * meta. Null for plain comments. */
  liveness_state?: AgentLivenessState | null;
  from_status: string | null;
  to_status: string | null;
  created_at: string;
}

/** True when a schedule firing wrote this row. */
export const startedByWakeup = (msg: IssueMessage | undefined | null): boolean =>
  msg?.meta?.source?.kind === 'schedule';

export interface IssueMessageList {
  messages: IssueMessage[];
  total: number;
}

export type IssueMessageAttachment =
  | { kind: 'image' | 'video' | 'pdf'; url: string; mime?: string | null }
  | {
      kind: 'resource_ref';
      resource_id: string;
      name: string;
      mime: string;
      scope: { type: 'personal' | 'team'; id: string };
    }
  /**
   * A library ASSET (v2 Task 3). Named as its own member rather than left to
   * the `url`-carrying branch above: an `asset_ref` is identified by
   * `asset_id`, and a mapper that treated it as a binary attachment would send
   * `{kind: 'asset_ref', url: ''}` — accepted by `AttachmentRequest` (every
   * field is Optional), resolved to nothing, and reported back as
   * `asset_not_accessible`. The union is what makes that omission a compile
   * error instead of a mention that quietly does nothing.
   *
   * `loadout_id` null means "the asset's default loadout" — the backend's
   * reading, not a missing value.
   */
  | {
      kind: 'asset_ref';
      asset_id: string;
      loadout_id: string | null;
      name: string;
      mime: string;
      url: string;
    }
  /**
   * A CITATION of one registered output version (3a Task 6).
   *
   * Its own member for the same reason `asset_ref` is one, with a sharper
   * edge: this kind carries no `url` AND no `name`. A mapper that reached for
   * the familiar snapshot field would post `{kind:'output_ref', name:'…'}`
   * with no coordinates at all — accepted by `AttachmentRequest` (every field
   * Optional) and refused as `output_ref_unresolvable`, which reads to the
   * user as "the thing you pointed at is gone" rather than "the client forgot
   * to send which thing".
   *
   * `title` is the backend's field name and its value is overwritten server
   * side with the registry row's title; it travels so an optimistic render has
   * words before the round trip answers.
   */
  | {
      kind: 'output_ref';
      ref_kind: string;
      ref_id: string;
      version: number;
      title: string | null;
    };

export interface IssueMessagePostPayload {
  body: string;
  /** @deprecated Never read by the backend — dispatch routes on the issue's
   *  assignee_agent_id. Sending it has no effect. */
  agent_id?: string | null;
  attachments?: IssueMessageAttachment[];
  /** Agents this one comment must not wake. Subtractive: the server computes
   *  who would wake and can only drop from that set, never add. Omit the key
   *  entirely when nothing is suppressed. */
  suppress_agent_ids?: string[];
  /** Phase 2a: this comment answers the parked typed question with that id.
   *  The body must equal one of its option labels (or be free text when the
   *  question allows it) — the server validates (409 no_open_question / 400
   *  answer_shape) and records `question_answered` after delivery. */
  answer_to?: string;
}

/** What posting a comment would start — the server's own verdict.
 *  Distinct from DispatchPreview: the comment path has no terminal-status or
 *  already-running guard, so a comment wakes where a dispatch would be blocked. */
export interface CommentTriggerPreview {
  will_wake: boolean;
  agent_id: string | null;
  /** The draft body was a `/note` command → a silent note: it lands in the
   *  thread but wakes nothing. The reason `will_wake` is false (distinct from
   *  client suppression). Optional so a pre-note cached verdict still validates;
   *  the backend always sends it (defaults to false). */
  is_note?: boolean;
}

export interface IssueMessagePostResponse {
  comment: IssueMessage;
  agent_run: IssueMessage | null;
  /** True only on the Wake path (a reply turn actually started). `agent_run`
   *  above is always null regardless of path, so this is the field to check
   *  before assuming a reply drove the agent forward — the Note path
   *  (suppressed / `/note` body) and the Legacy path (no assignee agent)
   *  both start nothing and leave this false. */
  agent_dispatched: boolean;
  /** harness P4 §1-③: a root run was mid-turn, so the comment went to its
   *  inbox (claimed before the next step) instead of starting a new turn. */
  diverted_to_inbox?: boolean;
  inbox_id?: string | null;
}

/** Thrown by callers (e.g. TaskCenter's needs-input answer handler) when a
 *  reply POSTed successfully but `agent_dispatched` came back false — the
 *  message was saved, but no turn was started, so the caller has to signal
 *  that distinctly from a network/backend failure. */
export class AgentNotDispatchedError extends Error {
  constructor() {
    super('reply posted but no agent turn was dispatched');
    this.name = 'AgentNotDispatchedError';
  }
}

// ── REST helpers ───────────────────────────────────────────────────

const _base = `${getApiUrl()}/api/v1/issues`;

/**
 * A typed 4xx from the answer channel (phase 2a): the backend's
 * `{detail: {code, message}}` — `no_open_question` / `answer_shape` (409/400
 * from the endpoint), or a kind's own rejection (`budget_still_exhausted`,
 * `budget_unreadable`, `no_issue_target`). QuestionCard maps `code` to copy.
 */
export class IssueAnswerRejectedError extends Error {
  readonly status: number;
  readonly code: string;
  constructor(status: number, code: string, message: string) {
    super(message || code);
    this.name = 'IssueAnswerRejectedError';
    this.status = status;
    this.code = code;
  }
}

async function _json<T>(res: Response): Promise<T> {
  if (!res.ok) {
    const text = await res.text().catch(() => '');
    try {
      const parsed = JSON.parse(text) as {
        detail?: { code?: unknown; message?: unknown } | string;
        details?: { code?: unknown; message?: unknown } | string;
      };
      // `details` FIRST, and it is the one production sends. Every
      // HTTPException is wrapped by `app/core/exceptions.py` into
      // `{success, error, code:"http_<status>", request_id, details}`, where a
      // dict `detail` is moved verbatim to `details` and the original key is
      // gone. Reading only `detail` — as this did until 3a Task 6 — keeps every
      // FastAPI-shaped unit fixture green while turning every real refusal into
      // "400 Bad Request: {…}" (CLAUDE.md 2026-09-09). `detail` stays as the
      // fallback for the direct-ASGI paths that never pass a handler.
      //
      // The envelope's own `code` is deliberately NOT read: it is `http_400` on
      // every refusal, so treating it as typed would make every failure look
      // classified while naming nothing.
      const carrier = parsed?.details ?? parsed?.detail;
      const code = typeof carrier === 'object' && carrier !== null ? carrier.code : undefined;
      if (typeof code === 'string' && code) {
        const rawMessage = (carrier as { message?: unknown }).message;
        const message = typeof rawMessage === 'string' ? rawMessage : '';
        throw new IssueAnswerRejectedError(res.status, code, message);
      }
    } catch (err) {
      if (err instanceof IssueAnswerRejectedError) throw err;
      // not the typed shape — fall through to the generic error below
    }
    throw new Error(`${res.status} ${res.statusText}: ${text}`);
  }
  if (res.status === 204) return undefined as unknown as T;
  return res.json() as Promise<T>;
}

export async function listIssueMessages(issueId: number): Promise<IssueMessageList> {
  const res = await fetch(`${_base}/${issueId}/messages`, {
    headers: await getAuthHeaders(),
  });
  return _json<IssueMessageList>(res);
}

/** Ask the server what a comment on this issue would start. Read-only (no
 *  side effect) but a POST, because it carries the draft body — a `/note`
 *  prefix flips the verdict to a silent note, so the predicate must see the
 *  same body the send path will. Pass no body (or null) for the armed,
 *  nothing-typed-yet verdict. */
export async function getCommentTriggerPreview(
  issueId: number,
  body?: string | null,
): Promise<CommentTriggerPreview> {
  const res = await fetch(`${_base}/${issueId}/comment-trigger-preview`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json', ...(await getAuthHeaders()) },
    body: JSON.stringify({ body: body ?? null }),
  });
  return _json<CommentTriggerPreview>(res);
}

export async function postIssueMessage(
  issueId: number,
  payload: IssueMessagePostPayload,
): Promise<IssueMessagePostResponse> {
  const res = await fetch(`${_base}/${issueId}/messages`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json', ...(await getAuthHeaders()) },
    body: JSON.stringify(payload),
  });
  return _json<IssueMessagePostResponse>(res);
}

/**
 * Dev/demo helper: flip an issue-scoped agent_run from running →
 * completed with a sample summary. The DB triggers fan out the chat
 * row update via Realtime.
 */
export async function simulateAgentRunComplete(
  issueId: number,
  runId: string,
  outputSummary?: string,
): Promise<{ run_id: string; status: string; summary_preview: string }> {
  const url = `${_base}/${issueId}/agent-runs/${runId}/simulate-complete`;
  const params = outputSummary ? `?output_summary=${encodeURIComponent(outputSummary)}` : '';
  const res = await fetch(`${url}${params}`, {
    method: 'POST',
    headers: await getAuthHeaders(),
  });
  return _json(res);
}
