/**
 * EpisodeNodeCard — the Overview accordion's single-node info card (IA
 * redesign Task 5, spec `2026-08-10-workspace-ia-redesign`). Fills the
 * `renderNodeCard` slot `WorkspaceOverview`/Task 4 left as a placeholder: the
 * selected node's name/status header, an entry button whose destination and
 * label follow the node's creative `surface` (nodeSurface.ts::resolveSurface
 * — script/storyboard/renders open that surface, a deliverable-only node
 * opens its Stage Board), a compact read-only facts strip (owner/schedule/
 * deliverable), and a footer with "Open in Todolist" + a Settings deep-link.
 * The cursor node additionally gets Back/Complete-stage buttons that drive
 * the workspace's shared advance gate.
 *
 * Deliberately LEAN (先只读+advance, per the task brief): unlike
 * `CurrentNodeCard` (the pre-IA-redesign Overview card) this never edits
 * owner/members/schedule/brief in place — that full surface now lives on
 * `WorkspaceStageBoard` (reachable via the entry button on a non-surface
 * node, or via the Settings deep-link). `canEditConfig` is threaded through
 * as a prop so Task 9 can flip the owner/schedule facts to live pickers
 * without a new prop contract; until then both branches render the same
 * read-only `<span>` — see the `canEditConfig` ternaries below.
 *
 * `onOpenSettings`'s first argument is the episode id the card itself has no
 * way to know (`ProjectStageNode` carries no `episode_id` field — confirmed
 * against types.ts, see `WorkspaceOverview`'s own file-doc note on this).
 * The caller (`ProjectWorkspace.renderNodeCard`) already has it in scope from
 * its own `(episodeId, nodeId)` slot signature and closes over it when
 * building this prop, so the value this component passes for that argument
 * is never actually read — the settings button below passes `''`.
 */

import type { FC, ReactNode } from 'react';
import { useTranslation } from 'react-i18next';
import { ArrowLeft, ArrowRight, Bot, ExternalLink, FileCheck2, Settings, User } from 'lucide-react';
import type { ProjectStageNode } from '../../types';
import { resolveSurface } from './nodeSurface';
import { NODE_STATUS_CONFIG, NODE_STATUS_LABEL } from '../workflow/nodeStatus';

export interface EpisodeNodeCardProps {
  node: ProjectStageNode;
  /** Observer can edit owner/schedule in place — hardwired `false` until
   * Task 9 wires the real permission; both branches render read-only today. */
  canEditConfig: boolean;
  /** 评审修复轮1 (Important #1): Back/Complete-stage act on the SHARED
   * workflow cursor — a read-only member seeing (and being able to click)
   * those buttons only to eat a generic error toast once the server's real
   * gate (`verify_project_write_access`) rejects it is a UX regression, not
   * a safety hole. Mirrors `CurrentNodeCard`'s `isActive && canWrite` gate
   * exactly. */
  canWrite: boolean;
  /** Non-cursor nodes hide Complete-stage/Back — same semantics as
   * `CurrentNodeCard`'s `isActive`: those buttons act on the shared workflow
   * cursor, not an arbitrary node. */
  isCursorNode: boolean;
  /** Entry button — reuses `ProjectWorkspace.handleSelectNode`'s existing
   * surface routing (script/storyboard/renders/Stage Board). */
  onEnterSurface: (node: ProjectStageNode) => void;
  onRequestAdvance: (direction: 'forward' | 'back') => void;
  onOpenTodolist: () => void;
  /** Deep-link into the node's Settings config tab. */
  onOpenSettings: (episodeId: string, nodeId: string) => void;
}

const Fact: FC<{ label: string; children: ReactNode }> = ({ label, children }) => (
  <div className="grid grid-cols-[5.5rem_1fr] items-start gap-3 py-1">
    <span className="pt-0.5 text-[11px] uppercase tracking-wider text-ink-600">{label}</span>
    <div className="min-w-0">{children}</div>
  </div>
);

const ENTRY_LABEL_KEY: Record<'script' | 'storyboard' | 'renders' | 'stage', [string, string]> = {
  script: ['projects.nodeCard.openScript', 'Open Script'],
  storyboard: ['projects.nodeCard.openStoryboard', 'Open Storyboard'],
  renders: ['projects.nodeCard.openRenders', 'Open Renders'],
  stage: ['projects.nodeCard.openStageBoard', 'Open Stage Board'],
};

