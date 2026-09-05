/**
 * Context block: everything this issue hangs off — its project and the
 * pipeline strip (the "next stop" when this issue is one step of a run).
 * (Formerly DetailLinksPanel; sub-issues moved to their own block.)
 */
import React from 'react';
import { useTranslation } from 'react-i18next';
import { Link } from 'react-router-dom';

import type { IssueBlock, IssueBlockProps } from '../issueBlocks';
import type { AgentRef } from '../types';
import { PipelineRunStrip } from './PipelineRunStrip';
import { RailCard, RailRow } from './StatusBlock';

const LinksBlockView: React.FC<IssueBlockProps> = ({ ctx }) => {
  const { t } = useTranslation();
  const project = ctx.issue.project as { id: number | string; name: string; color?: string } | undefined;
  return (
    <RailCard title={t('issueDetail.links', 'Links')} testId="detail-links-panel">
      {project && (
        <RailRow label={t('issueDetail.project', 'Project')}>
          {ctx.env.projectPath ? (
            <Link to={ctx.env.projectPath} data-testid="detail-open-project" className="inline-flex items-center gap-1.5 text-[var(--accent-text)] hover:underline">
              <span className={`w-1.5 h-1.5 rounded-full ${project.color ?? 'bg-ink-500'}`} />
              {project.name}
            </Link>
          ) : (
            project.name
          )}
        </RailRow>
      )}
      <PipelineRunStrip
        issueId={Number(ctx.issue.id)}
        agentsById={(ctx.env.agentsById ?? {}) as Record<string, AgentRef>}
        refreshKey={ctx.env.refreshKey}
      />
    </RailCard>
  );
};

export const linksBlock: IssueBlock = {
  id: 'links',
  zone: 'context',
  order: 60,
  match: () => true,
  component: LinksBlockView,
};
