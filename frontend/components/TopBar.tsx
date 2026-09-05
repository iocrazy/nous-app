import React, { useState, useRef, useEffect } from 'react';
import {
  Search,
  ListTodo,
  Bell,
  LogOut,
  User,
  Settings,
  HelpCircle,
  Inbox,
  CheckCircle2,
  ShieldAlert,
  Upload as UploadIcon,
  Download as DownloadIcon,
  Sparkles,
} from 'lucide-react';
import { useTranslation } from 'react-i18next';
import { useNavigate } from 'react-router-dom';
import { ApprovalsPanel } from './ApprovalsPanel';
import { TaskDetailModal } from './TaskCenter/TaskDetailModal';
import { TaskCenterStatusBar, type TaskTab } from './TaskCenter/TaskCenterStatusBar';
import { FlowTaskList } from './TaskCenter/FlowStepCard';
import {
  groupTasksByFlow,
  isActiveItem,
  summarizeFlowItems,
  type PanelItem, countActiveFlowUnits } from './TaskCenter/flowGrouping';
import { useAgentRunTasks } from './TaskCenter/useAgentRunTasks';
import { aiLibraryService } from '../services/aiLibraryService';
import { LanguageSwitcher } from './LanguageSwitcher';
import { useUpload, formatSpeed as uploadFormatSpeed, formatFileSize as uploadFormatFileSize } from '../contexts/UploadContext';
import { useExportTasks } from '../contexts/ExportTaskContext';
import { useTaskManager, type UnifiedTask } from '../contexts/TaskManagerContext';
import { useInbox } from '../contexts/InboxContext';
import { InboxPanel } from './notifications/InboxPanel';

// ---------------------------------------------------------------------------
// Types
// ---------------------------------------------------------------------------

interface TopBarProps {
  user: { name: string; email: string; avatarUrl?: string } | null;
  onNavigate?: (view: string, tab?: string) => void;
  onSignOut: () => void;
  onOpenSettings?: (tab?: string) => void;
  sidebarCollapsed?: boolean;
}

type PanelType = 'taskCenter' | 'notifications' | 'approvals' | 'avatar' | null;

// ---------------------------------------------------------------------------
// Hook: click-outside + Escape to close
// ---------------------------------------------------------------------------

function useCloseOnOutsideOrEscape(
  ref: React.RefObject<HTMLElement | null>,
  isOpen: boolean,
  onClose: () => void,
) {
  useEffect(() => {
    if (!isOpen) return;

    const handleClickOutside = (e: MouseEvent) => {
      if (ref.current && !ref.current.contains(e.target as Node)) {
        onClose();
      }
    };
    const handleEsc = (e: KeyboardEvent) => {
      if (e.key === 'Escape') onClose();
    };

    document.addEventListener('mousedown', handleClickOutside);
    document.addEventListener('keydown', handleEsc);
    return () => {
      document.removeEventListener('mousedown', handleClickOutside);
      document.removeEventListener('keydown', handleEsc);
    };
  }, [isOpen, ref, onClose]);
}

// ---------------------------------------------------------------------------
// Icon button wrapper
// ---------------------------------------------------------------------------

const IconButton: React.FC<{
  title: string;
  onClick: () => void;
  active?: boolean;
  children: React.ReactNode;
  badge?: number;
}> = ({ title, onClick, active, children, badge }) => (
  <button
    title={title}
    onClick={onClick}
    className={`relative p-2 rounded-lg transition-colors ${
      active
        ? 'bg-[var(--accent-soft)] text-[var(--accent-text)]'
        : 'text-content-2 hover:bg-island-2 hover:text-content-2'
    }`}
  >
    {children}
    {badge != null && badge > 0 && (
      <span className="absolute -top-0.5 -right-0.5 flex items-center justify-center min-w-[18px] h-[18px] px-1 text-[10px] font-bold leading-none text-white bg-red-500 rounded-full">
        {badge > 99 ? '99+' : badge}
      </span>
    )}
  </button>
);

// ---------------------------------------------------------------------------
// Panel shell (shared floating panel wrapper)
// ---------------------------------------------------------------------------

