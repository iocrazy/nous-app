/**
 * Activity tab — status / property timeline distinct from Chat (A8.7).
 *
 * Today: filters issue_messages to kind='system_status' so users see
 * the lifecycle of the issue (status transitions) without the comment
 * + agent_run noise. Future: property changes (assignee, priority,
 * project) should land here too once we add a property_history table.
 */

import React from 'react';
import type { IssueMessage } from '../../services/issueMessageService';
import type { IssueStatus } from '../../services/issuesService';
import { STATUS_COLOR, STATUS_LABEL, IssueStatusIcon } from './IssueStatusIcon';
import { relativeTime } from '../../utils/taskDisplay';

interface IssueActivityTabProps {
  messages: IssueMessage[];
  selfUserId?: string;
}

export const IssueActivityTab: React.FC<IssueActivityTabProps> = ({ messages, selfUserId }) => {
  const events = messages.filter((m) => m.kind === 'system_status');
  if (events.length === 0) {
    return (
      <div className="text-[14px] text-zinc-500 italic px-4 py-12 text-center">
        No status changes yet. Updating the issue's status will show a row here.
      </div>
    );
  }
  return (
    <div className="px-4 py-3 space-y-1">
      {events.map((m) => {
        const from = m.from_status as IssueStatus | undefined;
        const to = m.to_status as IssueStatus | undefined;
        const isSelf = m.author_user_id && m.author_user_id === selfUserId;
        const author = isSelf ? 'You' : m.author_user_id ? `User ${m.author_user_id.slice(0, 6)}` : 'System';
        return (
          <div key={m.id} className="flex items-center gap-2 px-2 py-1.5 text-[13px] text-zinc-400 border-l-2 border-zinc-800/80">
            <span className="font-medium text-zinc-300">{author}</span>
            <span>updated status</span>
            <span className="ml-2 inline-flex items-center gap-1.5">
              {from && (
                <span className={`${STATUS_COLOR[from]} inline-flex items-center gap-1`}>
                  <IssueStatusIcon status={from} size={11} />
                  {STATUS_LABEL[from].toLowerCase()}
                </span>
              )}
              <span className="text-zinc-600">→</span>
              {to && (
                <span className={`${STATUS_COLOR[to]} inline-flex items-center gap-1`}>
                  <IssueStatusIcon status={to} size={11} />
                  {STATUS_LABEL[to].toLowerCase()}
                </span>
              )}
            </span>
            <span className="ml-auto text-[12px] text-zinc-500">{relativeTime(m.created_at)}</span>
          </div>
        );
      })}
    </div>
  );
};
