/**
 * SubTaskCard — TapNow-style sub-task card rendered inline above an
 * assistant message bubble. One card per Skill / Delegate dispatch the
 * LLM made on this turn, in emission order.
 *
 * Compact by default: shows the tool name + a one-line summary
 * (delegate target slug, skill slug, queued/error status). Click to
 * expand and see the raw args + result JSON.
 */

import React, { useState } from 'react';
import { ChevronRight, ChevronDown, Workflow, Wrench, AlertTriangle, CheckCircle2 } from 'lucide-react';

import type { ChatToolCall } from '../../types';

export interface SubTaskCardProps {
  call: ChatToolCall;
}

export interface ToolCallSummary {
  /** Short title for the collapsed row. */
  label: string;
  /** Status pill text (queued / error / done / loaded). */
  status: string;
  /** True when the result payload contains a string ``error`` field. */
  isError: boolean;
}

/**
 * Compute the one-liner shown in the collapsed sub-task card. Pure
 * function — no React, easy to unit test.
 *
 * Recognised tool shapes:
 *   - Delegate: args = {agent_slug, prompt, ...}, result = {status, ...}
 *   - Skill: args = {skill, file?}, result = {prompt, ...}
 *
 * Any other ``call.name`` falls back to a generic label so future
 * tools render without a code change.
 */
export function summarizeToolCall(call: ChatToolCall): ToolCallSummary {
  const args = call.args ?? {};
  const result = call.result ?? {};

  const errorText =
    typeof result.error === 'string' ? result.error : undefined;
  const isError = Boolean(errorText);

  if (call.name === 'Delegate') {
    const slug = typeof args.agent_slug === 'string' ? args.agent_slug : 'agent';
    const status =
      errorText ||
      (typeof result.status === 'string' ? result.status : 'queued');
    return { label: `→ ${slug}`, status, isError };
  }

  if (call.name === 'Skill') {
    const skillSlug =
      typeof args.skill === 'string' ? args.skill : 'skill';
    const file = typeof args.file === 'string' ? args.file : null;
    const label = file ? `${skillSlug} · ${file}` : skillSlug;
    const status = errorText || (file ? 'loaded' : 'read');
    return { label, status, isError };
  }

  return {
    label: call.name,
    status: errorText || 'done',
    isError,
  };
}

function safeStringify(value: unknown): string {
  try {
    return JSON.stringify(value, null, 2);
  } catch {
    return String(value);
  }
}

export function SubTaskCard({ call }: SubTaskCardProps): React.ReactElement {
  const [expanded, setExpanded] = useState(false);
  const { label, status, isError } = summarizeToolCall(call);

  const Icon = call.name === 'Delegate' ? Workflow : Wrench;
  const StatusIcon = isError ? AlertTriangle : CheckCircle2;

  const accent = isError
    ? 'text-red-400 bg-red-500/10 border-red-500/30'
    : call.name === 'Delegate'
      ? 'text-indigo-300 bg-indigo-500/10 border-indigo-500/30'
      : 'text-amber-300 bg-amber-500/10 border-amber-500/30';

  return (
    <div className={`rounded-lg border ${accent} text-xs mb-1.5`}>
      <button
        type="button"
        onClick={() => setExpanded((v) => !v)}
        className="flex w-full items-center gap-1.5 px-2 py-1.5 text-left hover:bg-white/5 rounded-lg"
      >
        {expanded ? (
          <ChevronDown size={12} className="flex-shrink-0 opacity-70" />
        ) : (
          <ChevronRight size={12} className="flex-shrink-0 opacity-70" />
        )}
        <Icon size={12} className="flex-shrink-0" />
        <span className="font-medium flex-1 truncate" title={label}>
          {label}
        </span>
        <StatusIcon size={11} className="flex-shrink-0 opacity-80" />
        <span className="opacity-80 truncate max-w-[40%]" title={status}>
          {status}
        </span>
      </button>

      {expanded && (
        <div className="px-2 pb-2 pt-1 space-y-2 border-t border-white/10">
          <div>
            <div className="text-[10px] uppercase tracking-wide opacity-60 mb-0.5">
              args
            </div>
            <pre className="text-[10px] font-mono bg-black/30 rounded p-1.5 overflow-x-auto whitespace-pre-wrap break-words">
              {safeStringify(call.args)}
            </pre>
          </div>
          <div>
            <div className="text-[10px] uppercase tracking-wide opacity-60 mb-0.5">
              result
            </div>
            <pre className="text-[10px] font-mono bg-black/30 rounded p-1.5 overflow-x-auto whitespace-pre-wrap break-words max-h-48 overflow-y-auto">
              {safeStringify(call.result)}
            </pre>
          </div>
        </div>
      )}
    </div>
  );
}

export interface SubTaskListProps {
  calls: ChatToolCall[];
}

/**
 * Renders the sub-task list above an assistant bubble. Returns null
 * when empty so callers can drop it in unconditionally.
 */
export function SubTaskList({ calls }: SubTaskListProps): React.ReactElement | null {
  if (!calls || calls.length === 0) return null;
  return (
    <div className="px-3 pt-2">
      {calls.map((call, idx) => (
        <SubTaskCard
          key={`${call.iteration}-${call.name}-${idx}`}
          call={call}
        />
      ))}
    </div>
  );
}
