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
} from 'lucide-react';
import { useTranslation } from 'react-i18next';
import { LanguageSwitcher } from './LanguageSwitcher';

// ---------------------------------------------------------------------------
// Types
// ---------------------------------------------------------------------------

interface TopBarProps {
  user: { name: string; email: string; avatarUrl?: string } | null;
  unreadCount?: number;
  onNavigate: (view: string, tab?: string) => void;
  onSignOut: () => void;
  onOpenSettings?: (tab?: string) => void;
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

const TaskCenterPanel: React.FC = () => {
  const { t } = useTranslation();
  const [tab, setTab] = useState<'transfers' | 'tasks'>('transfers');

  return (
    <PanelShell className="w-80">
      {/* Tab bar */}
      <div className="flex border-b border-zinc-800">
        {(['transfers', 'tasks'] as const).map((key) => (
          <button
            key={key}
            onClick={() => setTab(key)}
            className={`flex-1 px-4 py-2.5 text-sm font-medium transition-colors ${
              tab === key
                ? 'text-indigo-400 border-b-2 border-indigo-400'
                : 'text-zinc-500 hover:text-zinc-300'
            }`}
          >
            {t(`topbar.${key}`)}
          </button>
        ))}
      </div>

      {/* Empty state */}
      <div className="flex flex-col items-center justify-center py-10 text-zinc-500">
        <Inbox size={28} className="mb-2 text-zinc-600" />
        <span className="text-sm">{t('topbar.noItems')}</span>
      </div>
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
  onNavigate: (view: string, tab?: string) => void;
  onSignOut: () => void;
  onOpenSettings?: (tab?: string) => void;
}> = ({ user, onNavigate, onSignOut, onOpenSettings }) => {
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

export const TopBar: React.FC<TopBarProps> = ({ user, unreadCount = 0, onNavigate, onSignOut, onOpenSettings }) => {
  const { t } = useTranslation();
  const [openPanel, setOpenPanel] = useState<PanelType>(null);

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
    <header className="fixed top-0 right-0 left-64 h-14 border-b border-zinc-800 bg-zinc-950/80 backdrop-blur-sm z-30 flex items-center justify-end px-6 gap-2">
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
          <AvatarMenu user={user} onNavigate={onNavigate} onSignOut={onSignOut} onOpenSettings={onOpenSettings} />
        )}
      </div>
    </header>
  );
};

export default TopBar;
