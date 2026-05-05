/**
 * TodolistPage — paperclip-style task tracking + chat (A8 UI scaffold).
 *
 * Renders either the issue list (no :identifier param) or the issue
 * detail view (with :identifier param). Backed by mock fixtures —
 * service layer wiring lands in the next session per user direction
 * "先做 ui，实现 ui 基础上，我们再继续".
 */

import React, { useMemo, useState } from 'react';
import { useParams, useNavigate } from 'react-router-dom';
import { ListTodo } from 'lucide-react';
import { IssueListView } from '../components/Todolist/IssueListView';
import { IssueDetailView } from '../components/Todolist/IssueDetailView';
import { NewIssueDialog } from '../components/Todolist/NewIssueDialog';
import { MOCK_ISSUES, findIssueByIdentifier, getMessages } from '../components/Todolist/fixtures';
import { useToast } from '../components/Toast';

export function TodolistPage() {
  const { identifier, teamId } = useParams<{ identifier?: string; teamId: string }>();
  const navigate = useNavigate();
  const [newIssueOpen, setNewIssueOpen] = useState(false);
  const { addToast } = useToast();

  const issue = useMemo(() => identifier ? findIssueByIdentifier(identifier) : null, [identifier]);
  const messages = useMemo(() => issue ? getMessages(issue.id) : [], [issue]);

  if (identifier && !issue) {
    return (
      <div className="flex flex-col items-center justify-center h-64 text-zinc-500">
        <ListTodo size={36} className="mb-3 text-zinc-700" />
        <p className="text-sm">Issue {identifier} not found.</p>
        <button
          onClick={() => navigate(`/team/${teamId}/todolist`)}
          className="mt-3 text-xs text-indigo-400 hover:text-indigo-300"
        >
          ← Back to all issues
        </button>
      </div>
    );
  }

  return (
    <>
      {issue
        ? <IssueDetailView issue={issue} messages={messages} />
        : (
          <IssueListView
            issues={MOCK_ISSUES}
            onNewIssue={() => setNewIssueOpen(true)}
          />
        )}
      {newIssueOpen && (
        <NewIssueDialog
          onClose={() => setNewIssueOpen(false)}
          onSubmit={(form) => {
            addToast(`(mock) Issue created: "${form.title}"`, 'success');
            setNewIssueOpen(false);
          }}
        />
      )}
    </>
  );
}

export default TodolistPage;
