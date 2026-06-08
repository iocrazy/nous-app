import React, { useEffect, useState } from 'react';
import { CheckCircle2, Inbox, ListChecks, Upload as UploadIcon } from 'lucide-react';
import { useTranslation } from 'react-i18next';
import { useNavigate } from 'react-router-dom';
import { TaskCenterRow } from './TaskCenter/TaskCenterRow';
import { TaskDetailModal } from './TaskCenter/TaskDetailModal';
import { ActiveTaskCard } from './TaskCenter/ActiveTaskCard';
import { TaskCenterStatusBar, type TaskTab } from './TaskCenter/TaskCenterStatusBar';
import { summarizeTasks, isActiveStatus } from './TaskCenter/taskCenterSummary';
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
// TaskCenterPanel (backendTasks + agentTasks merge, summarizeTasks counts,
// active/history split, ActiveTaskCard for running + TaskCenterRow for
// queued/history + live UploadContext rows + TaskDetailModal). The only
// difference is the shell: a full-height slide-up overlay with larger touch
// targets instead of the floating dropdown panel.
// ---------------------------------------------------------------------------

export interface MobileTasksPageProps {
  isOpen: boolean;
  onClose: () => void;
}

// ---------------------------------------------------------------------------
// MobileTasksTabButton — the pill tab button for the mobile bottom bar.
//
// Lives in this file (not AppLayout) because it reads the live active-task
// count via useTaskManager for the badge — that hook is only valid INSIDE
// TaskManagerProvider, and AppLayout's function body runs outside it (the
// provider only wraps AppLayout's JSX return, where the pill is rendered).
// ---------------------------------------------------------------------------

export function MobileTasksTabButton({
  active,
  onClick,
}: {
  active: boolean;
  onClick: () => void;
}) {
  const { totalActive } = useTaskManager();
  return (
    <button
      onClick={onClick}
      className={`relative flex flex-col items-center gap-0.5 px-4 py-1.5 rounded-full transition-colors ${
        active ? 'bg-zinc-800 text-indigo-400' : 'text-zinc-500'
      }`}
    >
      <ListChecks size={20} />
      <span className="text-[9px] leading-tight font-medium">Tasks</span>
      {totalActive > 0 && (
        <span className="absolute top-0 right-2 flex items-center justify-center min-w-[16px] h-[16px] px-1 text-[9px] font-bold leading-none text-white bg-indigo-500 rounded-full">
          {totalActive > 99 ? '99+' : totalActive}
        </span>
      )}
    </button>
  );
}

