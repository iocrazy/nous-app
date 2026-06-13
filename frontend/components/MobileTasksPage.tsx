import React, { useEffect, useState } from 'react';
import { CheckCircle2, Inbox, Upload as UploadIcon } from 'lucide-react';
import { useTranslation } from 'react-i18next';
import { useNavigate } from 'react-router-dom';
import { TaskDetailModal } from './TaskCenter/TaskDetailModal';
import { TaskCenterStatusBar, type TaskTab } from './TaskCenter/TaskCenterStatusBar';
import { FlowTaskList } from './TaskCenter/FlowStepCard';
import {
  groupTasksByFlow,
  isActiveItem,
  summarizeFlowItems,
  type PanelItem,
} from './TaskCenter/flowGrouping';
import { useAgentRunTasks } from './TaskCenter/useAgentRunTasks';
import {
  useUpload,
  formatSpeed as uploadFormatSpeed,
  formatFileSize as uploadFormatFileSize,
} from '../contexts/UploadContext';
import { useTaskManager, type UnifiedTask } from '../contexts/TaskManagerContext';

// ---------------------------------------------------------------------------
// MobileTasksPage — full-screen Task Center for mobile.
//
// Reuses the exact same building blocks + assembly logic as TopBar's desktop
// TaskCenterPanel (backendTasks + agentTasks merge, flow grouping via
// groupTasksByFlow + FlowTaskList, flow-unit counts, live UploadContext
// rows + TaskDetailModal). The only
// difference is the shell: a full-height slide-up overlay with larger touch
// targets instead of the floating dropdown panel.
// ---------------------------------------------------------------------------

export interface MobileTasksPageProps {
  isOpen: boolean;
  onClose: () => void;
  /** Feed the inner scroll position to the collapsible bottom tab bar. */
  onContentScroll?: (el: HTMLElement) => void;
}

// Always-mounted outer wrapper. Bails out BEFORE the heavy data hooks
// (useAgentRunTasks opens a Supabase Realtime channel + fetch), so a closed
// overlay holds zero subscriptions — the inner panel only mounts while open,
// mirroring the desktop TaskCenterPanel's open/close lifecycle.
export function MobileTasksPage({ isOpen, onClose, onContentScroll }: MobileTasksPageProps) {
  if (!isOpen) return null;
  return <MobileTasksPanel onClose={onClose} onContentScroll={onContentScroll} />;
}

