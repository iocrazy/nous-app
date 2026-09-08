/**
 * TaskCenter — the Linear-style task LOG / history view.
 *
 * Reusable component embedding the full toolbar + list/kanban + detail
 * drawer pipeline. Rendered by Settings → Tasks (per-user log) and may
 * be reused elsewhere (e.g., a dedicated /tasks-log route in the future).
 *
 * For "task DISPATCH + conversation" UX (paperclip-style), see TodolistPage
 * (still pending its rewrite as of A7).
 */

import React, { useEffect, useMemo, useState } from 'react';
import { ListTodo, WifiOff } from 'lucide-react';
import { useTaskManager, type UnifiedTask } from '../../contexts/TaskManagerContext';
import {
  groupTasks,
  type GroupBy,
} from '../../utils/taskDisplay';
import { TaskToolbar, type ViewMode } from './TaskToolbar';
import { TaskListView } from './TaskListView';
import { TaskKanbanView } from './TaskKanbanView';
import { BatchActionBar } from './BatchActionBar';
import { TaskPagination } from './TaskPagination';
import { NeedsInputSection } from './NeedsInputSection';
import { PausedSection } from './PausedSection';
import { useTaskPage } from './useTaskPage';
import { postIssueMessage, AgentNotDispatchedError } from '../../services/issueMessageService';
import { useToast } from '../Toast';
import { useTranslation } from 'react-i18next';
import {
  isTerminal,
  toggleSelection,
  addAll,
  partitionForRetry,
  nextFocusId,
} from '../../utils/taskSelection';
import { runBatch } from '../../utils/batchRunner';

interface TaskCenterProps {
  /** When true, the component fills its parent container without breaking
   * out of page padding (suitable for Settings modal embedding). When
   * false (default), uses the edge-to-edge full-height layout. */
  embedded?: boolean;
}

