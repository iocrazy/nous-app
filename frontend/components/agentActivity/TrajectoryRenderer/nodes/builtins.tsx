/**
 * The built-in node renderers. Semantic tokens only (ok / warn / danger /
 * info / agent), no emoji, English copy via i18n. Registered explicitly at
 * the bottom — importing this module is what installs them.
 */

import React from 'react';
import { useTranslation } from 'react-i18next';
import { AlertTriangle, ChevronDown, ChevronRight, Inbox, MessageSquare, RotateCw, ShieldOff, Wallet, Wrench, GitFork } from 'lucide-react';

import type {
  BudgetNode,
  DeniedNode,
  ErrorNode,
  InboxNode,
  StepLine,
  StepNode,
  TurnEndNode,
  UserNode,
} from '../foldEvents';
import { registerTrajectoryNode, type NodeProps } from './registry';

function fmtMs(ms: number | null): string {
  if (ms === null) return '';
  return ms >= 1000 ? `${(ms / 1000).toFixed(1)}s` : `${Math.round(ms)}ms`;
}

function fmtCents(c: number | null): string {
  return c === null ? '' : `¢${c.toFixed(c < 1 ? 3 : 2)}`;
}

const Row: React.FC<React.PropsWithChildren<{ className?: string; testId?: string }>> = ({
  className = '',
  testId,
  children,
}) => (
  <div className={`flex items-center gap-2 px-2.5 py-1.5 text-xs ${className}`} data-testid={testId}>
    {children}
  </div>
);

export const UserNodeView: React.FC<NodeProps<UserNode>> = ({ node }) => {
  const { t } = useTranslation();
  return (
    <Row className="text-ink-300" testId="traj-user">
      <MessageSquare size={12} className="shrink-0 text-info" />
      <span className="truncate">
        <span className="text-ink-500">{t('trajectory.you')}: </span>
        {node.text}
      </span>
    </Row>
  );
};

function lineLabel(line: StepLine, t: (k: string, o?: Record<string, unknown>) => string): string {
  switch (line.type) {
    case 'tool':
      return line.label;
    case 'retry':
      return t('trajectory.retries', { count: line.count, attempt: line.label });
    case 'compaction':
      return t('trajectory.compaction');
    case 'todo':
      return line.label || t('trajectory.todoUpdated');
    case 'output':
      return t('trajectory.output', { chars: (line.detail?.chars as number) ?? line.label.length });
    default:
      return line.label;
  }
}

function summaryText(node: StepNode, t: (k: string, o?: Record<string, unknown>) => string): string {
  const parts: string[] = [];
  if (node.summary.tools) parts.push(t('trajectory.tools', { count: node.summary.tools }));
  if (node.summary.retries) parts.push(t('trajectory.retries', { count: node.summary.retries }));
  if (node.summary.compactions) parts.push(t('trajectory.compaction'));
  if (node.summary.todo) parts.push(`${node.summary.todo.done}/${node.summary.todo.total}`);
  if (node.summary.outputs) parts.push(t('trajectory.outputs', { count: node.summary.outputs }));
  return parts.join(' · ');
}

/** Jump to the forked run's bubble in the same timeline (IssueChatThread gives each run row `id="run-<id>"`). */
function scrollToRun(runId: string): void {
  if (typeof document === 'undefined') return;
  document.getElementById(`run-${runId}`)?.scrollIntoView({ behavior: 'smooth', block: 'center' });
}

