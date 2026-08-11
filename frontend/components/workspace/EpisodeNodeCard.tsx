/**
 * EpisodeNodeCard — the Overview accordion's single-node info card (IA
 * redesign Task 5, spec `2026-08-10-workspace-ia-redesign`). Fills the
 * `renderNodeCard` slot `WorkspaceOverview`/Task 4 left as a placeholder: the
 * selected node's name/status header, an entry button whose destination and
 * label follow the node's creative `surface` (nodeSurface.ts::resolveSurface
 * — script/storyboard/renders open that surface, a deliverable-only node
 * opens its Stage Board), a compact facts strip (owner/schedule/
 * deliverable), and a footer with "Open in Todolist" + a Settings deep-link.
 * The cursor node additionally gets Back/Complete-stage buttons that drive
 * the workspace's shared advance gate.
 *
 * Task 9 (角色门控行内编辑) flips the owner/schedule facts to live pickers
 * when `canEditConfig` is true (project owner OR the episode's own owner —
 * computed by `ProjectWorkspace`, see that file): empty state renders a
 * dashed "+ Assign Owner" / "+ Set Schedule" pill (hover agent-soft), a
 * filled value renders a quiet button (looks like plain text until hover,
 * which reveals a border + `--island-2` background + a trailing chevron).
 * Owner opens a small local candidate menu (people/agents props — NOT the
 * full `OwnerPicker` widget: that component owns its own trigger+dropdown
 * chrome with no way to inject this card's pill/quiet-button styling or a
 * `data-testid` on the actual clickable element, so this menu reuses its
 * `PersonOption`/`AgentOption` wire shapes but renders its own trigger+list).
 * Schedule opens the real `DateRangePopover` (Task 8) anchored on the
 * trigger button, which fits perfectly — it already takes an `anchorEl`.
 *
 * Deliberately NO local optimistic/revert state and NO toast here: both
 * `onPatchNode` calls below are `void`-fired and forgotten. The prop's
 * contract is "never rejects" — `ProjectWorkspace`'s real implementation
 * owns the optimistic update, the 403 (`node_config_forbidden`) / generic
 * error revert, and the toast (single toaster, avoids a double-toast if
 * this component also caught and surfaced one). See
 * `ProjectWorkspace.test.tsx` for that coverage.
 *
 * `onOpenSettings`'s first argument is the episode id the card itself has no
 * way to know (`ProjectStageNode` carries no `episode_id` field — confirmed
 * against types.ts, see `WorkspaceOverview`'s own file-doc note on this).
 * The caller (`ProjectWorkspace.renderNodeCard`) already has it in scope from
 * its own `(episodeId, nodeId)` slot signature and closes over it when
 * building this prop, so the value this component passes for that argument
 * is never actually read — the settings button below passes `''`.
 *
 * Task 9 (#1787 遗留修复): the deliverable fact is now a quiet-button link
 * to that node's Stage Board (`onOpenStage`), not plain text. #1787 flipped
 * the header entry button to route strictly by `resolveSurface` — a
 * script/storyboard/renders-surface node's own deliverable/archive view
 * (Stage Board) lost its only remaining entry point once that happened.
 * Readonly viewers can click it too: this is navigation (viewing archived
 * files/tasks/brief), not a config edit, so it does not gate on
 * `canEditConfig`.
 */

import { useEffect, useRef, useState, type FC, type ReactNode } from 'react';
import { useTranslation } from 'react-i18next';
import {
  ArrowLeft,
  ArrowRight,
  Bot,
  ChevronDown,
  ExternalLink,
  FileCheck2,
  Plus,
  Settings,
  User,
} from 'lucide-react';
import type { ProjectNodePatch, ProjectStageNode, WorkflowMemberRef } from '../../types';
import { resolveSurface } from './nodeSurface';
import { NODE_STATUS_CONFIG, NODE_STATUS_LABEL } from '../workflow/nodeStatus';
import { DateRangePopover } from '../common/DateRangePopover';
import { OwnerCandidateList, type AgentOption, type PersonOption } from '../workflow/OwnerCandidateList';

/** The subset of `ProjectNodePatch` this card's editable facts ever send —
 * owner/schedule only (members/skipped/form_data/brief stay on the fuller
 * Stage Board surface, Task 5's file-doc "deliberately LEAN" note). */
export type NodeConfigPatch = Pick<
  ProjectNodePatch,
  'owner_user_id' | 'owner_agent_id' | 'planned_start' | 'planned_due'
>;

