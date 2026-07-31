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
import React, { useEffect, useRef, useState } from 'react';

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

  useEffect(() => {
    setLocal(value);
    lastSaved.current = value;
  }, [value]);

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