export const StepNodeView: React.FC<NodeProps<StepNode>> = ({ node, expanded, onToggle, marks }) => {
  const { t } = useTranslation();
  const open = node.live || expanded;
  const tail = [fmtMs(node.summary.durationMs), fmtCents(node.summary.costCents)].filter(Boolean).join(' · ');
  return (
    <div
      className={`rounded-md ${node.live ? 'border border-ok-line bg-ok-soft/40' : ''}`}
      data-testid={node.live ? 'traj-step-live' : 'traj-step'}
      data-step={node.step}
    >
      <button
        type="button"
        onClick={() => onToggle?.(node)}
        className="flex w-full items-center gap-2 px-2.5 py-1.5 text-left text-xs text-ink-400 hover:text-ink-200"
        aria-expanded={open}
      >
        {node.live ? (
          <span className="inline-block h-3 w-3 shrink-0 animate-spin rounded-full border-2 border-ok-line border-t-ok" />
        ) : open ? (
          <ChevronDown size={11} className="shrink-0 text-ink-600" />
        ) : (
          <ChevronRight size={11} className="shrink-0 text-ink-600" />
        )}
        <span className={`font-mono text-[10px] tracking-widest ${node.live ? 'text-ok' : 'text-ink-600'}`}>
          {t('trajectory.step', { n: node.step }).toUpperCase()}
          {node.live ? ` · ${t('trajectory.live').toUpperCase()}` : ''}
        </span>
        {!open && <span className="truncate text-ink-400">{summaryText(node, t)}</span>}
        <span className="ml-auto shrink-0 tabular-nums text-[11px] text-ink-600">{tail}</span>
      </button>
      {marks && marks.length > 0 && (
        <div className="flex flex-wrap items-center gap-1 px-2.5 pb-1" data-testid="trajectory-fork-marks">
          {marks.map((runId) => (
            <button
              key={runId}
              type="button"
              data-testid="trajectory-fork-mark"
              data-run={runId}
              onClick={(e) => {
                e.stopPropagation();
                scrollToRun(runId);
              }}
              className="inline-flex items-center gap-1 rounded border border-info-line bg-info-soft px-1.5 py-0.5 text-[11px] text-info hover:brightness-110"
            >
              <GitFork size={10} /> {t('fork.mark', 'Fork → #{{run}}', { run: runId.slice(-6) })}
            </button>
          ))}
        </div>
      )}
      {open && (
        <div className="flex flex-col gap-0.5 pb-1.5 pl-7 pr-2.5" data-testid="traj-step-lines">
          {node.lines.map((line) => (
            <div key={line.key} className="flex items-center gap-2 text-xs text-ink-400" data-testid={`traj-line-${line.type}`}>
              {line.type === 'retry' ? (
                <RotateCw size={11} className="shrink-0 text-warn" />
              ) : (
                <Wrench size={11} className={`shrink-0 ${line.ok ? 'text-agent' : 'text-danger'}`} />
              )}
              <span className="truncate">{lineLabel(line, t)}</span>
              {line.type === 'tool' && !line.ok && <span className="text-danger">{t('trajectory.failed')}</span>}
              <span className="ml-auto shrink-0 tabular-nums text-[11px] text-ink-600">{fmtMs(line.durationMs)}</span>
            </div>
          ))}
          {node.live && node.lines.length === 0 && (
            <div className="text-xs text-ink-500">{t('trajectory.thinking')}</div>
          )}
        </div>
      )}
    </div>
  );
};

export const InboxNodeView: React.FC<NodeProps<InboxNode>> = ({ node }) => {
  const { t } = useTranslation();
  return (
    <Row className="rounded-md bg-ok-soft text-ink-200" testId="traj-inbox">
      <Inbox size={12} className="shrink-0 text-ok" />
      <span className="truncate">{t('trajectory.inboxClaimed', { kind: node.inboxKind })}</span>
      {node.step !== null && (
        <span className="ml-auto shrink-0 text-[11px] text-ink-600">{t('trajectory.beforeStep', { n: node.step })}</span>
      )}
    </Row>
  );
};

export const BudgetNodeView: React.FC<NodeProps<BudgetNode>> = ({ node }) => {
  const { t } = useTranslation();
  const over = node.action === 'halt';
  return (
    <Row className={`rounded-md ${over ? 'bg-danger-soft text-danger' : 'bg-warn-soft text-warn'}`} testId="traj-budget">
      <Wallet size={12} className="shrink-0" />
      <span className="truncate">
        {over ? t('trajectory.budgetHalt', { pct: node.pct ?? 100 }) : t('trajectory.budgetWarn', { pct: node.pct ?? 80 })}
      </span>
      {node.spentCents !== null && node.budgetCents !== null && (
        <span className="ml-auto shrink-0 tabular-nums text-[11px]">
          {fmtCents(node.spentCents)} / {fmtCents(node.budgetCents)}
        </span>
      )}
    </Row>
  );
};

export const TurnEndNodeView: React.FC<NodeProps<TurnEndNode>> = ({ node }) => {
  const { t } = useTranslation();
  const bad = !['completed', 'awaiting_approval'].includes(node.reason);
  return (
    <Row className={`border-t border-ink-800 ${bad ? 'text-warn' : 'text-ink-500'}`} testId="traj-turn-end">
      <span className="truncate">{t('trajectory.turnEnd', { reason: node.reason })}</span>
      <span className="ml-auto shrink-0 tabular-nums text-[11px] text-ink-600">
        {t('trajectory.steps', { count: node.steps })}
        {node.costCents !== null ? ` · ${fmtCents(node.costCents)}` : ''}
      </span>
    </Row>
  );
};

export const DeniedNodeView: React.FC<NodeProps<DeniedNode>> = ({ node }) => {
  const { t } = useTranslation();
  return (
    <Row className="rounded-md bg-danger-soft text-danger" testId="traj-denied">
      <ShieldOff size={12} className="shrink-0" />
      <span className="truncate">{t('trajectory.denied', { tool: node.tool })}</span>
      <span className="ml-auto shrink-0 truncate text-[11px] opacity-80">{node.reason}</span>
    </Row>
  );
};

export const ErrorNodeView: React.FC<NodeProps<ErrorNode>> = ({ node }) => (
  <Row className="text-danger" testId="traj-error">
    <AlertTriangle size={12} className="shrink-0" />
    <span className="truncate">{node.text}</span>
  </Row>
);

registerTrajectoryNode('user', UserNodeView);
registerTrajectoryNode('step', StepNodeView);
registerTrajectoryNode('inbox', InboxNodeView);
registerTrajectoryNode('budget', BudgetNodeView);
registerTrajectoryNode('turn_end', TurnEndNodeView);
registerTrajectoryNode('denied', DeniedNodeView);
registerTrajectoryNode('error', ErrorNodeView);