export interface EpisodeNodeCardProps {
  node: ProjectStageNode;
  /** Project owner OR this episode's own owner may edit owner/schedule in
   * place (Task 9, spec §5) — computed by `ProjectWorkspace` from
   * `project.owner_id` / the episode's `owner_id` (episodes/progress). */
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
  /** Task 9 (#1787 遗留修复): the deliverable fact's entry into that node's
   * Stage Board (archived files/tasks/brief) — the surface-routed entry
   * button (`onEnterSurface` above) no longer reaches Stage Board for a
   * script/storyboard/renders-surface node once it has its own deliverable,
   * so this is the fact's own dedicated link. `ProjectWorkspace` already has
   * this exact routing as `handleOpenStage`; readonly viewers can click it
   * too — it is navigation, not an edit. */
  onOpenStage: (nodeId: string) => void;
  /** Candidate owners for the editable owner field's menu — only consulted
   * when `canEditConfig` (read-only branch never touches them), so an
   * absent/empty array is a safe default for every call site that never
   * grants edit access. */
  people?: PersonOption[];
  agents?: AgentOption[];
  /** In-place PATCH for owner/schedule (Task 9). Implemented by
   * `ProjectWorkspace` (optimistic update, 403/generic-error revert, toast —
   * see that file); this component only ever `void`-fires it, so the
   * contract is "never rejects". */
  onPatchNode?: (nodeId: string, patch: NodeConfigPatch) => Promise<void>;
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

/** Trigger button chrome shared by the owner/schedule editable facts —
 * empty = dashed pill inviting a click, filled = "quiet" (text-like until
 * hover reveals a border/background + chevron), per the Task 9 mock v4. */
const EditableFactTrigger: FC<{
  testId: string;
  empty: boolean;
  emptyLabel: string;
  onClick: (e: React.MouseEvent<HTMLButtonElement>) => void;
  children?: ReactNode;
}> = ({ testId, empty, emptyLabel, onClick, children }) =>
  empty ? (
    <button
      type="button"
      data-testid={testId}
      onClick={onClick}
      className="inline-flex items-center gap-1 rounded-full border border-dashed border-line px-2 py-0.5 text-[12.5px] text-ink-500 transition hover:border-agent-line hover:bg-agent-soft hover:text-agent"
    >
      <Plus size={11} /> {emptyLabel}
    </button>
  ) : (
    <button
      type="button"
      data-testid={testId}
      onClick={onClick}
      className="group inline-flex items-center gap-1 rounded-md px-1.5 py-0.5 text-[13px] text-ink-300 transition hover:border hover:border-line hover:bg-island-2"
    >
      {children}
      <ChevronDown size={11} className="shrink-0 text-ink-600 opacity-0 transition-opacity group-hover:opacity-100" />
    </button>
  );

export const EpisodeNodeCard: FC<EpisodeNodeCardProps> = ({
  node,
  canEditConfig,
  canWrite,
  isCursorNode,
  onEnterSurface,
  onRequestAdvance,
  onOpenTodolist,
  onOpenSettings,
  onOpenStage,
  people = [],
  agents = [],
  onPatchNode = async () => {},
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

  // ── Task 9: owner menu (local trigger + list — see file-doc for why this
  // doesn't reuse the `OwnerPicker` widget directly). ──────────────────────
  const [ownerMenuOpen, setOwnerMenuOpen] = useState(false);
  const ownerWrapRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    if (!ownerMenuOpen) return undefined;
    const onDown = (e: MouseEvent) => {
      if (ownerWrapRef.current && !ownerWrapRef.current.contains(e.target as Node)) {
        setOwnerMenuOpen(false);
      }
    };
    document.addEventListener('mousedown', onDown);
    return () => document.removeEventListener('mousedown', onDown);
  }, [ownerMenuOpen]);

  const patchOwner = (patch: Pick<NodeConfigPatch, 'owner_user_id' | 'owner_agent_id'>) => {
    setOwnerMenuOpen(false);
    void onPatchNode(node.id, patch);
  };

  // `OwnerCandidateList`'s selection contract (shared with `OwnerPicker`,
  // 评审修复轮1) speaks `WorkflowMemberRef` ({user_id} xor {agent_id}), not
  // this card's own `NodeConfigPatch` shape — translate at the boundary.
  const isOwnerSelected = (ref: WorkflowMemberRef): boolean =>
    (!!ref.user_id && ref.user_id === node.owner_user_id) ||
    (!!ref.agent_id && ref.agent_id === node.owner_agent_id);
  const selectOwner = (ref: WorkflowMemberRef) =>
    patchOwner({ owner_user_id: ref.user_id ?? null, owner_agent_id: ref.agent_id ?? null });

