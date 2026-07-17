/**
 * Composer disclosure: what this comment is about to start.
 *
 * Posting on an agent-assigned issue starts a billed agent turn. This chip is
 * the only signal that ⌘↩ costs money, and clicking it opts THIS comment out —
 * the note still lands in the thread and the agent reads it on its next wake.
 *
 * Three states:
 *  - armed ("Will start when sent · X"): clicking suppresses this comment.
 *  - suppressed ("Won't start this time"): clicking restores.
 *  - note ("Quiet note · won't wake X"): the draft opens with `/note` — a
 *    keyboard intent, so this state is NOT a toggle. It renders as a static
 *    span (no click affordance); deleting the prefix re-arms the chip.
 *
 * The verdict is the server's (POST /issues/{id}/comment-trigger-preview, which
 * carries the draft body); this component renders it and never re-derives it.
 * Same discipline as DispatchConfirmDialog: the endpoint returns an id, the
 * caller resolves the display name, so the predicate stays pure.
 *
 * Colour note: text rides the `ink` scale, which inverts under
 * [data-theme="light"]. Semantic colours (amber/indigo/…) do NOT invert
 * (index.css §light) — `text-amber-300` reads as invisible on a white surface,
 * which is exactly the btn-tint defect #1382/#1401 chased down twice. Amber is
 * therefore confined to the dot and an alpha tint, both of which composite
 * correctly over either surface.
 */

import type { CommentTriggerPreview } from '../../services/issueMessageService';

interface IssueCommentTriggerChipProps {
  /** The server's verdict for this issue. */
  preview: CommentTriggerPreview;
  /** Resolved by the caller from preview.agent_id; may lag a rename. */
  agentName?: string;
  suppressed: boolean;
  /** Nothing is about to be sent → nothing to disclose. */
  draftEmpty: boolean;
  onToggle: () => void;
}

export function IssueCommentTriggerChip({
  preview,
  agentName,
  suppressed,
  draftEmpty,
  onToggle,
}: IssueCommentTriggerChipProps) {
  // The predicate is authoritative about WHETHER; the name is cosmetic. A
  // pending/renamed agent must degrade to a generic label, never "undefined".
  const who = agentName?.trim() || 'the assigned agent';

  // ── Note state ─────────────────────────────────────────────────────────
  // The draft opens with /note: the server says nothing wakes. Rendered as a
  // static disclosure, not a toggle — the note is a keyboard intent, undone by
  // editing the draft, not by clicking. Requires agent_id: a note on an
  // unassigned issue has nobody to "not wake", and the chip's whole job is
  // billing disclosure — nothing billable was ever at stake there.
  if (preview.is_note && preview.agent_id && !draftEmpty) {
    return (
      <span
        data-testid="comment-trigger-chip"
        data-state="note"
        title="This comment starts with /note — it lands in the thread without waking the agent. Delete the /note prefix to send a normal comment."
        className={[
          'inline-flex items-center gap-1.5 self-start',
          'px-2 py-0.5 rounded-full text-[11px] leading-5',
          'ring-1 text-ink-400 ring-ink-700',
        ].join(' ')}
      >
        <span className="w-1.5 h-1.5 rounded-full shrink-0 bg-ink-500" />
        <span>
          Quiet note
          <span className="text-ink-500"> · won't wake {who}</span>
        </span>
      </span>
    );
  }

  // will_wake with no agent_id can't be opted out of (there's no id to name in
  // suppress_agent_ids), so the chip would be an un-dismissible dead end.
  // Shouldn't happen by construction — a wake always has an assignee — but
  // disclose-without-a-false-affordance beats a stuck toggle.
  if (!preview.will_wake || !preview.agent_id || draftEmpty) return null;

  return (
    <button
      type="button"
      data-testid="comment-trigger-chip"
      aria-pressed={suppressed}
      aria-label={
        suppressed
          ? `${who} will not start from this comment. Click to restore.`
          : `${who} will start when this comment is sent. Click to skip.`
      }
      onClick={onToggle}
      title={
        suppressed
          ? "This comment won't start a run. The agent still reads it next time it works this issue."
          : 'Click to skip the run for this comment.'
      }
      className={[
        'inline-flex items-center gap-1.5 self-start',
        'px-2 py-0.5 rounded-full text-[11px] leading-5',
        'ring-1 transition-colors',
        suppressed
          ? 'text-ink-400 ring-ink-700 hover:text-ink-300'
          : 'text-ink-200 bg-amber-500/10 ring-amber-500/30 hover:bg-amber-500/15',
      ].join(' ')}
    >
      <span
        className={[
          'w-1.5 h-1.5 rounded-full shrink-0',
          // Static, not animate-pulse: the pulse means "running now" elsewhere
          // in this module (IssueBoardView / the header chip). This is
          // prospective — reusing the pulse would claim a run already started.
          suppressed ? 'bg-ink-500' : 'bg-amber-400',
        ].join(' ')}
      />
      {suppressed ? (
        <span>
          Won't start this time
          <span className="text-ink-500"> · Click to restore</span>
        </span>
      ) : (
        <span>
          Will start when sent
          <span className="text-ink-400"> · {who}</span>
        </span>
      )}
    </button>
  );
}
