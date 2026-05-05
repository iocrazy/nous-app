import { ListTodo } from 'lucide-react';

/**
 * TodolistPage — placeholder pending paperclip-style rewrite.
 *
 * Earlier (A7) this was repurposed as a Linear-style task LOG, but that's
 * the wrong shape: this page is meant for task DISPATCH + conversational
 * threads with agents (paperclip MyIssues / IssueChatThread style).
 *
 * The task log A7 produced has been moved to Settings → Tasks (per-user)
 * and is mirrored by the existing Arco-based admin page at
 * `/admin/src/pages/tasks/index.tsx` (system-wide).
 *
 * The paperclip-style rewrite will land in a follow-up.
 */
export function TodolistPage() {
  return (
    <div className="flex flex-col items-center justify-center h-64 text-zinc-500">
      <ListTodo size={48} className="mb-4 text-zinc-600" />
      <p className="text-lg font-medium text-zinc-400">Todolist</p>
      <p className="text-sm mt-1">Task dispatch + conversation — coming soon</p>
      <p className="text-xs mt-2 text-zinc-600">
        Looking for the task execution log? Settings → Tasks.
      </p>
    </div>
  );
}

export default TodolistPage;