function MobileTasksPanel({
  onClose,
  onContentScroll,
}: {
  onClose: () => void;
  onContentScroll?: (el: HTMLElement) => void;
}) {
  const { t } = useTranslation();
  const navigate = useNavigate();
  const { tasks, cancelTask, retryTask, isLoading } = useTaskManager();
  const upload = useUpload();
  const [tab, setTab] = useState<TaskTab>('active');
  const [detailTask, setDetailTask] = useState<UnifiedTask | null>(null);

  // Open a produced resource's detail page; RedirectToTeam injects the team.
  const openResource = (resourceId: string) => {
    onClose();
    navigate(`/resources/file/${resourceId}`);
  };

  const uploadingItems = upload.items.filter((i) => i.status === 'uploading');

  // Agent runs (chat / issue / scheduled) come from agent_runs, not
  // task_tracking — merged in so this page shows ALL execution. Backend
  // task_tracking excludes active uploads (UploadContext renders those live, so
  // counting them here too would double up).
  const agentTasks = useAgentRunTasks();
  const backendTasks = tasks.filter(
    (tk) => !(tk.task_type === 'upload' && (tk.status === 'pending' || tk.status === 'processing')),
  );
  const allTasks = [...backendTasks, ...agentTasks];

  // One user submission (parse → download → followups) shares a flow_id and
  // renders as ONE card with step circles — counts are in flow units. Same
  // grouping as TopBar's desktop panel (FlowTaskList).
  const panelItems = groupTasksByFlow(allTasks);
  const flowCounts = summarizeFlowItems(panelItems);
  const counts = { ...flowCounts, running: flowCounts.running + uploadingItems.length };
  const completedCount = counts.completed + counts.failed;
  const activeTotal = counts.running + counts.queued;
  const hasTasks = allTasks.length > 0 || uploadingItems.length > 0;

  // Active tab: items with something processing sort above purely-queued.
  const hasProcessing = (i: PanelItem) =>
    i.kind === 'flow'
      ? i.steps.some((s) => s.status === 'processing')
      : i.task.status === 'processing';
  const activeItems = panelItems
    .filter(isActiveItem)
    .sort((a, b) => Number(hasProcessing(b)) - Number(hasProcessing(a)));
  const historyItems = panelItems.filter((i) => !isActiveItem(i)).slice(0, 50);
  const showActive = tab === 'active';

  // Shared 1s clock for the live running cards' elapsed time — only ticks while
  // something is actually running (the panel only mounts while open).
  const [now, setNow] = useState(() => Date.now());
  useEffect(() => {
    if (counts.running === 0) return;
    const id = window.setInterval(() => setNow(Date.now()), 1000);
    return () => window.clearInterval(id);
  }, [counts.running]);

  return (
    // z-[48]: covers the underlying page's floating controls (z-30/z-40) but
    // sits BELOW the bottom tab bar (z-[49]) so the pill stays visible &
    // tappable here, and below modals/Profile (z-50). TaskDetailModal portals
    // at z-60, above everything.
    <div className="sm:hidden fixed inset-0 z-[48] bg-ink-950 flex flex-col">
      {/* Header — leave the page via the bottom tab bar. Clear button deferred. */}
      <div className="flex items-center justify-center px-4 pt-[max(env(safe-area-inset-top),12px)] pb-3 border-b border-ink-800/80">
        <h1 className="text-[17px] font-semibold text-ink-100">{t('topbar.taskCenter')}</h1>
      </div>

      {isLoading ? (
        <div className="flex flex-col items-center justify-center flex-1 text-ink-500">
          <div className="w-6 h-6 border-2 border-ink-600 border-t-indigo-400 rounded-full animate-spin mb-3" />
          <span className="text-sm">{t('common.loading')}</span>
        </div>
      ) : hasTasks ? (
        <>
          <TaskCenterStatusBar
            counts={counts}
            tab={tab}
            onTab={setTab}
            activeTotal={activeTotal}
            historyTotal={completedCount}
          />

          {/* Task list — fills remaining height, scrolls */}
          <div
            onScroll={(e) => onContentScroll?.(e.currentTarget)}
            className="flex-1 overflow-y-auto pb-[calc(env(safe-area-inset-bottom,16px)+96px)]"
          >
            {showActive ? (
              <>
                {/* Active uploads from UploadContext (client-side progress) */}
                {uploadingItems.map((item) => (
                  <div key={item.id} className="px-4 py-3 border-b border-ink-800/50">
                    <div className="flex items-center gap-3">
                      <div className="w-9 h-9 rounded-lg flex items-center justify-center shrink-0 bg-blue-500/20 text-blue-400">
                        <UploadIcon size={16} />
                      </div>
                      <div className="flex-1 min-w-0">
                        <div className="flex items-center justify-between">
                          <span className="text-sm text-ink-300 truncate max-w-[200px]">{item.filename}</span>
                          <div className="flex items-center gap-1.5 shrink-0 ml-2">
                            <span className="text-[11px] text-ink-500">{item.percent}%</span>
                            {item.speed > 0 && (
                              <span className="text-[11px] text-ink-600">{uploadFormatSpeed(item.speed)}</span>
                            )}
                          </div>
                        </div>
                        <div className="flex items-center gap-2 mt-0.5">
                          <span className="text-[11px] text-ink-600">Upload</span>
                          {item.fileSize > 0 && (
                            <span className="text-[11px] text-ink-600">{uploadFormatFileSize(item.fileSize)}</span>
                          )}
                        </div>
                      </div>
                    </div>
                    <div className="mt-2 h-1.5 bg-ink-800 rounded-full overflow-hidden">
                      <div
                        className="h-full rounded-full transition-all duration-300 bg-indigo-500"
                        style={{ width: `${Math.max(item.percent, 2)}%` }}
                      />
                    </div>
                  </div>
                ))}

                {/* Flow cards (step circles) + standalone tasks */}
                <FlowTaskList
                  items={activeItems}
                  now={now}
                  onCancel={cancelTask}
                  onRetry={retryTask}
                  onOpenResource={openResource}
                  onOpenDetail={setDetailTask}
                />

                {uploadingItems.length === 0 && activeItems.length === 0 && (
                  <div className="flex flex-col items-center justify-center py-16 text-ink-500">
                    <CheckCircle2 size={32} className="mb-3 text-ink-600" />
                    <span className="text-sm">{t('topbar.noActiveTasks')}</span>
                  </div>
                )}
              </>
            ) : (
              <>
                <FlowTaskList
                  items={historyItems}
                  now={now}
                  onCancel={cancelTask}
                  onRetry={retryTask}
                  onOpenResource={openResource}
                  onOpenDetail={setDetailTask}
                />
                {historyItems.length === 0 && (
                  <div className="flex flex-col items-center justify-center py-16 text-ink-500">
                    <Inbox size={32} className="mb-3 text-ink-600" />
                    <span className="text-sm">{t('topbar.noItems')}</span>
                  </div>
                )}
              </>
            )}
          </div>
        </>
      ) : (
        <div className="flex flex-col items-center justify-center flex-1 text-ink-500">
          <Inbox size={36} className="mb-3 text-ink-600" />
          <span className="text-sm">{t('topbar.noItems')}</span>
        </div>
      )}

      {/* Task detail modal (portal — renders above this overlay) */}
      <TaskDetailModal
        task={detailTask}
        onClose={() => setDetailTask(null)}
        onOpenResource={openResource}
      />
    </div>
  );
}
