/**
 * WorkspaceTopBar — the project-condition strip (spec frame: 顶部项目条). Project
 * identity on the left; a read-only workflow node stepper on the right (G3
 * dropped the legacy SOP `current_stage` read-out — a No-workflow project
 * shows no stepper here at all). When the studio editor is open it also
 * carries a film "场记板" (slate) read-out — EP·S — so the writer always
 * knows where they are.
 */

import { useEffect, useState } from 'react';
import { useTranslation } from 'react-i18next';
import { ArrowLeft, Bot, Zap, ZapOff } from 'lucide-react';
import { MiniStepper } from './MiniStepper';
import { updateProject } from '../../services/projectsService';
import { useOptionalToast } from '../Toast';
import type { Project, ProjectStage, ProjectWorkflow } from '../../types';

interface WorkspaceTopBarProps {
  projectName: string;
  onBack: () => void;
  canWrite?: boolean;
  /** Film slate read-out (studio only): the current episode + scene numbers. */
  slate?: { ep: number; scene?: number | null } | null;
  /** The project's workflow — when present it drives the top-bar stepper. */
  workflow?: ProjectWorkflow | null;
  /** Adjacent-node jump → the server-computed advance/back gate. */
  onRequestAdvance?: (direction: 'forward' | 'back') => void;
  /** Non-adjacent jump → land on the Overview node card. */
  onJumpToNode?: (nodeId: string) => void;
  /** M4 Autopilot (mig 395, task O1/O2): the project-level master switch —
   * omitted entirely hides the chip (e.g. a host that hasn't wired it
   * through yet). `projectId` is required alongside it since the chip owns
   * its own PATCH call (optimistic + revert-on-error, mirroring
   * AgentMemoriesPanel's own capture-before-mutate / revert-on-failure
   * idiom — this is the first topbar mutation, so there's no prior topbar
   * pattern to match). */
  projectId?: string;
  autopilotEnabled?: boolean;
  /** Bubbles the fresh value up after a successful toggle so the caller can
   * sync its own `project` state (mirrors `ProjectWorkspace`'s existing
   * `onProjectUpdated` bubble-up from the Settings module). */
  onAutopilotChange?: (enabled: boolean) => void;
}

export function WorkspaceTopBar({
  projectName,
  onBack,
  canWrite = true,
  slate,
  workflow,
  onRequestAdvance,
  onJumpToNode,
  projectId,
  autopilotEnabled,
  onAutopilotChange,
}: WorkspaceTopBarProps) {
  const { t } = useTranslation();
  const toast = useOptionalToast();
  // Optimistic local mirror of the prop — toggled immediately on click, then
  // reverted if the PATCH fails. Re-synced whenever the caller's own value
  // changes underneath us (e.g. navigating to a different project).
  const [autopilot, setAutopilot] = useState(autopilotEnabled ?? true);
  const [autopilotSaving, setAutopilotSaving] = useState(false);
  useEffect(() => {
    setAutopilot(autopilotEnabled ?? true);
  }, [autopilotEnabled, projectId]);

  const handleToggleAutopilot = async () => {
    if (!projectId || autopilotSaving) return;
    const prev = autopilot;
    const next = !prev;
    setAutopilot(next); // optimistic
    setAutopilotSaving(true);
    try {
      const updated: Project = await updateProject(projectId, { autopilot_enabled: next });
      onAutopilotChange?.(updated.autopilot_enabled ?? next);
    } catch (err) {
      console.error('[WorkspaceTopBar] autopilot toggle failed', err);
      setAutopilot(prev); // revert
      toast?.addToast(t('projects.workflow.autopilot.toggleFailed'), 'error');
    } finally {
      setAutopilotSaving(false);
    }
  };

  // Map the workflow's nodes onto the MiniStepper's stage shape; adjacent
  // dots run the advance / back gate, non-adjacent dots navigate to the node
  // card. No workflow → hasWorkflow is false and the stepper renders nothing.
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
          className="flex items-center gap-1.5 rounded-lg bg-island-2 border border-line-strong pl-1.5 pr-2.5 py-1 shrink-0"
        >
          <span
            aria-hidden
            className="w-[22px] h-3 rounded-[3px]"
            style={{ background: 'repeating-linear-gradient(-45deg,transparent 0 4px,var(--line-strong) 4px 8px)' }}
          />
          <span className="font-mono text-[10px] font-semibold tracking-wide text-ink-300">
            EP{slate.ep}
            {slate.scene ? ` · S${slate.scene}` : ''}
          </span>
        </span>
      )}

      <div className="flex-1" />

      {/* M4 Autopilot (mig 395, task O1/O2/O3) — project-level master switch,
          same chip family as agents-active below (rounded-full soft-tint
          pill + icon + short label) but its own semantic hue: on = --agent
          (this IS the "an agent may act automatically" signal), off = a
          neutral/muted tone — never the amber/warn hue agents-active uses,
          so the two chips never read as the same kind of state. */}
      {projectId && autopilotEnabled !== undefined && (
        <button
          type="button"
          onClick={() => void handleToggleAutopilot()}
          disabled={!canWrite || autopilotSaving}
          data-testid="workspace-autopilot-chip"
          data-autopilot={autopilot ? 'on' : 'off'}
          title={t(
            autopilot ? 'projects.workflow.autopilot.onTitle' : 'projects.workflow.autopilot.offTitle',
          )}
          className={`inline-flex items-center gap-1 rounded-full px-2.5 py-1 text-[11px] font-medium whitespace-nowrap transition disabled:cursor-not-allowed disabled:opacity-60 ${
            autopilot ? 'bg-agent-soft text-agent' : 'bg-ink-800 text-ink-500'
          }`}
        >
          {autopilot ? <Zap size={12} /> : <ZapOff size={12} />}
          {t('projects.workflow.autopilot.label')}
        </button>
      )}

      {agentsActive > 0 && (
        <span
          data-testid="workspace-agents-active"
          className="inline-flex items-center gap-1 rounded-full bg-amber-500/12 px-2.5 py-1 text-[11px] font-medium text-amber-400 whitespace-nowrap"
        >
          <Bot size={12} />
          {t('projects.workflow.agentsActive', { count: agentsActive })}
        </span>
      )}

      {hasWorkflow && (
        <MiniStepper
          catalog={wfCatalog}
          currentIndex={wfCurrentIndex}
          canWrite={canWrite}
          onJump={handleWorkflowJump}
        />
      )}
    </div>
  );
}

export default WorkspaceTopBar;