export function MobileTasksPage({ isOpen, onClose }: MobileTasksPageProps) {
  const { t } = useTranslation();
  const navigate = useNavigate();
  const { tasks, cancelTask, retryTask, clearCompleted, isLoading } = useTaskManager();
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

  const counts = summarizeTasks(allTasks, uploadingItems.length);
  const completedCount = counts.completed + counts.failed;
  const activeTotal = counts.running + counts.queued;
  const hasTasks = allTasks.length > 0 || uploadingItems.length > 0;

  // Newest first; within the Active tab, running sorts above queued.
  const byRecency = (a: UnifiedTask, b: UnifiedTask) =>
    (b.created_at || '').localeCompare(a.created_at || '');
  const activeList = allTasks
    .filter((tk) => isActiveStatus(tk.status))
    .sort((a, b) => {
      const ar = a.status === 'processing' ? 1 : 0;
      const br = b.status === 'processing' ? 1 : 0;
      return ar !== br ? br - ar : byRecency(a, b);
    });
  const historyList = allTasks
    .filter((tk) => !isActiveStatus(tk.status))
    .sort(byRecency)
    .slice(0, 50);

  const runningTasks = activeList.filter((tk) => tk.status === 'processing');
  const queuedTasks = activeList.filter((tk) => tk.status === 'pending');
  const showActive = tab === 'active';

  // Shared 1s clock for the live running cards' elapsed time — only ticks while
  // the page is open AND something is actually running.
  const [now, setNow] = useState(() => Date.now());
  useEffect(() => {
    if (!isOpen || counts.running === 0) return;
    const id = window.setInterval(() => setNow(Date.now()), 1000);
    return () => window.clearInterval(id);
  }, [isOpen, counts.running]);

  if (!isOpen) return null;

  return (
    <div className="sm:hidden fixed inset-0 z-50 bg-zinc-950 flex flex-col">
      {/* Header */}
      <div className="flex items-center justify-between px-4 pt-[max(env(safe-area-inset-top),12px)] pb-3 border-b border-zinc-800/80">
        <div className="w-16">
          {completedCount > 0 && (
            <button
              onClick={() => clearCompleted()}
              className="text-[12px] text-zinc-500 hover:text-zinc-300 active:text-zinc-200 transition-colors"
            >
              {t('topbar.clearCompleted')}
            </button>
          )}
        </div>
        <h1 className="text-[17px] font-semibold text-zinc-100">{t('topbar.taskCenter')}</h1>
        <button
          onClick={onClose}
          className="px-4 py-1.5 text-[15px] font-medium text-zinc-100 bg-zinc-800 rounded-full hover:bg-zinc-700 active:bg-zinc-600 transition-colors"
        >
          {t('common.done', 'Done')}
        </button>
      </div>

      {isLoading ? (
        <div className="flex flex-col items-center justify-center flex-1 text-zinc-500">
          <div className="w-6 h-6 border-2 border-zinc-600 border-t-indigo-400 rounded-full animate-spin mb-3" />
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
          <div className="flex-1 overflow-y-auto pb-[calc(env(safe-area-inset-bottom,16px)+16px)]">
            {showActive ? (
              <>
                {/* Running now — prominent live cards */}
                {runningTasks.length > 0 && (
                  <div className="px-4 pt-3 pb-1 text-[11px] font-medium uppercase tracking-wide text-zinc-500">
                    {t('topbar.running')} · {runningTasks.length}
                  </div>
                )}
                {runningTasks.map((task) => (
                  <ActiveTaskCard key={task.id} task={task} now={now} onCancel={cancelTask} />
                ))}

                {/* Active uploads from UploadContext (client-side progress) */}
                {uploadingItems.map((item) => (
                  <div key={item.id} className="px-4 py-3 border-b border-zinc-800/50">
                    <div className="flex items-center gap-3">
                      <div className="w-9 h-9 rounded-lg flex items-center justify-center shrink-0 bg-blue-500/20 text-blue-400">
                        <UploadIcon size={16} />
                      </div>
                      <div className="flex-1 min-w-0">
                        <div className="flex items-center justify-between">
                          <span className="text-sm text-zinc-300 truncate max-w-[200px]">{item.filename}</span>
                          <div className="flex items-center gap-1.5 shrink-0 ml-2">
                            <span className="text-[11px] text-zinc-500">{item.percent}%</span>
                            {item.speed > 0 && (
                              <span className="text-[11px] text-zinc-600">{uploadFormatSpeed(item.speed)}</span>
                            )}
                          </div>
                        </div>
                        <div className="flex items-center gap-2 mt-0.5">
                          <span className="text-[11px] text-zinc-600">Upload</span>
                          {item.fileSize > 0 && (
                            <span className="text-[11px] text-zinc-600">{uploadFormatFileSize(item.fileSize)}</span>
                          )}
                        </div>
                      </div>
                    </div>
                    <div className="mt-2 h-1.5 bg-zinc-800 rounded-full overflow-hidden">
                      <div
                        className="h-full rounded-full transition-all duration-300 bg-indigo-500"
                        style={{ width: `${Math.max(item.percent, 2)}%` }}
                      />
                    </div>
                  </div>
                ))}

                {/* Queued */}
                {queuedTasks.length > 0 && (
                  <div className="px-4 pt-3 pb-1 text-[11px] font-medium uppercase tracking-wide text-zinc-500 border-t border-zinc-800/50">
                    {t('topbar.queued')} · {queuedTasks.length}
                  </div>
                )}
                {queuedTasks.map((task) => (
                  <TaskCenterRow
                    key={task.id}
                    task={task}
                    onCancel={cancelTask}
                    onRetry={retryTask}
                    onOpenResource={openResource}
                    onOpenDetail={setDetailTask}
                  />
                ))}

                {uploadingItems.length === 0 && activeList.length === 0 && (
                  <div className="flex flex-col items-center justify-center py-16 text-zinc-500">
                    <CheckCircle2 size={32} className="mb-3 text-zinc-600" />
                    <span className="text-sm">{t('topbar.noActiveTasks')}</span>
                  </div>
                )}
              </>
            ) : (
              <>
                {historyList.map((task) => (
                  <TaskCenterRow
                    key={task.id}
                    task={task}
                    onCancel={cancelTask}
                    onRetry={retryTask}
                    onOpenResource={openResource}
                    onOpenDetail={setDetailTask}
                  />
                ))}
                {historyList.length === 0 && (
                  <div className="flex flex-col items-center justify-center py-16 text-zinc-500">
                    <Inbox size={32} className="mb-3 text-zinc-600" />
                    <span className="text-sm">{t('topbar.noItems')}</span>
                  </div>
                )}
              </>
            )}
          </div>
        </>
      ) : (
        <div className="flex flex-col items-center justify-center flex-1 text-zinc-500">
          <Inbox size={36} className="mb-3 text-zinc-600" />
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