const PanelShell: React.FC<{
  children: React.ReactNode;
  className?: string;
}> = ({ children, className = '' }) => (
  <div
    className={`fixed sm:absolute right-2 sm:right-0 top-14 sm:top-full sm:mt-2 bg-card border border-line rounded-xl shadow-2xl z-50 ${className}`}
  >
    {children}
  </div>
);

// ---------------------------------------------------------------------------
// TaskCenter panel
// ---------------------------------------------------------------------------

const TaskCenterPanel: React.FC<{
  onClose: () => void;
  onOpenDetail: (task: UnifiedTask) => void;
}> = ({ onClose, onOpenDetail }) => {
  const { t } = useTranslation();
  const navigate = useNavigate();
  const { tasks, cancelTask, retryTask, clearCompleted, isLoading } = useTaskManager();
  const upload = useUpload();
  const exportTasks = useExportTasks();
  const [tab, setTab] = useState<TaskTab>('active');

  // Open a produced resource's detail page; RedirectToTeam injects the team.
  const openResource = (resourceId: string) => {
    onClose();
    navigate(`/resources/file/${resourceId}`);
  };
  const uploadingItems = upload.items.filter(i => i.status === 'uploading');
  // Client-side download/zip-export tasks (see ExportTaskContext). Shown in the
  // panel like uploads; running ones count toward "running".
  const exportItems = exportTasks.items;
  const exportRunning = exportItems.filter((i) => i.status === 'running');

  // Agent runs (chat / issue / scheduled) come from agent_runs, not
  // task_tracking — merged in here so the Task Center is the one place to see
  // ALL execution. Backend task_tracking excludes active uploads (UploadContext
  // renders those live, so counting them here too would double up).
  const agentTasks = useAgentRunTasks();
  const backendTasks = tasks.filter(
    (tk) => !(tk.task_type === 'upload' && (tk.status === 'pending' || tk.status === 'processing')),
  );
  const allTasks = [...backendTasks, ...agentTasks];

  // One user submission (parse → download → thumbnail/extract_audio/
  // transcode/ai_*) shares a flow_id and renders as ONE card with step
  // circles. Counts are in flow units — what the user calls "a task".
  const panelItems = groupTasksByFlow(allTasks);
  const flowCounts = summarizeFlowItems(panelItems);
  const counts = {
    ...flowCounts,
    running: flowCounts.running + uploadingItems.length + exportRunning.length,
  };
  const completedCount = counts.completed + counts.failed;
  const activeTotal = counts.running + counts.queued;
  const hasTasks =
    allTasks.length > 0 || uploadingItems.length > 0 || exportItems.length > 0;

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
  // something is actually running (and the panel is open, since it only mounts then).
  const [now, setNow] = useState(() => Date.now());
  useEffect(() => {
    if (counts.running === 0) return;
    const id = window.setInterval(() => setNow(Date.now()), 1000);
    return () => window.clearInterval(id);
  }, [counts.running]);

  return (
    <PanelShell className="w-[calc(100vw-2rem)] sm:w-96 right-0 sm:right-0">
      {/* Header */}
      <div className={`flex items-center justify-between px-4 py-3 border-b border-line`}>
        <span className={`text-sm font-semibold text-content-2`}>{t('topbar.taskCenter')}</span>
        {completedCount > 0 && (
          <button
            onClick={() => clearCompleted()}
            className={`text-[10px] text-content-3 hover:text-content-2 transition-colors`}
          >
            {t('topbar.clearCompleted')}
          </button>
        )}
      </div>

      {isLoading ? (
        <div className={`flex flex-col items-center justify-center py-10 text-content-3`}>
          <div className={`w-5 h-5 border-2 border-line-strong border-t-indigo-400 rounded-full animate-spin mb-2`} />
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

          {/* Task list */}
          <div className="max-h-80 overflow-y-auto">
            {showActive ? (
              <>
                {/* Active uploads from UploadContext (client-side progress).
                    Bulk imports (bulkSummary non-null): render one aggregate row.
                    Small / legacy uploads (bulkSummary null): render per-file rows. */}
                {upload.bulkSummary ? (
                  <div className={`px-3 py-2.5 border-b border-line`}>
                    <div className="flex items-center gap-2.5">
                      <div className="w-8 h-8 rounded-lg flex items-center justify-center shrink-0 text-sm bg-blue-500/20 text-blue-400">
                        <UploadIcon size={14} />
                      </div>
                      <div className="flex-1 min-w-0">
                        <div className="flex items-center justify-between">
                          <span className={`text-xs font-medium text-content-2 truncate`}>
                            {t('resources.bulkImporting', {
                              done: upload.bulkSummary.done,
                              total: upload.bulkSummary.total,
                            })}
                          </span>
                          <span className={`text-[10px] shrink-0 ml-2 text-content-3`}>
                            {upload.overallProgress}%
                          </span>
                        </div>
                        <div className="flex items-center gap-2 mt-0.5">
                          <span className={`text-[10px] text-content-4 capitalize`}>
                            {upload.bulkSummary.phase}
                          </span>
                          {upload.bulkSummary.linked > 0 && (
                            <span className={`text-[10px] text-content-4`}>
                              {t('resources.bulkLinked', { count: upload.bulkSummary.linked })}
                            </span>
                          )}
                          {upload.bulkSummary.failed > 0 && (
                            <span className="text-[10px] text-red-400">
                              {t('resources.bulkFailed', { count: upload.bulkSummary.failed })}
                            </span>
                          )}
                        </div>
                      </div>
                    </div>
                    <div className={`mt-1.5 h-1 bg-island-2 rounded-full overflow-hidden`}>
                      <div
                        className="h-full rounded-full transition-all duration-300 bg-indigo-500"
                        style={{ width: `${Math.max(upload.overallProgress, 2)}%` }}
                      />
                    </div>
                  </div>
                ) : (
                  uploadingItems.map((item) => (
                    <div key={item.id} className={`px-3 py-2.5 border-b border-line`}>
                      <div className="flex items-center gap-2.5">
                        <div className="w-8 h-8 rounded-lg flex items-center justify-center shrink-0 text-sm bg-blue-500/20 text-blue-400">
                          <UploadIcon size={14} />
                        </div>
                        <div className="flex-1 min-w-0">
                          <div className="flex items-center justify-between">
                            <span className={`text-xs text-content-2 truncate max-w-[180px]`}>{item.filename}</span>
                            <div className="flex items-center gap-1.5 shrink-0 ml-2">
                              <span className={`text-[10px] text-content-3`}>{item.percent}%</span>
                              {item.speed > 0 && (
                                <span className={`text-[10px] text-content-4`}>{uploadFormatSpeed(item.speed)}</span>
                              )}
                            </div>
                          </div>
                          <div className="flex items-center gap-2 mt-0.5">
                            <span className={`text-[10px] text-content-4`}>Upload</span>
                            {item.fileSize > 0 && (
                              <span className={`text-[10px] text-content-4`}>{uploadFormatFileSize(item.fileSize)}</span>
                            )}
                          </div>
                        </div>
                      </div>
                      <div className={`mt-1.5 h-1 bg-island-2 rounded-full overflow-hidden`}>
                        <div
                          className="h-full rounded-full transition-all duration-300 bg-indigo-500"
                          style={{ width: `${Math.max(item.percent, 2)}%` }}
                        />
                      </div>
                    </div>
                  ))
                )}

                {/* Client-side zip exports (ExportTaskContext) — same row style
                    as uploads, so a bulk download shows progress + a record. */}
                {exportItems.map((item) => (
                  <div key={item.id} className={`px-3 py-2.5 border-b border-line`}>
                    <div className="flex items-center gap-2.5">
                      <div className={`w-8 h-8 rounded-lg flex items-center justify-center shrink-0 ${item.status === 'error' ? 'bg-red-500/20 text-red-400' : 'bg-amber-500/20 text-amber-500'}`}>
                        <DownloadIcon size={14} />
                      </div>
                      <div className="flex-1 min-w-0">
                        <div className="flex items-center justify-between">
                          <span className={`text-xs text-content-2 truncate max-w-[180px]`}>{item.label}</span>
                          <span className={`text-[10px] shrink-0 ml-2 ${item.status === 'error' ? 'text-red-400' : 'text-content-3'}`}>
                            {item.status === 'running' ? `${item.percent}%` : item.status === 'complete' ? 'Done' : 'Failed'}
                          </span>
                        </div>
                        <div className="flex items-center gap-2 mt-0.5">
                          <span className={`text-[10px] text-content-4`}>Zip export</span>
                          <span className={`text-[10px] text-content-4`}>
                            {item.done}/{item.total}{item.failed > 0 ? ` · ${item.failed} failed` : ''}
                          </span>
                        </div>
                      </div>
                    </div>
                    <div className={`mt-1.5 h-1 bg-island-2 rounded-full overflow-hidden`}>
                      <div
                        className={`h-full rounded-full transition-all duration-300 ${item.status === 'error' ? 'bg-red-500' : 'bg-amber-500'}`}
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
                  onOpenDetail={onOpenDetail}
                />

                {uploadingItems.length === 0 && exportItems.length === 0 && activeItems.length === 0 && (
                  <div className={`flex flex-col items-center justify-center py-8 text-content-3`}>
                    <CheckCircle2 size={24} className={`mb-2 text-content-4`} />
                    <span className="text-xs">{t('topbar.noActiveTasks')}</span>
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
                  onOpenDetail={onOpenDetail}
                />
                {historyItems.length === 0 && (
                  <div className={`flex flex-col items-center justify-center py-8 text-content-3`}>
                    <Inbox size={24} className={`mb-2 text-content-4`} />
                    <span className="text-xs">{t('topbar.noItems')}</span>
                  </div>
                )}
              </>
            )}
          </div>
        </>
      ) : (
        <div className={`flex flex-col items-center justify-center py-10 text-content-3`}>
          <Inbox size={28} className={`mb-2 text-content-4`} />
          <span className="text-sm">{t('topbar.noItems')}</span>
        </div>
      )}
    </PanelShell>
  );
};

// ---------------------------------------------------------------------------
// Notifications panel
// ---------------------------------------------------------------------------

const NotificationsPanel: React.FC<{ onClose?: () => void }> = ({ onClose }) => (
  <PanelShell className="w-[calc(100vw-2rem)] sm:w-80">
    <InboxPanel onClose={onClose} />
  </PanelShell>
);

// ---------------------------------------------------------------------------
// Avatar menu
// ---------------------------------------------------------------------------

const AvatarMenu: React.FC<{
  user: { name: string; email: string };
  onSignOut: () => void;
  onOpenSettings?: (tab?: string) => void;
}> = ({ user, onSignOut, onOpenSettings }) => {
  const { t } = useTranslation();
  return (
    <PanelShell className="w-56">
      {/* User info header */}
      <div className={`px-4 py-3 border-b border-line`}>
        <p className={`text-sm font-semibold text-content-2 truncate`}>{user.name}</p>
        <p className={`text-xs text-content-3 truncate`}>{user.email}</p>
      </div>

      {/* Menu items */}
      <div className="py-1">
        <MenuButton
          icon={User}
          label={t('user.profile')}
          onClick={() => onOpenSettings?.('personal')}
        />
        <MenuButton
          icon={Settings}
          label={t('nav.settings')}
          onClick={() => onOpenSettings?.('general')}
        />
      </div>

      <div className={`border-t border-line`} />

      <div className="py-1">
        <MenuButton
          icon={HelpCircle}
          label={t('topbar.helpDocs')}
          onClick={() => {
            /* placeholder */
          }}
        />
      </div>

      <div className={`border-t border-line`} />

      <div className="py-1">
        <MenuButton icon={LogOut} label={t('user.signOut')} onClick={onSignOut} danger />
      </div>
    </PanelShell>
  );
};

const MenuButton: React.FC<{
  icon: React.ElementType;
  label: string;
  onClick: () => void;
  danger?: boolean;
}> = ({ icon: Icon, label, onClick, danger }) => (
  <button
    onClick={onClick}
    className={`w-full flex items-center gap-3 px-4 py-2 text-sm transition-colors ${
      danger
        ? 'text-red-400 hover:bg-red-500/10'
        : 'text-content-2 hover:bg-island-2 hover:text-content'
    }`}
  >
    <Icon size={16} />
    <span>{label}</span>
  </button>
);

// ---------------------------------------------------------------------------
// User avatar circle
// ---------------------------------------------------------------------------

const UserAvatar: React.FC<{
  name: string;
  avatarUrl?: string;
  size?: number;
}> = ({ name, avatarUrl, size = 32 }) => {
  const initial = (name || '?').charAt(0).toUpperCase();

  if (avatarUrl) {
    return (
      <img
        src={avatarUrl}
        alt={name}
        className="rounded-full object-cover"
        style={{ width: size, height: size }}
      />
    );
  }

  return (
    <div
      className="flex items-center justify-center rounded-full bg-[var(--accent-soft)] text-[var(--accent-text)] font-semibold text-sm select-none"
      style={{ width: size, height: size }}
    >
      {initial}
    </div>
  );
};

// ---------------------------------------------------------------------------
// TopBar
// ---------------------------------------------------------------------------

export const TopBar: React.FC<TopBarProps> = ({ user, onNavigate, onSignOut, onOpenSettings, sidebarCollapsed = false }) => {
  const { t } = useTranslation();
  const [openPanel, setOpenPanel] = useState<PanelType>(null);
  const upload = useUpload();
  const { tasks, totalActive } = useTaskManager();
  const inbox = useInbox();
  const uploadingCount = upload.items.filter(i => i.status === 'uploading').length;
  // Flow units, same as the panel header (one "Generate 3 images" = 1). The
  // server's totalActive counts rows; it is kept only as the fallback for
  // the moment before the working set has loaded, when there are no rows to
  // group yet — active rows are always in the working set once it exists.
  const activeFlowUnits = tasks.length > 0 ? countActiveFlowUnits(tasks) : totalActive;
  const badgeCount = activeFlowUnits + uploadingCount;

  // Auto-open task center panel when upload starts (instant, browser-side)
  useEffect(() => {
    if (upload.isUploading && openPanel !== 'taskCenter') {
      setOpenPanel('taskCenter');
    }
  }, [upload.isUploading]); // eslint-disable-line react-hooks/exhaustive-deps

  const taskCenterRef = useRef<HTMLDivElement>(null);
  const notificationsRef = useRef<HTMLDivElement>(null);
  const approvalsRef = useRef<HTMLDivElement>(null);
  const avatarRef = useRef<HTMLDivElement>(null);

  // G1-UI: pending approvals badge. Polled every 60s, refreshed on
  // every panel open (the panel itself polls more aggressively).
  const [approvalsCount, setApprovalsCount] = useState(0);
  useEffect(() => {
    let cancelled = false;
    const fetchCount = async () => {
      try {
        const resp = await aiLibraryService.listApprovalRequests(50);
        if (!cancelled) setApprovalsCount(resp.count);
      } catch { /* badge is best-effort */ }
    };
    void fetchCount();
    const id = window.setInterval(fetchCount, 60_000);
    return () => { cancelled = true; window.clearInterval(id); };
  }, []);

  // Only one panel open at a time — toggle or switch
  const togglePanel = (panel: PanelType) => {
    setOpenPanel((prev) => (prev === panel ? null : panel));
  };

  const closeAll = () => setOpenPanel(null);

  // Typed result modal — owned here (not inside TaskCenterPanel) so the panel's
  // outside-click-close doesn't unmount the portaled modal when it's clicked.
  const navigate = useNavigate();
  const [detailTask, setDetailTask] = useState<UnifiedTask | null>(null);
  const openResourceFromModal = (resourceId: string) => {
    setDetailTask(null);
    closeAll();
    navigate(`/resources/file/${resourceId}`);
  };

  // Close handlers for each panel
  useCloseOnOutsideOrEscape(taskCenterRef, openPanel === 'taskCenter', closeAll);
  useCloseOnOutsideOrEscape(notificationsRef, openPanel === 'notifications', closeAll);
  useCloseOnOutsideOrEscape(approvalsRef, openPanel === 'approvals', closeAll);
  useCloseOnOutsideOrEscape(avatarRef, openPanel === 'avatar', closeAll);

  // Island-aware ink → surface-token overrides.
  const brandTextCls = 'text-content';
  const avatarHoverBg = 'hover:bg-island-2';

  return (
    <header
      className="relative h-12 z-50 hidden sm:flex items-center gap-1.5 sm:gap-2 px-1"
    >
      {/* Island mode: brand on the left (spec §2 "global zone"); spacer pushes
          controls to the right. */}
      <div className={`flex items-center gap-2 font-bold text-sm ${brandTextCls} pl-1 pr-2 select-none`}>
        <span className="w-6 h-6 rounded-[7px] grid place-items-center bg-gradient-to-br from-indigo-400 to-indigo-500 shadow-[0_0_16px_rgba(99,102,241,0.4)]">
          <Sparkles size={13} className="text-white" />
        </span>
        <span>Nous</span>
      </div>
      <div className="flex-1" />
      {/* Language Switcher — desktop only (mobile: accessible via Settings) */}
      <div className="hidden sm:block">
        <LanguageSwitcher />
      </div>

      {/* Search — desktop only (Phase 2+) */}
      <div className="hidden sm:block">
        <IconButton
          title={t('topbar.search')}
          onClick={() => { /* Cmd+K search — Phase 2+ */ }}
        >
          <Search size={18} />
        </IconButton>
      </div>

      {/* Task Center — hidden on mobile (accessible via MobileProfilePage) */}
      <div ref={taskCenterRef} className="relative hidden sm:block">
        <IconButton
          title={t('topbar.taskCenter')}
          onClick={() => togglePanel('taskCenter')}
          active={openPanel === 'taskCenter'}
          badge={badgeCount > 0 ? badgeCount : undefined}
        >
          <ListTodo size={18} />
        </IconButton>
        {openPanel === 'taskCenter' && (
          <TaskCenterPanel onClose={closeAll} onOpenDetail={setDetailTask} />
        )}
      </div>

      {/* Typed result detail modal (portals to body; survives panel close) */}
      <TaskDetailModal
        task={detailTask}
        onClose={() => setDetailTask(null)}
        onOpenResource={openResourceFromModal}
      />

      {/* Notifications — hidden on mobile (accessible via MobileProfilePage) */}
      <div ref={notificationsRef} className="relative hidden sm:block">
        <IconButton
          title={t('topbar.notifications')}
          onClick={() => togglePanel('notifications')}
          active={openPanel === 'notifications'}
          badge={inbox.unreadCount}
        >
          <Bell size={18} />
        </IconButton>
        {openPanel === 'notifications' && (
          <NotificationsPanel onClose={() => setOpenPanel(null)} />
        )}
      </div>

      {/* G1-UI: Approvals (agent hook gates pending user decision) */}
      <div ref={approvalsRef} className="relative hidden sm:block">
        <IconButton
          title={t('topbar.approvals', 'Approvals')}
          onClick={() => togglePanel('approvals')}
          active={openPanel === 'approvals'}
          badge={approvalsCount > 0 ? approvalsCount : undefined}
        >
          <ShieldAlert size={18} />
        </IconButton>
        {openPanel === 'approvals' && (
          <PanelShell className="w-[calc(100vw-2rem)] sm:w-96 right-0 sm:right-0">
            <ApprovalsPanel
              compact
              onCountChange={setApprovalsCount}
            />
          </PanelShell>
        )}
      </div>

      {/* Avatar Menu — desktop only, stays on far right */}
      <div ref={avatarRef} className="relative hidden sm:block">
        <button
          title={t('topbar.account')}
          onClick={() => togglePanel('avatar')}
          className={`p-1 rounded-lg transition-colors ${avatarHoverBg}`}
        >
          <UserAvatar
            name={user?.name || '?'}
            avatarUrl={user?.avatarUrl}
          />
        </button>
        {openPanel === 'avatar' && user && (
          <AvatarMenu user={user} onSignOut={onSignOut} onOpenSettings={onOpenSettings} />
        )}
      </div>
    </header>
  );
};

export default TopBar;
