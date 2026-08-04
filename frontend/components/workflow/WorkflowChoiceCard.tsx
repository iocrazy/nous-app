/**
 * WorkflowChoiceCard — one selectable card in a workflow-template picker.
 *
 * Shared between CreateProjectModal's "pick a workflow at creation" block and
 * AttachWorkflowModal's "attach a workflow to an existing project" block
 * (M1.x opt-in migration path) so the two pickers render identically instead
 * of drifting apart as separate copies.
 */

import React from 'react';

interface WorkflowChoiceCardProps {
  title: string;
  hint: string;
  selected: boolean;
  onClick: () => void;
  testId: string;
}

export const WorkflowChoiceCard: React.FC<WorkflowChoiceCardProps> = ({
  title,
  hint,
  selected,
  onClick,
  testId,
}) => {
  return (
    <button
      type="button"
      onClick={onClick}
      data-testid={testId}
      className={`flex flex-col items-start gap-0.5 rounded-lg border px-3 py-2.5 text-left transition ${
        selected
          ? 'border-[var(--accent-border)] bg-[var(--accent-soft)]'
          : 'border-ink-700 hover:border-ink-600'
      }`}
    >
      <span className={`text-[13px] font-medium ${selected ? 'text-[var(--accent-text)]' : 'text-ink-100'}`}>
        {title}
      </span>
      <span className="text-[11px] text-ink-500">{hint}</span>
    </button>
  );
};

export default WorkflowChoiceCard;