export const TaskCenter: React.FC<TaskCenterProps> = ({ embedded = false }) => {
  const {
    revision, refreshTasks, isConnected, isWsConnected, retryTask, deleteTask,
    needsInputItems, refreshNeedsInput,
  } = useTaskManager();
  const { addToast } = useToast();
  const { t } = useTranslation();

  // Reuses the existing issue-reply endpoint (issueMessageService.postIssueMessage)
  // rather than adding a parallel one — posting the reply flips the issue's
  // needs_followup status server-side, and refreshNeedsInput() re-pulls the
  // list so the row disappears once it's no longer waiting on an answer.
  //
  // Silent-no-op fix (final review, finding 5): the POST can succeed (the
  // message is saved) while starting no agent turn at all — the legacy
  // no-assignee path and the /note-suppressed path both do this, and
  // `agent_run` in the response is always null on every path (can't be used
  // to tell them apart). Without checking `agent_dispatched`, the card would
  // sit "pending" forever waiting for a status flip that will never come,
  // with zero feedback. Throwing here routes it into NeedsInputSection's
  // existing catch, which it distinguishes from a network failure.
  const handleAnswerNeedsInput = async (issueId: string, text: string, answerTo?: string) => {
    const res = await postIssueMessage(
      Number(issueId),
      answerTo ? { body: text, answer_to: answerTo } : { body: text },
    );
    if (!res.agent_dispatched) {
      throw new AgentNotDispatchedError();
    }
    await refreshNeedsInput();
  };

  // Settings → Tasks list: server-side page-number pagination. Owns
  // page / multi-select filters / sort / search; `tasks` below is the
  // CURRENT PAGE only (grouping + selection run over the page).
  const taskPage = useTaskPage(revision);
  const tasks = taskPage.tasks;

  // Default 'flow': one user submission (parse → download → followups)
  // reads as one group — matching the floating panel's flow cards. Users
  // can switch back to the flat list via the toolbar's Layers picker.
  const [groupBy, setGroupBy] = useState<GroupBy>('flow');
  const [viewMode, setViewMode] = useState<ViewMode>('list');
  // Set of task ids whose inline detail panel is open. Multi-expand
  // intentionally — admin Arco's NotionTable behaves the same way.
  const [expandedIds, setExpandedIds] = useState<Set<string>>(new Set());
  const [refreshing, setRefreshing] = useState(false);

  // ─── Multi-select / batch actions ────────────────────
  const [selectedIds, setSelectedIds] = useState<Set<string>>(new Set());
  const [batchBusy, setBatchBusy] = useState(false);
  const [batchProgress, setBatchProgress] = useState<{ done: number; total: number } | null>(null);

  // "Select all N matching" (cross-page) flips this on; any manual selection
  // change flips it off so the bar reflects reality.
  const [allMatching, setAllMatching] = useState(false);
  const toggleSelect = (id: string) => {
    setSelectedIds((prev) => toggleSelection(prev, id));
    setAllMatching(false);
  };
  const clearSelection = () => {
    setSelectedIds(new Set());
    setAllMatching(false);
  };

  // Keyboard roving focus: click anywhere in the list to engage, then
  // ↑/↓ move the focused row, Space toggles its selection, Enter expands.
  const [focusedId, setFocusedId] = useState<string | null>(null);

  const toggleExpand = (task: UnifiedTask) => {
    setExpandedIds((prev) => {
      const next = new Set(prev);
      if (next.has(task.id)) next.delete(task.id);
      else next.add(task.id);
      return next;
    });
  };

  // ─── Stale connection banner ──────────────────────────
  // Suppress flicker during initial mount: wait 3s after !isLoading before
  // surfacing a disconnection. SUBSCRIBED + WS open arrive within ~1s of
  // first paint in healthy cases; if either is still down at 3s, the user
  // is genuinely on stale data.
  const isAnyConnectionDown = !isConnected || !isWsConnected;
  const [showStaleBanner, setShowStaleBanner] = useState(false);
  useEffect(() => {
    if (taskPage.loading || !isAnyConnectionDown) {
      setShowStaleBanner(false);
      return;
    }
    const timer = setTimeout(() => setShowStaleBanner(true), 3000);
    return () => clearTimeout(timer);
  }, [taskPage.loading, isAnyConnectionDown]);

  // Group the CURRENT PAGE client-side (filter/sort already ran server-side).
  const groups = useMemo(() => groupTasks(tasks, groupBy), [tasks, groupBy]);

  // Partition the live selection against the current task list (drops ids
  // that vanished, and splits the retryable subset for the Retry button).
  const retryPartition = useMemo(
    () => partitionForRetry(tasks, selectedIds),
    [tasks, selectedIds],
  );

  const selectAllTerminal = () => {
    setSelectedIds((prev) =>
      addAll(prev, tasks.filter((t) => isTerminal(t.status)).map((t) => t.id)),
    );
    setAllMatching(false);
  };

  // Cross-page: select EVERY terminal task matching the current filter (not
  // just the visible page). Fetches ids server-side (capped at 5000).
  const handleSelectAllMatching = async () => {
    try {
      const { ids, capped } = await taskPage.fetchAllMatchingIds();
      setSelectedIds(new Set(ids));
      setAllMatching(true);
      if (capped) {
        addToast(t('taskCenter.batch.selectCapped', { count: ids.length }), 'info');
      }
    } catch (e) {
      console.error('[TaskCenter] select-all-matching failed:', e);
      addToast(t('taskCenter.batch.selectAllFailed'), 'error');
    }
  };

  // On-screen row order (respects grouping); drives ↑/↓ keyboard nav.
  const orderedIds = useMemo(
    () => groups.flatMap((g) => g.tasks.map((t) => t.id)),
    [groups],
  );
  const taskById = useMemo(
    () => new Map(tasks.map((t) => [t.id, t])),
    [tasks],
  );

  const handleListKeyDown = (e: React.KeyboardEvent) => {
    if (e.key === 'ArrowDown' || e.key === 'ArrowUp') {
      if (orderedIds.length === 0) return;
      e.preventDefault();
      setFocusedId((cur) => nextFocusId(orderedIds, cur, e.key === 'ArrowDown' ? 1 : -1));
    } else if (e.key === ' ' || e.key === 'Spacebar') {
      if (!focusedId) return;
      const tk = taskById.get(focusedId);
      if (tk && isTerminal(tk.status)) {
        e.preventDefault(); // stop the page from scrolling
        toggleSelect(focusedId);
      }
    } else if (e.key === 'Enter') {
      if (!focusedId) return;
      const tk = taskById.get(focusedId);
      if (tk) {
        e.preventDefault();
        toggleExpand(tk);
      }
    } else if (e.key === 'Escape') {
      // First Esc clears the selection (and focus) WITHOUT bubbling — the
      // SettingsModal listens for Escape on `document`, so stopPropagation
      // keeps the modal open. With nothing selected, let Esc bubble through
      // so the second press closes Settings as usual.
      if (selectedIds.size > 0) {
        e.stopPropagation();
        clearSelection();
        setFocusedId(null);
      }
    }
  };

  const runBatchAction = async (
    ids: string[],
    action: (id: string) => Promise<void>,
    successKey: 'retry' | 'delete',
  ) => {
    if (ids.length === 0 || batchBusy) return;
    setBatchBusy(true);
    setBatchProgress({ done: 0, total: ids.length });
    try {
      const { succeeded, failed } = await runBatch(ids, action, {
        concurrency: 4,
        onProgress: (done, total) => setBatchProgress({ done, total }),
      });
      if (failed.length === 0) {
        addToast(
          t(`taskCenter.batch.toast.${successKey}Done`, { count: succeeded.length }),
          'success',
        );
      } else {
        addToast(
          t('taskCenter.batch.toast.partial', {
            ok: succeeded.length,
            failed: failed.length,
          }),
          'error',
        );
      }
      clearSelection();
    } finally {
      setBatchBusy(false);
      setBatchProgress(null);
    }
  };

  // When all-matching is active, selectedIds spans pages (the ids endpoint
  // already returned terminal-only), so act on the raw set; otherwise use the
  // on-page partition (which knows retryable vs all for visible rows).
  const retryIds = allMatching ? [...selectedIds] : retryPartition.retryable;
  const deleteIds = allMatching ? [...selectedIds] : retryPartition.all;

  const handleBatchRetry = () => runBatchAction(retryIds, retryTask, 'retry');
  const handleBatchDelete = () => {
    if (deleteIds.length === 0) return;
    // Destructive + multi-row → confirm (mirrors the single-row delete).
    if (!window.confirm(t('taskCenter.batch.confirmDelete', { count: deleteIds.length }))) return;
    runBatchAction(deleteIds, deleteTask, 'delete');
  };

  const handleRefresh = async () => {
    setRefreshing(true);
    try {
      taskPage.refresh();
      await refreshTasks();
    } finally {
      setRefreshing(false);
    }
  };

  if (taskPage.loading && tasks.length === 0) {
    return (
      <div className="flex flex-col items-center justify-center h-64 text-ink-500">
        <ListTodo size={36} className="mb-3 text-ink-700 animate-pulse" />
        <p className="text-sm">Loading tasks…</p>
      </div>
    );
  }

  const containerClass = embedded
    ? 'flex flex-col h-full bg-ink-950 border border-ink-800/80 rounded-md overflow-hidden'
    : 'flex flex-col h-[calc(100vh-5rem)] -mx-4 sm:-mx-8 -mb-28 sm:-mb-8 bg-ink-950 border-t border-ink-800/80';

  return (
    <div className={containerClass}>
      <NeedsInputSection items={needsInputItems} onAnswer={handleAnswerNeedsInput} />
      <PausedSection />
      {showStaleBanner && (
        <button
          type="button"
          onClick={handleRefresh}
          className="flex items-center gap-2 px-4 py-2 text-xs bg-amber-500/10 text-warn border-b border-amber-500/30 hover:bg-amber-500/15 transition-colors"
          title={`Realtime: ${isConnected ? 'connected' : 'down'} · WebSocket: ${isWsConnected ? 'connected' : 'down'}. Click to refresh.`}
        >
          <WifiOff size={12} />
          <span>Live updates paused — click to refresh</span>
        </button>
      )}
      <TaskToolbar
        search={taskPage.search}
        onSearchChange={taskPage.setSearch}
        statusFilter={taskPage.statuses}
        onToggleStatus={taskPage.toggleStatus}
        typeFilter={taskPage.types}
        onToggleType={taskPage.toggleType}
        groupBy={groupBy}
        onGroupByChange={setGroupBy}
        sortBy={taskPage.sort}
        onSortByChange={taskPage.setSort}
        viewMode={viewMode}
        onViewModeChange={setViewMode}
        onRefresh={handleRefresh}
        isRefreshing={refreshing}
        totalCount={taskPage.total}
      />
      <div
        className="flex-1 overflow-y-auto outline-none"
        tabIndex={viewMode === 'list' ? 0 : undefined}
        onKeyDown={viewMode === 'list' ? handleListKeyDown : undefined}
      >
        {viewMode === 'list' ? (
          <TaskListView
            groups={groups}
            expandedIds={expandedIds}
            onToggle={toggleExpand}
            selectedIds={selectedIds}
            onToggleSelect={toggleSelect}
            focusedId={focusedId}
            onFocusRow={setFocusedId}
          />
        ) : (
          <TaskKanbanView
            groups={groups}
            selectedTaskId={null}
            onSelect={() => { /* kanban detail intentionally noop — use list view to expand */ }}
          />
        )}
      </div>
      {viewMode === 'list' && (
        <TaskPagination
          page={taskPage.page}
          totalPages={taskPage.totalPages}
          total={taskPage.total}
          pageSize={taskPage.pageSize}
          onPage={taskPage.setPage}
          onPageSize={taskPage.setPageSize}
          disabled={taskPage.loading || batchBusy}
        />
      )}
      {/* Pinned to the BOTTOM as the last flex child: selecting a row shrinks
          the scroll viewport from below instead of shoving the whole list
          down (no top-anchored layout shift on first select). */}
      {(retryPartition.all.length > 0 || (allMatching && selectedIds.size > 0)) && (
        <BatchActionBar
          selectedCount={allMatching ? selectedIds.size : retryPartition.all.length}
          retryableCount={allMatching ? selectedIds.size : retryPartition.retryable.length}
          onRetry={handleBatchRetry}
          onDelete={handleBatchDelete}
          onSelectAll={selectAllTerminal}
          onClear={clearSelection}
          busy={batchBusy}
          progress={batchProgress}
          allMatchingActive={allMatching}
          matchTotal={taskPage.total}
          onSelectAllMatching={
            !allMatching && taskPage.total > tasks.length
              ? handleSelectAllMatching
              : undefined
          }
        />
      )}
    </div>
  );
};

export default TaskCenter;
