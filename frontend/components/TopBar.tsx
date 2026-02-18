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
  XCircle,
  X,
} from 'lucide-react';
import { useTranslation } from 'react-i18next';
import { LanguageSwitcher } from './LanguageSwitcher';
import { useUpload } from '../contexts/UploadContext';
import {
  useTaskManager,
  formatSpeed,
  formatFileSize,
  taskTypeIcon,
  taskTypeLabel,
  type TaskStatus,
} from '../contexts/TaskManagerContext';

// ---------------------------------------------------------------------------
// Types
// ---------------------------------------------------------------------------

interface TopBarProps {
  user: { name: string; email: string; avatarUrl?: string } | null;
  unreadCount?: number;
  onNavigate?: (view: string, tab?: string) => void;
  onSignOut: () => void;
  onOpenSettings?: (tab?: string) => void;
  sidebarCollapsed?: boolean;
}

type PanelType = 'taskCenter' | 'notifications' | 'avatar' | null;

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
        ? 'bg-zinc-800 text-zinc-100'
        : 'text-zinc-400 hover:bg-zinc-800 hover:text-zinc-200'
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
    className={`absolute right-0 top-full mt-2 bg-zinc-900 border border-zinc-700/50 rounded-xl shadow-2xl z-50 ${className}`}
  >
    {children}
  </div>
);

// ---------------------------------------------------------------------------
// TaskCenter panel
// ---------------------------------------------------------------------------

// Task type → background color for icon badge
function taskTypeBg(type: string): string {
  switch (type) {
    case 'upload':              return 'bg-blue-500/20 text-blue-400';
    case 'download':            return 'bg-purple-500/20 text-purple-400';
    case 'transcode':           return 'bg-amber-500/20 text-amber-400';
    case 'ai_pipeline':         return 'bg-cyan-500/20 text-cyan-400';
    case 'ai_extract':          return 'bg-violet-500/20 text-violet-400';
    case 'ai_transcription':    return 'bg-fuchsia-500/20 text-fuchsia-400';
    case 'ai_summary':          return 'bg-cyan-500/20 text-cyan-400';
    default:                    return 'bg-zinc-700/50 text-zinc-400';
  }
}

// Progress bar color based on status
function progressBarColor(status: TaskStatus): string {
  switch (status) {
    case 'completed':  return 'bg-emerald-500';
    case 'failed':     return 'bg-red-500';
    case 'cancelled':  return 'bg-zinc-600';
    default:           return 'bg-indigo-500';
  }
}

