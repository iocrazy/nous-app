/**
 * OwnerCandidateList — the People/Agents grouped candidate list shared by
 * `OwnerPicker` (its own dropdown) and `EpisodeNodeCard`'s Task 9 editable
 * owner menu (ev评审修复轮1, IA redesign task-9 review).
 *
 * Before this extraction, `EpisodeNodeCard.tsx` had a hand-copied ~50-line
 * near-duplicate of `OwnerPicker`'s dropdown block (container/group-header/
 * list-item/empty-state markup, all byte-for-byte identical classes) — the
 * decision to NOT mount the full `<OwnerPicker>` widget in the node card
 * (it owns its own trigger+dropdown chrome with no way to inject the node
 * card's pill/quiet-button styling or a `data-testid` on the real clickable
 * element) is still correct, but the shared LIST portion belongs in one
 * place (评审修复轮1). Selection uses the same `WorkflowMemberRef` wire
 * shape (`{user_id}` xor `{agent_id}`) both call sites already speak.
 *
 * `Avatar` also lives here (moved out of `OwnerPicker.tsx`, which re-exports
 * it) since both the list rows and `OwnerPicker`'s own trigger/member-chips
 * need it — one definition, not two.
 */

import type { FC } from 'react';
import { Bot, Check } from 'lucide-react';
import type { WorkflowMemberRef } from '../../types';

export interface PersonOption {
  /** user_id (UUID). */
  id: string;
  name: string;
}

export interface AgentOption {
  /** ai_agents.id (UUID). */
  id: string;
  name: string;
  slug?: string;
  color?: string;
}

export const Avatar: FC<{ kind: 'user' | 'agent'; name: string; color?: string }> = ({
  kind,
  name,
  color,
}) => {
  if (kind === 'agent') {
    return (
      <span
        className="inline-flex h-5 w-5 items-center justify-center rounded-full text-white"
        style={{ background: color || 'var(--accent, #6366f1)' }}
      >
        <Bot size={12} />
      </span>
    );
  }
  return (
    <span className="inline-flex h-5 w-5 items-center justify-center rounded-full bg-ink-700 text-[10px] font-semibold text-ink-200">
      {(name || '?').charAt(0).toUpperCase()}
    </span>
  );
};

export interface OwnerCandidateListProps {
  people: PersonOption[];
  agents: AgentOption[];
  /** Whether a given candidate ref is the currently-selected owner/member —
   * caller decides the match semantics (single-owner XOR vs multi-member
   * membership), this component only renders the check mark. */
  isSelected: (ref: WorkflowMemberRef) => boolean;
  onSelect: (ref: WorkflowMemberRef) => void;
  peopleLabel: string;
  agentsLabel: string;
  emptyLabel: string;
}

/** People/Agents grouped list + empty state — no outer positioning wrapper
 * (both call sites own their own `absolute`/`shadow`/`border` container,
 * which differs slightly between them, so that stays with each caller). */
export const OwnerCandidateList: FC<OwnerCandidateListProps> = ({
  people,
  agents,
  isSelected,
  onSelect,
  peopleLabel,
  agentsLabel,
  emptyLabel,
}) => (
  <>
    {people.length > 0 && (
      <>
        <div className="px-2.5 py-1 text-[10px] font-medium uppercase tracking-wider text-ink-600">
          {peopleLabel}
        </div>
        {people.map((p) => {
          const ref: WorkflowMemberRef = { user_id: p.id };
          const sel = isSelected(ref);
          return (
            <button
              key={p.id}
              type="button"
              onClick={() => onSelect(ref)}
              className="flex w-full items-center gap-2 px-2.5 py-1.5 text-[13px] text-ink-200 hover:bg-ink-800"
            >
              <Avatar kind="user" name={p.name} />
              <span className="flex-1 truncate text-left">{p.name}</span>
              {sel && <Check size={14} className="text-emerald-500" />}
            </button>
          );
        })}
      </>
    )}
    {agents.length > 0 && (
      <>
        <div className="px-2.5 py-1 text-[10px] font-medium uppercase tracking-wider text-ink-600">
          {agentsLabel}
        </div>
        {agents.map((a) => {
          const ref: WorkflowMemberRef = { agent_id: a.id };
          const sel = isSelected(ref);
          return (
            <button
              key={a.id}
              type="button"
              onClick={() => onSelect(ref)}
              className="flex w-full items-center gap-2 px-2.5 py-1.5 text-[13px] text-ink-200 hover:bg-ink-800"
            >
              <Avatar kind="agent" name={a.name} color={a.color} />
              <span className="flex-1 truncate text-left">{a.name}</span>
              {sel && <Check size={14} className="text-emerald-500" />}
            </button>
          );
        })}
      </>
    )}
    {people.length === 0 && agents.length === 0 && (
      <div className="px-2.5 py-2 text-[13px] text-ink-500">{emptyLabel}</div>
    )}
  </>
);

export default OwnerCandidateList;
