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
import { Bot, Check, ChevronDown, User, X } from 'lucide-react';
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

function Avatar({
  kind,
  name,
  color,
}: {
  kind: 'user' | 'agent';
  name: string;
  color?: string;
}) {
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
}

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
          {people.length > 0 && (
            <>
              <div className="px-2.5 py-1 text-[10px] font-medium uppercase tracking-wider text-ink-600">
                People
              </div>
              {people.map((p) => {
                const ref: WorkflowMemberRef = { user_id: p.id };
                const sel = isSelected(ref);
                return (
                  <button
                    key={p.id}
                    type="button"
                    onClick={() => toggle(ref)}
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
                Agents
              </div>
              {agents.map((a) => {
                const ref: WorkflowMemberRef = { agent_id: a.id };
                const sel = isSelected(ref);
                return (
                  <button
                    key={a.id}
                    type="button"
                    onClick={() => toggle(ref)}
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
            <div className="px-2.5 py-2 text-[13px] text-ink-500">No candidates</div>
          )}
        </div>
      )}
    </div>
  );
};
