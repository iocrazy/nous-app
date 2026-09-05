/**
 * Context block: sub-issue completion. Prefers the rollup's count (server,
 * includes children created after the list loaded), falls back to the
 * page's SubtaskCount. Renders nothing when there are no children.
 */
import React from 'react';
import { useTranslation } from 'react-i18next';

import type { IssueBlock, IssueBlockProps } from '../issueBlocks';
import { SubtaskBar } from '../SubtaskBar';
import { RailCard } from './StatusBlock';

function countOf(ctx: IssueBlockProps['ctx']): { total: number; done: number } | null {
  const fromRollup = ctx.rollup?.sub_issues;
  if (fromRollup && fromRollup.total > 0) return { total: fromRollup.total, done: fromRollup.done };
  const fromPage = ctx.env.subtaskCount;
  return fromPage && fromPage.total > 0 ? fromPage : null;
}

const SubtasksBlockView: React.FC<IssueBlockProps> = ({ ctx }) => {
  const { t } = useTranslation();
  const count = countOf(ctx);
  if (!count) return null;
  return (
    <RailCard title={t('issueDetail.subIssues', 'Sub-issues')} testId="detail-tasks">
      <SubtaskBar count={count} size="detail" />
      <div className="text-[12px] text-ink-500 tabular-nums">
        {count.done} / {count.total} {t('issueDetail.done', 'done')}
      </div>
    </RailCard>
  );
};

export const subtasksBlock: IssueBlock = {
  id: 'subtasks',
  zone: 'context',
  order: 40,
  match: (ctx) => countOf(ctx) !== null,
  component: SubtasksBlockView,
};
