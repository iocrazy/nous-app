/**
 * Context block: the project deliverables dropzone. Any project-backed issue
 * gets it; a `project_stage` mirror routes uploads into the node's stage
 * folder (stage name = title suffix after " — "). Wraps DeliverablesZone,
 * which WorkspaceStageBoard also mounts, so the component itself stays put.
 */
import React from 'react';

import { DeliverablesZone } from '../DeliverablesZone';
import type { IssueBlock, IssueBlockProps } from '../issueBlocks';

const DeliverablesBlockView: React.FC<IssueBlockProps> = ({ ctx }) => {
  const project = ctx.issue.project as { id: number | string; name: string } | undefined;
  if (!project) return null;
  const isStageMirror = ctx.originKind === 'project_stage';
  const title = String(ctx.issue.title ?? '');
  const stageName = isStageMirror ? title.split(' — ').slice(1).join(' — ') || undefined : undefined;
  return (
    <DeliverablesZone
      projectId={String(project.id)}
      issueId={Number(ctx.issue.id)}
      isStageMirror={isStageMirror}
      projectName={project.name}
      stageName={stageName}
    />
  );
};

export const deliverablesBlock: IssueBlock = {
  id: 'deliverables',
  zone: 'context',
  order: 30,
  match: (ctx) => !!ctx.issue.project,
  component: DeliverablesBlockView,
};
