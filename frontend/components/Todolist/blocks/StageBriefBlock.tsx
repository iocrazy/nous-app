/**
 * Context block: the mirror-issue-side read-only echo of a workflow node's
 * `brief` (mig 395, M4 Autopilot). Matches only a `project_stage` mirror with a
 * project and a node id; renders nothing outside `in_review` or with an empty
 * brief — a "spotlight while under review" surface, not a general brief display.
 * (Formerly StageBriefMirror.tsx; moved here as a registered block.)
 */
import React, { useEffect, useState } from 'react';
import { useTranslation } from 'react-i18next';

import { fetchStageBoard } from '../../../services/workflowService';
import type { IssueBlock, IssueBlockProps } from '../issueBlocks';

export const StageBriefMirror: React.FC<{ projectId: string; nodeId: string }> = ({ projectId, nodeId }) => {
  const { t } = useTranslation();
  const [brief, setBrief] = useState('');
  const [inReview, setInReview] = useState(false);

  useEffect(() => {
    let cancelled = false;
    setBrief('');
    setInReview(false);
    fetchStageBoard(projectId, nodeId)
      .then((data) => {
        if (cancelled) return;
        setBrief(data.node.brief ?? '');
        setInReview(data.node.status === 'in_review');
      })
      .catch((err) => {
        console.error('[StageBriefBlock] failed to load the node brief', err);
      });
    return () => {
      cancelled = true;
    };
  }, [projectId, nodeId]);

  if (!inReview || !brief.trim()) return null;

  return (
    <div
      data-testid="issue-stage-brief-pinned"
      className="rounded-md border border-[var(--accent-border)] bg-[var(--accent-soft)] px-3 py-2 text-[12.5px] text-[var(--accent-text)]"
    >
      <div className="mb-1 text-[10px] uppercase tracking-wider opacity-80">
        {t('projects.workflow.brief.reviewHeading')}
      </div>
      <p className="whitespace-pre-wrap">{brief}</p>
    </div>
  );
};

/** UiIssue keeps the server columns under `raw`; a bare Issue row has them
 * at the top level. Blocks accept either. */
function originIdOf(issue: Record<string, unknown>): string | null {
  const raw = issue.raw as Record<string, unknown> | undefined;
  const v = raw?.origin_id ?? issue.origin_id;
  return typeof v === 'string' && v ? v : null;
}

const StageBriefBlockView: React.FC<IssueBlockProps> = ({ ctx }) => {
  const project = ctx.issue.project as { id: number | string } | undefined;
  const nodeId = originIdOf(ctx.issue);
  if (!project || !nodeId) return null;
  return <StageBriefMirror projectId={String(project.id)} nodeId={nodeId} />;
};

export const stageBriefBlock: IssueBlock = {
  id: 'stage-brief',
  zone: 'context',
  order: 20,
  match: (ctx) => ctx.originKind === 'project_stage' && !!ctx.issue.project && originIdOf(ctx.issue) !== null,
  component: StageBriefBlockView,
};