export const EpisodeNodeCard: FC<EpisodeNodeCardProps> = ({
  node,
  canEditConfig,
  canWrite,
  isCursorNode,
  onEnterSurface,
  onRequestAdvance,
  onOpenTodolist,
  onOpenSettings,
}) => {
  const { t } = useTranslation();
  const meta = NODE_STATUS_CONFIG[node.status];
  const surface = resolveSurface(node);
  const [entryKey, entryFallback] = ENTRY_LABEL_KEY[surface ?? 'stage'];

  const hasOwner = Boolean(node.owner_user_id || node.owner_agent_id);
  const ownerLabel = !hasOwner
    ? t('projects.workflow.unassigned')
    : node.owner_agent_id
      ? t('projects.workflow.genericAgent')
      : t('projects.workflow.genericMember');

  const hasSchedule = Boolean(node.planned_start || node.planned_due);
  const hasDeliverable = Boolean(node.deliverable_label || node.deliverable_required);

  return (
    <div
      data-testid="episode-node-card"
      data-node-id={node.id}
      className="rounded-xl border border-line bg-island p-4"
    >
      <div className="mb-2 flex flex-wrap items-center gap-2">
        <span className={`h-2.5 w-2.5 rounded-full ${meta.dot}`} aria-hidden />
        <h3 className="text-sm font-semibold text-ink-100">{node.name}</h3>
        <span className={`text-[11px] font-medium ${meta.text}`}>{NODE_STATUS_LABEL[node.status]}</span>
        <button
          type="button"
          onClick={() => onEnterSurface(node)}
          data-testid="node-card-enter"
          className="ml-auto inline-flex items-center gap-1.5 rounded-md border px-2.5 py-1 text-[12.5px] transition"
          style={{
            background: 'var(--accent-soft)',
            color: 'var(--accent-text)',
            borderColor: 'var(--accent-border)',
          }}
        >
          {t(entryKey, entryFallback)}
        </button>
      </div>

      <Fact label={t('projects.workflow.owner')}>
        {canEditConfig ? (
          // Task 9 replaces this branch with a real OwnerPicker — read-only
          // for now, identical markup to the false branch below.
          <span data-testid="node-card-owner" className="inline-flex items-center gap-1.5 text-[13px] text-ink-300">
            {hasOwner && (node.owner_agent_id ? <Bot size={12} /> : <User size={12} />)}
            {ownerLabel}
          </span>
        ) : (
          <span data-testid="node-card-owner" className="inline-flex items-center gap-1.5 text-[13px] text-ink-300">
            {hasOwner && (node.owner_agent_id ? <Bot size={12} /> : <User size={12} />)}
            {ownerLabel}
          </span>
        )}
      </Fact>

      {hasSchedule && (
        <Fact label={t('projects.workflow.schedule')}>
          <span data-testid="node-card-schedule" className="text-[13px] text-ink-300">
            {node.planned_start ?? '—'} → {node.planned_due ?? '—'}
          </span>
        </Fact>
      )}

      {hasDeliverable && (
        <Fact label={t('projects.workflow.deliverable')}>
          <span data-testid="node-card-deliverable" className="inline-flex items-center gap-2 text-[13px] text-ink-300">
            <FileCheck2 size={13} className="shrink-0 text-ink-500" />
            <span className="truncate">{node.deliverable_label || '—'}</span>
            <span
              className={`shrink-0 rounded-full px-1.5 py-0.5 text-[10px] ${
                (node.deliverable_file_count ?? 0) > 0
                  ? 'bg-ok-soft text-ok'
                  : 'bg-ink-800 text-ink-500'
              }`}
            >
              {t('projects.workflow.deliverables.filesFiled', { count: node.deliverable_file_count ?? 0 })}
            </span>
          </span>
        </Fact>
      )}

      <div className="mt-3 flex items-center gap-2 border-t border-line pt-3">
        <button
          type="button"
          onClick={onOpenTodolist}
          data-testid="node-card-todolist"
          className="inline-flex items-center gap-1.5 rounded-md border border-line px-2.5 py-1.5 text-[12.5px] text-ink-300 transition hover:border-line-strong hover:text-ink-100"
        >
          <ExternalLink size={13} /> {t('projects.workflow.openInTodolist')}
        </button>
        <button
          type="button"
          onClick={() => onOpenSettings('', node.id)}
          data-testid="node-card-settings"
          className="inline-flex items-center gap-1.5 rounded-md px-2 py-1.5 text-[12.5px] text-ink-600 transition hover:text-ink-300"
        >
          <Settings size={13} /> {t('projects.nodeCard.settings', 'Node settings')}
        </button>

        {isCursorNode && canWrite && (
          <div data-testid="node-card-advance" className="ml-auto flex items-center gap-2">
            <button
              type="button"
              onClick={() => onRequestAdvance('back')}
              data-testid="node-card-back"
              className="inline-flex items-center gap-1.5 rounded-md px-2.5 py-1.5 text-[12.5px] text-ink-500 transition hover:bg-ink-800 hover:text-ink-200"
            >
              <ArrowLeft size={13} /> {t('projects.workflow.back')}
            </button>
            <button
              type="button"
              onClick={() => onRequestAdvance('forward')}
              data-testid="node-card-complete"
              className="inline-flex items-center gap-1.5 rounded-md border px-3 py-1.5 text-[12.5px] transition"
              style={{
                background: 'var(--accent-soft)',
                color: 'var(--accent-text)',
                borderColor: 'var(--accent-border)',
              }}
            >
              {t('projects.workflow.completeStage')} <ArrowRight size={13} />
            </button>
          </div>
        )}
      </div>
    </div>
  );
};

export default EpisodeNodeCard;