  // ── Task 9: schedule popover (DateRangePopover, anchored on the trigger). ─
  const [scheduleAnchor, setScheduleAnchor] = useState<HTMLElement | null>(null);

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
          <div ref={ownerWrapRef} className="relative inline-block">
            <EditableFactTrigger
              testId="node-card-owner"
              empty={!hasOwner}
              emptyLabel={t('projects.nodeCard.assignOwner', '+ Assign Owner')}
              onClick={() => setOwnerMenuOpen((v) => !v)}
            >
              {node.owner_agent_id ? <Bot size={12} /> : <User size={12} />}
              {ownerLabel}
            </EditableFactTrigger>
            {ownerMenuOpen && (
              <div className="absolute left-0 top-full z-30 mt-1 max-h-64 w-48 overflow-y-auto rounded-lg border border-line-strong bg-island py-1 shadow-2xl">
                {hasOwner && (
                  <button
                    type="button"
                    onClick={() => patchOwner({ owner_user_id: null, owner_agent_id: null })}
                    className="flex w-full items-center px-2.5 py-1.5 text-[13px] text-ink-500 hover:bg-ink-800"
                  >
                    {t('projects.nodeCard.unassign', 'Unassign')}
                  </button>
                )}
                <OwnerCandidateList
                  people={people}
                  agents={agents}
                  isSelected={isOwnerSelected}
                  onSelect={selectOwner}
                  peopleLabel={t('projects.nodeCard.people', 'People')}
                  agentsLabel={t('projects.nodeCard.agents', 'Agents')}
                  emptyLabel={t('projects.nodeCard.noCandidates', 'No candidates')}
                />
              </div>
            )}
          </div>
        ) : (
          <span data-testid="node-card-owner" className="inline-flex items-center gap-1.5 text-[13px] text-ink-300">
            {hasOwner && (node.owner_agent_id ? <Bot size={12} /> : <User size={12} />)}
            {ownerLabel}
          </span>
        )}
      </Fact>

      {/* 评审修复轮 (Minor #10): spec §3's readonly rule ("无权者:纯文本
          （空值显示灰色「未指派/未设置」）") applies to schedule same as
          owner — the owner Fact above always renders (see `ownerLabel`'s
          "Unassigned" fallback), but this row used to OMIT itself entirely
          for a readonly + empty schedule instead of showing a dim "Not set"
          — inconsistent with the owner row right above it. Always render
          the row now; only the readonly+empty branch is new. */}
      <Fact label={t('projects.workflow.schedule')}>
        {canEditConfig ? (
          <EditableFactTrigger
            testId="node-card-schedule"
            empty={!hasSchedule}
            emptyLabel={t('projects.nodeCard.setSchedule', '+ Set Schedule')}
            onClick={(e) => setScheduleAnchor(e.currentTarget)}
          >
            {node.planned_start ?? '—'} → {node.planned_due ?? '—'}
          </EditableFactTrigger>
        ) : hasSchedule ? (
          <span data-testid="node-card-schedule" className="text-[13px] text-ink-300">
            {node.planned_start ?? '—'} → {node.planned_due ?? '—'}
          </span>
        ) : (
          <span data-testid="node-card-schedule" className="text-[13px] text-ink-600">
            {t('projects.workflow.notSet', 'Not set')}
          </span>
        )}
      </Fact>
      <DateRangePopover
        anchorEl={scheduleAnchor}
        start={node.planned_start}
        end={node.planned_due}
        onChange={(start, end) => {
          setScheduleAnchor(null);
          void onPatchNode(node.id, { planned_start: start, planned_due: end });
        }}
        onClose={() => setScheduleAnchor(null)}
      />

      {hasDeliverable && (
        <Fact label={t('projects.workflow.deliverable')}>
          <button
            type="button"
            data-testid="node-card-deliverable"
            onClick={() => onOpenStage(node.id)}
            title={t('projects.nodeCard.openDeliverablesBoard', 'Open deliverables board')}
            className="group inline-flex max-w-full items-center gap-2 rounded-md px-1.5 py-0.5 text-[13px] text-ink-300 transition hover:border hover:border-line hover:bg-island-2"
          >
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
            <ChevronDown size={11} className="shrink-0 -rotate-90 text-ink-600 opacity-0 transition-opacity group-hover:opacity-100" />
          </button>
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
