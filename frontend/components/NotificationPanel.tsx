import React, { useEffect, useRef } from 'react';
import { Bell, Users, CheckCheck } from 'lucide-react';
import { useTranslation } from 'react-i18next';

interface Notification {
  id: string;
  type: 'system' | 'team';
  title: string;
  content?: string;
  createdAt: string;
  read: boolean;
}

interface NotificationPanelProps {
  isOpen: boolean;
  onClose: () => void;
  notifications: Notification[];
  onMarkRead: (id: string) => void;
  onMarkAllRead: () => void;
}

export const NotificationPanel: React.FC<NotificationPanelProps> = ({
  isOpen,
  onClose,
  notifications,
  onMarkRead,
  onMarkAllRead,
}) => {
  const { t } = useTranslation();
  const panelRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    const handleClickOutside = (e: MouseEvent) => {
      if (panelRef.current && !panelRef.current.contains(e.target as Node)) {
        onClose();
      }
    };
    const handleEsc = (e: KeyboardEvent) => {
      if (e.key === 'Escape') onClose();
    };

    if (isOpen) {
      document.addEventListener('mousedown', handleClickOutside);
      document.addEventListener('keydown', handleEsc);
    }
    return () => {
      document.removeEventListener('mousedown', handleClickOutside);
      document.removeEventListener('keydown', handleEsc);
    };
  }, [isOpen, onClose]);

  if (!isOpen) return null;

  const systemNotifications = notifications.filter(n => n.type === 'system');
  const teamNotifications = notifications.filter(n => n.type === 'team');
  const hasUnread = notifications.some(n => !n.read);

  const formatTime = (dateStr: string) => {
    const date = new Date(dateStr);
    const now = new Date();
    const diffMs = now.getTime() - date.getTime();
    const diffMins = Math.floor(diffMs / 60000);
    const diffHours = Math.floor(diffMs / 3600000);
    const diffDays = Math.floor(diffMs / 86400000);

    if (diffMins < 60) return `${diffMins}m`;
    if (diffHours < 24) return `${diffHours}h`;
    if (diffDays < 7) return `${diffDays}d`;
    return date.toLocaleDateString();
  };

  const NotificationItem = ({ item }: { item: Notification }) => (
    <button
      onClick={() => onMarkRead(item.id)}
      className={`w-full text-left p-3 rounded-lg transition-colors ${
        item.read ? 'bg-transparent hover:bg-zinc-800/30' : 'bg-zinc-800/50 hover:bg-zinc-800'
      }`}
    >
      <div className="flex items-start justify-between gap-2">
        <div className="flex-1 min-w-0">
          <p className={`text-sm font-medium truncate ${item.read ? 'text-zinc-400' : 'text-zinc-200'}`}>
            {item.title}
          </p>
          {item.content && (
            <p className="text-xs text-zinc-500 truncate mt-0.5">{item.content}</p>
          )}
        </div>
        <div className="flex items-center gap-2 flex-shrink-0">
          <span className="text-xs text-zinc-600">{formatTime(item.createdAt)}</span>
          {!item.read && <div className="w-2 h-2 rounded-full bg-indigo-500" />}
        </div>
      </div>
    </button>
  );

  return (
    <div
      ref={panelRef}
      className="absolute top-full right-0 mt-2 w-80 bg-zinc-900 border border-zinc-800 rounded-xl shadow-2xl z-50 animate-in fade-in slide-in-from-top-2 duration-200 overflow-hidden"
    >
      {/* Header */}
      <div className="flex items-center justify-between px-4 py-3 border-b border-zinc-800">
        <h3 className="font-semibold text-zinc-200">{t('notifications.title')}</h3>
        {hasUnread && (
          <button
            onClick={onMarkAllRead}
            className="flex items-center gap-1 text-xs text-indigo-400 hover:text-indigo-300 transition-colors"
          >
            <CheckCheck size={14} />
            <span>{t('notifications.markAllRead')}</span>
          </button>
        )}
      </div>

      {/* Content */}
      <div className="max-h-[400px] overflow-y-auto">
        {notifications.length === 0 ? (
          <div className="flex flex-col items-center justify-center py-10 text-zinc-500">
            <Bell size={32} className="mb-2 opacity-50" />
            <p className="text-sm">{t('notifications.empty')}</p>
          </div>
        ) : (
          <div className="p-2 space-y-3">
            {/* System Notifications */}
            {systemNotifications.length > 0 && (
              <div>
                <p className="px-2 py-1 text-xs font-semibold text-zinc-500 uppercase tracking-wider flex items-center gap-1">
                  <Bell size={12} />
                  {t('notifications.system')}
                </p>
                <div className="space-y-1">
                  {systemNotifications.map(n => (
                    <NotificationItem key={n.id} item={n} />
                  ))}
                </div>
              </div>
            )}

            {/* Team Notifications */}
            {teamNotifications.length > 0 && (
              <div>
                <p className="px-2 py-1 text-xs font-semibold text-zinc-500 uppercase tracking-wider flex items-center gap-1">
                  <Users size={12} />
                  {t('notifications.team')}
                </p>
                <div className="space-y-1">
                  {teamNotifications.map(n => (
                    <NotificationItem key={n.id} item={n} />
                  ))}
                </div>
              </div>
            )}
          </div>
        )}
      </div>
    </div>
  );
};
