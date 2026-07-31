/**
 * StageBriefMirror — the mirror-issue-side read-only echo of a workflow
 * node's `brief` (mig 395, M4 Autopilot task O1/O2/O3, spec §3: "in_review
 * 节点 … brief 置顶展示于 Stage Board 审阅区与镜像 issue"). Mounted in
 * `IssueDetailView` right alongside `DeliverablesZone` — same "issue-side,
 * project-backed, workflow-node-mirror-only" mounting gate (`isStageMirror`),
 * just a lightweight read-only block instead of a dropzone.
 *
 * There is no node-brief field on the Issue payload itself (a mirror issue
 * only carries `origin_kind`/`origin_id`, the node's own id) — this fetches
 * the node's own Stage Board row (the same endpoint `WorkspaceStageBoard`
 * already uses) and reads `.node.brief`/`.node.status` off it. Renders
 * nothing at all outside `in_review` or with an empty brief — this is a
 * "spotlight while under review" surface, not a general-purpose brief
 * display (that's the Stage Board / CurrentNodeCard's job).
 */
import React, { useEffect, useState } from 'react';
import { useTranslation } from 'react-i18next';
import { fetchStageBoard } from '../../services/workflowService';

interface StageBriefMirrorProps {
  projectId: string;
  nodeId: string;
}

export const StageBriefMirror: React.FC<StageBriefMirrorProps> = ({ projectId, nodeId }) => {
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
        console.error('[StageBriefMirror] failed to load the node brief', err);
      });
    return () => {
      cancelled = true;
    };
  }, [projectId, nodeId]);

  if (!inReview || !brief.trim()) return null;

  return (
    <div
      data-testid="issue-stage-brief-pinned"
      className="mt-4 rounded-md border border-[var(--accent-border)] bg-[var(--accent-soft)] px-3 py-2 text-[12.5px] text-[var(--accent-text)]"
    >
      <div className="mb-1 text-[10px] uppercase tracking-wider opacity-80">
        {t('projects.workflow.brief.reviewHeading')}
      </div>
      <p className="whitespace-pre-wrap">{brief}</p>
    </div>
  );
};

export default StageBriefMirror;
