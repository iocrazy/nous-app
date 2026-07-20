/**
 * CurrentNodeCard — the active workflow node's control panel on the workspace
 * Overview (spec §5). Owner / members through the shared OwnerPicker, a
 * planned_start/due range through NodeSchedulePicker, the deliverable line, and
 * the two flow actions: Open in Todolist (jump to the mirror issue's module)
 * and Complete stage (opens the server-computed advance gate). Every in-place
 * edit writes the instance node only (PATCH), then reloads the shared workflow.
 */

import React, { useState } from 'react';
import { ArrowLeft, ArrowRight, ExternalLink, FileCheck2 } from 'lucide-react';
import { useTranslation } from 'react-i18next';
import { updateProjectNode } from '../../services/workflowService';
import type { ProjectNodePatch, ProjectStageNode } from '../../types';
import { AgentOption, OwnerPicker, PersonOption } from './OwnerPicker';
import { NodeSchedulePicker } from './NodeSchedulePicker';
import { NODE_STATUS_CONFIG, NODE_STATUS_LABEL } from './nodeStatus';

interface CurrentNodeCardProps {
  projectId: string;
  node: ProjectStageNode;
  canWrite: boolean;
  people: PersonOption[];
  agents: AgentOption[];
  onPatched: () => void;
  onRequestAdvance: (direction: 'forward' | 'back') => void;
  onOpenTodolist: () => void;
}

const Row: React.FC<{ label: string; children: React.ReactNode }> = ({ label, children }) => (
  <div className="grid grid-cols-[5.5rem_1fr] items-start gap-3 py-1.5">
    <span className="pt-1.5 text-[11px] uppercase tracking-wider text-ink-600">{label}</span>
    <div className="min-w-0">{children}</div>
  </div>
);

export const CurrentNodeCard: React.FC<CurrentNodeCardProps> = ({
  projectId,
  node,
  canWrite,
  people,
  agents,
  onPatched,
  onRequestAdvance,
  onOpenTodolist,
}) => {
  const { t } = useTranslation();
  const [saving, setSaving] = useState(false);
  const meta = NODE_STATUS_CONFIG[node.status];

  const patch = async (body: ProjectNodePatch) => {
    setSaving(true);
    try {
      await updateProjectNode(projectId, node.id, body);
      onPatched();
    } catch (err) {
      console.error('[CurrentNodeCard] patch failed', err);
    } finally {
      setSaving(false);
    }
  };

  return (
    <div
      data-testid="workflow-current-node-card"
      data-node-id={node.id}
      className="rounded-xl border border-[var(--accent-border)] bg-island p-4"
    >
      <div className="mb-2 flex items-center gap-2">
        <span className={`h-2.5 w-2.5 rounded-full ${meta.dot}`} aria-hidden />
        <h3 className="text-sm font-semibold text-ink-100">{node.name}</h3>
        <span className={`text-[11px] font-medium ${meta.text}`}>
          {NODE_STATUS_LABEL[node.status]}
        </span>
      </div>

      <Row label={t('projects.workflow.owner')}>
        <OwnerPicker
          mode="owner"
          people={people}
          agents={agents}
          ownerUserId={node.owner_user_id}
          ownerAgentId={node.owner_agent_id}
          disabled={!canWrite || saving}
          placeholder={t('projects.workflow.unassigned')}
          onOwnerChange={(ref) =>
            void patch({
              owner_user_id: ref?.user_id ?? null,
              owner_agent_id: ref?.agent_id ?? null,
            })
          }
        />
      </Row>

      <Row label={t('projects.workflow.members')}>
        <OwnerPicker
          mode="members"
          people={people}
          agents={agents}
          members={node.members}
          disabled={!canWrite || saving}
          onMembersChange={(members) => void patch({ members })}
        />
      </Row>

      <Row label={t('projects.workflow.schedule')}>
        <NodeSchedulePicker
          start={node.planned_start}
          due={node.planned_due}
          disabled={!canWrite || saving}
          onChange={(start, dueDate) =>
            void patch({ planned_start: start, planned_due: dueDate })
          }
        />
      </Row>

      {(node.deliverable_label || node.deliverable_required) && (
        <Row label={t('projects.workflow.deliverable')}>
          <div className="flex items-center gap-2 pt-1 text-[13px] text-ink-300">
            <FileCheck2 size={14} className="shrink-0 text-ink-500" />
            <span className="truncate">{node.deliverable_label || '—'}</span>
            {node.deliverable_required && (
              <span className="rounded-full bg-amber-500/10 px-1.5 py-0.5 text-[10px] text-amber-400">
                Required
              </span>
            )}
            <span
              data-testid="workflow-node-filed-count"
              className={`ml-auto shrink-0 rounded-full px-1.5 py-0.5 text-[10px] ${
                (node.deliverable_file_count ?? 0) > 0
                  ? 'bg-emerald-500/10 text-emerald-300'
                  : 'bg-ink-800 text-ink-500'
              }`}
            >
              {t('projects.workflow.deliverables.filesFiled', {
                count: node.deliverable_file_count ?? 0,
              })}
            </span>
          </div>
        </Row>
      )}

      <div className="mt-3 flex items-center gap-2 border-t border-line pt-3">
        <button
          onClick={onOpenTodolist}
          data-testid="workflow-open-todolist"
          className="inline-flex items-center gap-1.5 rounded-md border border-line px-2.5 py-1.5 text-[12.5px] text-ink-300 transition hover:border-line-strong hover:text-ink-100"
        >
          <ExternalLink size={13} /> {t('projects.workflow.openInTodolist')}
        </button>
        {canWrite && (
          <>
            <button
              onClick={() => onRequestAdvance('back')}
              data-testid="workflow-back-btn"
              className="ml-auto inline-flex items-center gap-1.5 rounded-md px-2.5 py-1.5 text-[12.5px] text-ink-500 transition hover:bg-ink-800 hover:text-ink-200"
            >
              <ArrowLeft size={13} /> {t('projects.workflow.back')}
            </button>
            <button
              onClick={() => onRequestAdvance('forward')}
              data-testid="workflow-complete-stage"
              className="inline-flex items-center gap-1.5 rounded-md border px-3 py-1.5 text-[12.5px] transition"
              style={{
                background: 'var(--accent-soft)',
                color: 'var(--accent-text)',
                borderColor: 'var(--accent-border)',
              }}
            >
              {t('projects.workflow.completeStage')} <ArrowRight size={13} />
            </button>
          </>
        )}
      </div>
    </div>
  );
};
