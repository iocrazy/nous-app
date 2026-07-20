/**
 * WorkspaceTopBar — the project-condition strip (spec frame: 顶部项目条). Project
 * identity on the left; a read-only stage read-out on the right (the SOP stage
 * is informational here — advancing/jumping stages happens elsewhere, not from
 * this bar). When the studio editor is open it also carries a film "场记板"
 * (slate) read-out — EP·S — so the writer always knows where they are.
 */

import { useTranslation } from 'react-i18next';
import { ArrowLeft, Bot } from 'lucide-react';
import { MiniStepper } from './MiniStepper';
import type { ProjectStage, ProjectWorkflow } from '../../types';

interface WorkspaceTopBarProps {
  projectName: string;
  onBack: () => void;
  catalog: ProjectStage[];
  currentStage: ProjectStage | null;
  currentIndex: number;
  canWrite?: boolean;
  /** Film slate read-out (studio only): the current episode + scene numbers. */
  slate?: { ep: number; scene?: number | null } | null;
  /** The project's workflow — when present it drives the top-bar stepper. */
  workflow?: ProjectWorkflow | null;
  /** Adjacent-node jump → the server-computed advance/back gate. */
  onRequestAdvance?: (direction: 'forward' | 'back') => void;
  /** Non-adjacent jump → land on the Overview node card. */
  onJumpToNode?: (nodeId: string) => void;
}

const noopJump = () => {};

export function WorkspaceTopBar({
  projectName,
  onBack,
  catalog,
  currentStage,
  currentIndex,
  canWrite = true,
  slate,
  workflow,
  onRequestAdvance,
  onJumpToNode,
}: WorkspaceTopBarProps) {
  const { t } = useTranslation();

  // Workflow supersedes the SOP read-out when the project has one. Map its
  // nodes onto the MiniStepper's stage shape; adjacent dots run the advance /
  // back gate, non-adjacent dots navigate to the node card.
  const hasWorkflow = !!workflow?.has_workflow && workflow.nodes.length > 0;
  const wfNodes = workflow?.nodes ?? [];
  const wfCurrentIndex = hasWorkflow
    ? wfNodes.findIndex((n) => n.id === workflow?.current_node_id)
    : -1;
  const wfCatalog: ProjectStage[] = wfNodes.map((n) => ({
    id: n.id,
    slug: n.name,
    name: n.name,
    sort_order: n.sort_order,
    tools_recommended: [],
  }));
  const handleWorkflowJump = (stage: ProjectStage) => {
    const targetIndex = wfNodes.findIndex((n) => n.id === stage.id);
    if (targetIndex === wfCurrentIndex + 1) onRequestAdvance?.('forward');
    else if (targetIndex === wfCurrentIndex - 1) onRequestAdvance?.('back');
    else onJumpToNode?.(stage.id);
  };
  const agentsActive = workflow?.agents_active ?? 0;

  return (
    <div
      data-testid="workspace-topbar"
      className="flex items-center gap-3 px-4 py-2 border-b border-line flex-wrap"
    >
      <button
        data-testid="workspace-back-btn"
        onClick={onBack}
        title={t('projects.nav.backToList')}
        className="p-1 rounded hover:bg-ink-700 text-ink-400 hover:text-ink-200 transition-colors shrink-0"
      >
        <ArrowLeft size={15} />
      </button>
      <span className="w-6 h-6 rounded-md bg-indigo-500 text-white grid place-items-center text-[10px] font-bold shrink-0">
        {(projectName[0] || '?').toUpperCase()}
      </span>
      <span className="text-[13px] font-semibold text-ink-100 truncate">{projectName}</span>

      {slate && (
        <span
          data-testid="ws-slate"
          className="flex items-center gap-1.5 rounded-lg bg-[#16121f] pl-1.5 pr-2.5 py-1 shrink-0"
        >
          <span
            aria-hidden
            className="w-[22px] h-3 rounded-[3px]"
            style={{ background: 'repeating-linear-gradient(-45deg,#f4f1fb 0 4px,#16121f 4px 8px)' }}
          />
          <span className="font-mono text-[10px] font-semibold tracking-wide text-[#f4f1fb]">
            EP{slate.ep}
            {slate.scene ? ` · S${slate.scene}` : ''}
          </span>
        </span>
      )}

      <div className="flex-1" />

      {agentsActive > 0 && (
        <span
          data-testid="workspace-agents-active"
          className="inline-flex items-center gap-1 rounded-full bg-amber-500/12 px-2.5 py-1 text-[11px] font-medium text-amber-400 whitespace-nowrap"
        >
          <Bot size={12} />
          {t('projects.workflow.agentsActive', { count: agentsActive })}
        </span>
      )}

      {hasWorkflow ? (
        <MiniStepper
          catalog={wfCatalog}
          currentIndex={wfCurrentIndex}
          canWrite={canWrite}
          onJump={handleWorkflowJump}
        />
      ) : (
        catalog.length > 0 &&
        currentStage && (
          <>
            {/* Read-only: the dots show progress but cannot be jumped from here. */}
            <MiniStepper
              catalog={catalog}
              currentIndex={currentIndex}
              canWrite={false}
              onJump={noopJump}
            />
            <span
              data-testid="workspace-stage-chip"
              className="font-mono text-[10px] uppercase tracking-wider text-[var(--stall)] bg-[color-mix(in_srgb,var(--stall)_12%,transparent)] rounded-full px-2.5 py-1 font-semibold whitespace-nowrap"
            >
              {(currentStage.slug || currentStage.name).toUpperCase()} {currentIndex + 1}/{catalog.length}
            </span>
          </>
        )
      )}
    </div>
  );
}

export default WorkspaceTopBar;
