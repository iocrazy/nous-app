/**
 * People + agents combined picker (Project Workflow M1).
 *
 * One control that mixes team members and AI agents in a single list — an
 * owner is a single user XOR agent (spec §4/§7), members are a multi-select of
 * either. The parent supplies the candidate pools (`people` / `agents`) so the
 * same component serves both the team-level template editor and the
 * project-level node card without knowing where the lists come from.
 *
 * Selection is emitted as the wire shape ({ user_id } | { agent_id }) so
 * callers pass it straight to the API.
 */

import React, { useEffect, useRef, useState } from 'react';
import { ChevronDown, User, X } from 'lucide-react';
import type { WorkflowMemberRef } from '../../types';
import { Avatar, OwnerCandidateList } from './OwnerCandidateList';
import type { AgentOption, PersonOption } from './OwnerCandidateList';

// Re-exported so existing consumers (`WorkflowSection.tsx`,
// `CurrentNodeCard.tsx`, `EpisodeNodeCard.tsx`/its test) that import these
// types from `./OwnerPicker` keep compiling unchanged — the canonical
// definitions moved to `OwnerCandidateList.tsx` (评审修复轮1, task 9), which
// both this widget and `EpisodeNodeCard`'s own candidate menu now share.
export type { AgentOption, PersonOption };

type OwnerProps = {
  mode: 'owner';
  people: PersonOption[];
  agents: AgentOption[];
  ownerUserId?: string | null;
  ownerAgentId?: string | null;
  onOwnerChange: (ref: WorkflowMemberRef | null) => void;
  disabled?: boolean;
  placeholder?: string;
};

type MembersProps = {
  mode: 'members';
  people: PersonOption[];
  agents: AgentOption[];
  members: WorkflowMemberRef[];
  onMembersChange: (members: WorkflowMemberRef[]) => void;
  disabled?: boolean;
  placeholder?: string;
};

type OwnerPickerProps = OwnerProps | MembersProps;

export const OwnerPicker: React.FC<OwnerPickerProps> = (props) => {
  const { people, agents, disabled, placeholder } = props;
  const [open, setOpen] = useState(false);
  const rootRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    if (!open) return;
    const onDown = (e: MouseEvent) => {
      if (rootRef.current && !rootRef.current.contains(e.target as Node)) {
        setOpen(false);
      }
    };
    document.addEventListener('mousedown', onDown);
    return () => document.removeEventListener('mousedown', onDown);
  }, [open]);

  const agentName = (id: string) => agents.find((a) => a.id === id)?.name ?? 'Agent';
  const personName = (id: string) => people.find((p) => p.id === id)?.name ?? 'Member';

  const isSelected = (ref: WorkflowMemberRef): boolean => {
    if (props.mode === 'owner') {
      return (
        (!!ref.user_id && ref.user_id === props.ownerUserId) ||
        (!!ref.agent_id && ref.agent_id === props.ownerAgentId)
      );
    }
    return props.members.some(
      (m) =>
        (!!ref.user_id && m.user_id === ref.user_id) ||
        (!!ref.agent_id && m.agent_id === ref.agent_id),
    );
  };

  const toggle = (ref: WorkflowMemberRef) => {
    if (props.mode === 'owner') {
      props.onOwnerChange(isSelected(ref) ? null : ref);
      setOpen(false);
      return;
    }
    const already = isSelected(ref);
    const next = already
      ? props.members.filter(
          (m) =>
            !(
              (!!ref.user_id && m.user_id === ref.user_id) ||
              (!!ref.agent_id && m.agent_id === ref.agent_id)
            ),
        )
      : [...props.members, ref];
    props.onMembersChange(next);
  };

  // ── trigger label ──────────────────────────────────────────────────────
  let triggerContent: React.ReactNode;
  if (props.mode === 'owner') {
    if (props.ownerUserId) {
      triggerContent = (
        <span className="flex items-center gap-1.5 truncate">
          <Avatar kind="user" name={personName(props.ownerUserId)} />
          <span className="truncate text-ink-100">{personName(props.ownerUserId)}</span>
        </span>
      );
    } else if (props.ownerAgentId) {
      const a = agents.find((x) => x.id === props.ownerAgentId);
      triggerContent = (
        <span className="flex items-center gap-1.5 truncate">
          <Avatar kind="agent" name={a?.name ?? 'Agent'} color={a?.color} />
          <span className="truncate text-ink-100">{a?.name ?? 'Agent'}</span>
        </span>
      );
    } else {
      triggerContent = (
        <span className="flex items-center gap-1.5 text-ink-500">
          <User size={14} /> {placeholder ?? 'Unassigned'}
        </span>
      );
    }
  } else {
    triggerContent =
      props.members.length === 0 ? (
        <span className="text-ink-500">{placeholder ?? 'Add members'}</span>
      ) : (
        <span className="text-ink-200">
          {props.members.length} member{props.members.length === 1 ? '' : 's'}
        </span>
      );
  }

  return (
    <div ref={rootRef} className="relative">
      <button
        type="button"
        disabled={disabled}
        onClick={() => setOpen((v) => !v)}
        className="flex h-8 w-full items-center justify-between gap-2 rounded-md border border-line px-2 text-[13px] text-ink-200 transition hover:border-line-strong disabled:opacity-50"
      >
        <span className="min-w-0 flex-1 text-left">{triggerContent}</span>
        <ChevronDown size={14} className="shrink-0 text-ink-500" />
      </button>

      {/* Selected member chips (members mode only). */}
      {props.mode === 'members' && props.members.length > 0 && (
        <div className="mt-1.5 flex flex-wrap gap-1">
          {props.members.map((m) => {
            const label = m.user_id ? personName(m.user_id) : agentName(m.agent_id!);
            const key = m.user_id ?? m.agent_id!;
            return (
              <span
                key={key}
                className="inline-flex items-center gap-1 rounded-full bg-ink-800 px-2 py-0.5 text-[11px] text-ink-200"
              >
                <Avatar
                  kind={m.user_id ? 'user' : 'agent'}
                  name={label}
                  color={m.agent_id ? agents.find((a) => a.id === m.agent_id)?.color : undefined}
                />
                {label}
                {!disabled && (
                  <button
                    type="button"
                    onClick={() => toggle(m)}
                    className="text-ink-500 hover:text-ink-200"
                    aria-label={`Remove ${label}`}
                  >
                    <X size={11} />
                  </button>
                )}
              </span>
            );
          })}
        </div>
      )}

      {open && (
        <div className="absolute left-0 top-full z-30 mt-1 max-h-64 w-full min-w-[13rem] overflow-y-auto rounded-lg border border-line-strong bg-island py-1 shadow-2xl">
          <OwnerCandidateList
            people={people}
            agents={agents}
            isSelected={isSelected}
            onSelect={toggle}
            peopleLabel="People"
            agentsLabel="Agents"
            emptyLabel="No candidates"
          />
        </div>
      )}
    </div>
  );
};
