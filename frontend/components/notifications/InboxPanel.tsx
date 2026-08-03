import React from 'react';
import { useTranslation } from 'react-i18next';
import { useNavigate } from 'react-router-dom';
import {
  Bell,
  Sparkles,
  Send,
  Bot,
  Megaphone,
  CheckCheck,
  Workflow,
  MessageCircleQuestion,
} from 'lucide-react';
import { useInbox } from '../../contexts/InboxContext';
import { useWorkspaceScope } from '../../hooks/useWorkspaceScope';
import { resolveNotificationLink } from './notificationLink';
import type { InboxKind, InboxSeverity } from '../../services/notificationsService';
import type { UnifiedNotification } from '../../services/notificationMerge';

/**
 * Unified notification panel (W3d + release-notes merge) — rendered inside the
 * TopBar bell's PanelShell. One list mixing the three narrow inbox kinds
 * (typed icon + severity tint + deep link) with broadcast release-note /
 * announcement rows (megaphone, read-inline, no navigation). Clicking a row
 * marks it read against its own backend; "Mark all read" clears both feeds.
 */

const KIND_ICON: Record<InboxKind, React.ComponentType<{ size?: number; className?: string }>> = {
  generation_result: Sparkles,
  publish_result: Send,
  autopilot_output: Bot,
  workflow_stage: Workflow,
  agent_question: MessageCircleQuestion,
};

function severityClasses(severity: InboxSeverity): { dot: string; icon: string } {
  switch (severity) {
    case 'success':
      return { dot: 'bg-emerald-500', icon: 'text-emerald-500' };
    case 'error':
      return { dot: 'bg-red-500', icon: 'text-red-500' };
    default:
      return { dot: 'bg-indigo-500', icon: 'text-content-3' };
  }
}

function relativeTime(iso?: string | null): string {
  if (!iso) return '';
  const then = new Date(iso).getTime();
  if (Number.isNaN(then)) return '';
  const secs = Math.max(0, Math.floor((Date.now() - then) / 1000));
  if (secs < 60) return 'just now';
  const mins = Math.floor(secs / 60);
  if (mins < 60) return `${mins}m ago`;
  const hours = Math.floor(mins / 60);
  if (hours < 24) return `${hours}h ago`;
  const days = Math.floor(hours / 24);
  if (days < 7) return `${days}d ago`;
  return new Date(iso).toLocaleDateString();
}

export const InboxPanel: React.FC<{ onClose?: () => void }> = ({ onClose }) => {
  const { t } = useTranslation();
  const navigate = useNavigate();
  const { effectiveTeamId } = useWorkspaceScope();
  const { notifications, unreadCount, markRead, markAllRead } = useInbox();

  // The navigation target for an inbox row (broadcast rows never navigate —
  // their content is shown inline, matching the old release-notes panel).
  const inboxLink = (n: UnifiedNotification): string | null =>
    n.source === 'inbox'
      ? resolveNotificationLink({
          linkKind: n.link_kind,
          linkId: n.link_id,
          teamId: n.team_id || effectiveTeamId,
        })
      : null;

  const handleClick = (n: UnifiedNotification) => {
    if (!n.read) void markRead(n.id, n.source);
    const path = inboxLink(n);
    if (path) {
      navigate(path);
      onClose?.();
    }
  };

  return (
    <>
      <div className="flex items-center justify-between px-4 py-3 border-b border-line">
        <span className="text-sm font-semibold text-content-2">
          {t('topbar.notifications')}
        </span>
        {unreadCount > 0 && (
          <button
            onClick={() => void markAllRead()}
            className="flex items-center gap-1 text-xs text-content-3 hover:text-content-2 transition-colors"
          >
            <CheckCheck size={13} />
            {t('inbox.markAllRead', 'Mark all read')}
          </button>
        )}
      </div>

      {notifications.length === 0 ? (
        <div className="flex flex-col items-center justify-center py-10 text-content-3">
          <Bell size={28} className="mb-2 text-content-4" />
          <span className="text-sm">{t('inbox.empty', "You're all caught up.")}</span>
        </div>
      ) : (
        <div className="max-h-[24rem] overflow-y-auto">
          {notifications.map((n) => {
            // Broadcast (release-note / announcement) rows: megaphone, inline
            // content, no deep link. Inbox rows: typed kind icon + severity +
            // optional navigation.
            const isBroadcast = n.source === 'broadcast';
            const Icon = isBroadcast ? Megaphone : KIND_ICON[n.kind] || Bell;
            const sev = isBroadcast
              ? { dot: 'bg-indigo-500', icon: 'text-content-3' }
              : severityClasses(n.severity);
            const clickable = !!inboxLink(n);
            const body = n.body;
            return (
              <button
                key={`${n.source}-${n.id}`}
                onClick={() => handleClick(n)}
                disabled={!clickable && n.read}
                className={`w-full flex items-start gap-3 px-4 py-3 text-left border-b border-line/60 last:border-b-0 transition-colors ${
                  clickable ? 'hover:bg-island-2 cursor-pointer' : 'cursor-default'
                } ${n.read ? 'opacity-60' : ''}`}
              >
                <div className="relative mt-0.5 shrink-0">
                  <Icon size={16} className={sev.icon} />
                  {!n.read && (
                    <span
                      className={`absolute -top-1 -right-1 w-2 h-2 rounded-full ${sev.dot}`}
                    />
                  )}
                </div>
                <div className="min-w-0 flex-1">
                  <div className="text-sm text-content-2 truncate">{n.title}</div>
                  {body && (
                    <div className="text-xs text-content-3 truncate">{body}</div>
                  )}
                  <div className="text-[11px] text-content-4 mt-0.5">
                    {relativeTime(n.created_at)}
                  </div>
                </div>
              </button>
            );
          })}
        </div>
      )}
    </>
  );
};
