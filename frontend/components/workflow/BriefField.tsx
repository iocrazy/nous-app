/**
 * BriefField — the shared "pre-work notes" textarea for a workflow node's
 * `brief` (mig 395, M4 Autopilot task O1/O2/O3). Blur-save-only-if-changed,
 * reusing the exact `lastSaved` + defaulted-seed idiom `StageNodeForm` landed
 * (code review caught a phantom-save bug there: comparing against the RAW
 * possibly-`undefined` prop instead of the same defaulted baseline `local`
 * itself was seeded from fired a spurious PATCH on the very first blur with
 * no edit at all). `value` must already be defaulted to `''` by the caller
 * (both `CurrentNodeCard` and `WorkspaceStageBoard` read off
 * `node.brief ?? ''`, which `normalizeInstanceNode` also guarantees is never
 * `undefined` once the node has round-tripped through the API).
 *
 * Shared by `CurrentNodeCard` (compact Row entry) and `WorkspaceStageBoard`
 * (fuller box near Deliverables) so the two surfaces can never silently
 * diverge on the save contract.
 */
import React, { useRef, useState } from 'react';

export interface BriefFieldProps {
  value: string;
  disabled?: boolean;
  /** Shorter box for CurrentNodeCard's tighter Row layout; taller for the
   * Stage Board's dedicated section. Defaults to the taller (Stage Board) size. */
  compact?: boolean;
  placeholder?: string;
  onSave: (next: string) => void;
  testId?: string;
}

export const BriefField: React.FC<BriefFieldProps> = ({
  value,
  disabled,
  compact,
  placeholder,
  onSave,
  testId = 'workflow-brief-field',
}) => {
  const [local, setLocal] = useState(value);
  // The last value actually pushed to (or seeded from) the server — commit()
  // only fires onSave when the field genuinely changed, so a blur with no
  // edit (e.g. tabbing through) never manufactures a spurious PATCH.
  const lastSaved = useRef(value);
  // The `value` this field is currently seeded from.
  //
  // ⚠️ Reseeding MUST happen during render (React's documented "adjusting
  // state when a prop changes" recipe), never in a `useEffect`. A passive
  // effect is a DEFERRED write: React commits the DOM first and flushes
  // passive effects in a later scheduler task, so a keystroke that lands in
  // between gets silently overwritten by the seed — `local` snaps back to
  // `value`, `lastSaved` follows it, and the next blur sees "nothing changed"
  // and issues no PATCH at all. The edit is lost with zero user-visible
  // feedback (same family as the repo's "silent no-op is unacceptable" rule).
  //
  // That is not hypothetical: the effect version made
  // WorkspaceStageBoard/WorkspaceNodeSettings' brief tests flaky in CI —
  // under CPU contention the mount effect flushed *after* fireEvent.change,
  // and `updateProjectNode` was then never called (0 calls, not "called
  // late"), which is why no `waitFor` timeout could ever have fixed it.
  const [seededFrom, setSeededFrom] = useState(value);
  if (seededFrom !== value) {
    setSeededFrom(value);
    setLocal(value);
    lastSaved.current = value;
  }

  const commit = () => {
    if (local === lastSaved.current) return;
    lastSaved.current = local;
    onSave(local);
  };

  return (
    <textarea
      data-testid={testId}
      value={local}
      disabled={disabled}
      placeholder={placeholder}
      rows={compact ? 2 : 3}
      onChange={(e) => setLocal(e.target.value)}
      onBlur={commit}
      className="h-auto w-full min-w-0 resize-y rounded-md border border-line bg-transparent px-2 py-1.5 text-[13px] text-ink-100 placeholder-ink-600 focus:border-line-strong focus:outline-none disabled:cursor-not-allowed disabled:opacity-60"
    />
  );
};

export default BriefField;