const TaskCenterPanel: React.FC = () => {
  const { t } = useTranslation();
  const { tasks, activeTasks, totalActive, cancelTask, clearCompleted, isLoading } = useTaskManager();
  const completedCount = tasks.filter(
    (tk) => tk.status === 'completed' || tk.status === 'failed' || tk.status === 'cancelled',
  ).length;
  const hasTasks = tasks.length > 0;

  return (
    <PanelShell className="w-96">
      {/* Header */}
      <div className="flex items-center justify-between px-4 py-3 border-b border-zinc-800">
        <span className="text-sm font-semibold text-zinc-200">{t('topbar.taskCenter')}</span>
        {totalActive > 0 && (
          <span className="px-2 py-0.5 text-[10px] font-medium bg-indigo-500/20 text-indigo-400 rounded-full">
            {totalActive} {t('topbar.active')}
          </span>
        )}
      </div>

      {isLoading ? (
        <div className="flex flex-col items-center justify-center py-10 text-zinc-500">
          <div className="w-5 h-5 border-2 border-zinc-600 border-t-indigo-400 rounded-full animate-spin mb-2" />
          <span className="text-sm">{t('common.loading')}</span>
        </div>
      ) : hasTasks ? (
        <>
          {/* Task list */}
          <div className="max-h-80 overflow-y-auto">
            {tasks.slice(0, 50).map((task) => (
              <div key={task.id} className="px-3 py-2.5 border-b border-zinc-800/50 last:border-b-0">
                <div className="flex items-center gap-2.5">
                  {/* Task type icon */}
                  <div className={`w-8 h-8 rounded-lg flex items-center justify-center shrink-0 text-sm ${taskTypeBg(task.task_type)}`}>
                    {taskTypeIcon(task.task_type)}
                  </div>
                  {/* Task info */}
                  <div className="flex-1 min-w-0">
                    <div className="flex items-center justify-between">
                      <span className="text-xs text-zinc-300 truncate max-w-[180px]">{task.title}</span>
                      <div className="flex items-center gap-1.5 shrink-0 ml-2">
                        {(task.status === 'pending' || task.status === 'processing') && (
                          <>
                            {task.progress > 0 && (
                              <span className="text-[10px] text-zinc-500">{task.progress}%</span>
                            )}
                            {task.speed != null && task.speed > 0 && (
                              <span className="text-[10px] text-zinc-600">{formatSpeed(task.speed)}</span>
                            )}
                            <button
                              onClick={() => cancelTask(task.id)}
                              className="p-0.5 rounded text-zinc-600 hover:text-red-400 transition-colors"
                              title={t('common.cancel')}
                            >
                              <X size={12} />
                            </button>
                          </>
                        )}
                        {task.status === 'completed' && (
                          <CheckCircle2 size={14} className="text-emerald-400" />
                        )}
                        {task.status === 'failed' && (
                          <XCircle size={14} className="text-red-400" />
                        )}
                        {task.status === 'cancelled' && (
                          <X size={14} className="text-zinc-500" />
                        )}
                      </div>
                    </div>
                    <div className="flex items-center gap-2 mt-0.5">
                      <span className="text-[10px] text-zinc-600">{taskTypeLabel(task.task_type)}</span>
                      {task.subtitle && (
                        <span className="text-[10px] text-zinc-600 truncate">{task.subtitle}</span>
                      )}
                      {task.total_bytes != null && task.total_bytes > 0 && (
                        <span className="text-[10px] text-zinc-600">{formatFileSize(task.total_bytes)}</span>
                      )}
                    </div>
                    {task.error_msg && (
                      <div className="text-[10px] text-red-400 mt-0.5 truncate">{task.error_msg}</div>
                    )}
                  </div>
                </div>
                {/* Progress bar for active tasks */}
                {(task.status === 'pending' || task.status === 'processing') && (
                  <div className="mt-1.5 h-1 bg-zinc-800 rounded-full overflow-hidden">
                    <div
                      className={`h-full rounded-full transition-all duration-300 ${progressBarColor(task.status)}`}
                      style={{ width: `${Math.max(task.progress, task.status === 'processing' ? 2 : 0)}%` }}
                    />
                  </div>
                )}
              </div>
            ))}
          </div>

          {/* Bottom status bar */}
          <div className="flex items-center justify-between px-3 py-2 border-t border-zinc-800 bg-zinc-900/80">
            <div className="text-[10px] text-zinc-500">
              {totalActive > 0
                ? t('topbar.activeCount', { count: totalActive })
                : t('topbar.allComplete')}
            </div>
            {completedCount > 0 && (
              <button
                onClick={() => clearCompleted()}
                className="text-[10px] text-zinc-500 hover:text-zinc-300 transition-colors"
              >
                {t('topbar.clearCompleted')}
              </button>
            )}
          </div>
        </>
      ) : (
        <div className="flex flex-col items-center justify-center py-10 text-zinc-500">
          <Inbox size={28} className="mb-2 text-zinc-600" />
          <span className="text-sm">{t('topbar.noItems')}</span>
        </div>
      )}
    </PanelShell>
  );
};

// ---------------------------------------------------------------------------
// Notifications panel
// ---------------------------------------------------------------------------

const NotificationsPanel: React.FC = () => {
  const { t } = useTranslation();
  return (
    <PanelShell className="w-80">
      <div className="px-4 py-3 border-b border-zinc-800">
        <span className="text-sm font-semibold text-zinc-200">{t('topbar.notifications')}</span>
      </div>
      <div className="flex flex-col items-center justify-center py-10 text-zinc-500">
        <Bell size={28} className="mb-2 text-zinc-600" />
        <span className="text-sm">{t('topbar.noNotifications')}</span>
      </div>
    </PanelShell>
  );
};

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
      <div className="px-4 py-3 border-b border-zinc-800">
        <p className="text-sm font-semibold text-zinc-200 truncate">{user.name}</p>
        <p className="text-xs text-zinc-500 truncate">{user.email}</p>
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

      <div className="border-t border-zinc-800" />

      <div className="py-1">
        <MenuButton
          icon={HelpCircle}
          label={t('topbar.helpDocs')}
          onClick={() => {
            /* placeholder */
          }}
        />
      </div>

      <div className="border-t border-zinc-800" />

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
        : 'text-zinc-300 hover:bg-zinc-800 hover:text-zinc-100'
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
      className="flex items-center justify-center rounded-full bg-indigo-500/20 text-indigo-400 font-semibold text-sm select-none"
      style={{ width: size, height: size }}
    >
      {initial}
    </div>
  );
};

// ---------------------------------------------------------------------------
// TopBar
// ---------------------------------------------------------------------------

export const TopBar: React.FC<TopBarProps> = ({ user, unreadCount = 0, onNavigate, onSignOut, onOpenSettings, sidebarCollapsed = false }) => {
  const { t } = useTranslation();
  const [openPanel, setOpenPanel] = useState<PanelType>(null);
  const upload = useUpload();
  const { totalActive } = useTaskManager();

  // Auto-open task center panel when upload starts (instant, browser-side)
  useEffect(() => {
    if (upload.isUploading && openPanel !== 'taskCenter') {
      setOpenPanel('taskCenter');
    }
  }, [upload.isUploading]); // eslint-disable-line react-hooks/exhaustive-deps

  const taskCenterRef = useRef<HTMLDivElement>(null);
  const notificationsRef = useRef<HTMLDivElement>(null);
  const avatarRef = useRef<HTMLDivElement>(null);

  // Only one panel open at a time — toggle or switch
  const togglePanel = (panel: PanelType) => {
    setOpenPanel((prev) => (prev === panel ? null : panel));
  };

  const closeAll = () => setOpenPanel(null);

  // Close handlers for each panel
  useCloseOnOutsideOrEscape(taskCenterRef, openPanel === 'taskCenter', closeAll);
  useCloseOnOutsideOrEscape(notificationsRef, openPanel === 'notifications', closeAll);
  useCloseOnOutsideOrEscape(avatarRef, openPanel === 'avatar', closeAll);

  return (
    <header className={`fixed top-0 right-0 left-0 ${sidebarCollapsed ? 'sm:left-20' : 'sm:left-64'} h-14 border-b border-zinc-800 bg-zinc-950/80 backdrop-blur-sm z-30 flex items-center justify-end px-6 gap-2 transition-[left] duration-300`}>
      {/* Language Switcher */}
      <LanguageSwitcher />

      {/* Search */}
      <IconButton
        title={t('topbar.search')}
        onClick={() => { /* Cmd+K search — Phase 2+ */ }}
      >
        <Search size={18} />
      </IconButton>

      {/* Task Center */}
      <div ref={taskCenterRef} className="relative">
        <IconButton
          title={t('topbar.taskCenter')}
          onClick={() => togglePanel('taskCenter')}
          active={openPanel === 'taskCenter'}
          badge={totalActive > 0 ? totalActive : undefined}
        >
          <ListTodo size={18} />
        </IconButton>
        {openPanel === 'taskCenter' && <TaskCenterPanel />}
      </div>

      {/* Notifications */}
      <div ref={notificationsRef} className="relative">
        <IconButton
          title={t('topbar.notifications')}
          onClick={() => togglePanel('notifications')}
          active={openPanel === 'notifications'}
          badge={unreadCount}
        >
          <Bell size={18} />
        </IconButton>
        {openPanel === 'notifications' && <NotificationsPanel />}
      </div>

      {/* Avatar Menu */}
      <div ref={avatarRef} className="relative">
        <button
          title={t('topbar.account')}
          onClick={() => togglePanel('avatar')}
          className="p-1 rounded-lg transition-colors hover:bg-zinc-800"
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
