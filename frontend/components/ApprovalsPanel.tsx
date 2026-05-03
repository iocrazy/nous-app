/**
 * G1-UI — Pending agent approvals panel.
 *
 * Lists rows from agent_approval_requests where status='pending' and
 * lets the user approve or reject each. Used in two surfaces:
 *   - Settings → AI (full panel, embedded section)
 *   - TopBar dropdown (compact rendering on click of the badge)
 *
 * The same component handles both — pass `compact` to switch styling.
 *
 * Auto-refresh every 30s while mounted. After a decision the row is
 * removed locally and the next refresh confirms.
 */
import React, { useCallback, useEffect, useState } from 'react';
import { useTranslation } from 'react-i18next';
import {
  ShieldAlert,
  Check,
  X,
  RefreshCw,
  Clock,
} from 'lucide-react';
import { aiLibraryService } from '../services/aiLibraryService';
import { useToast } from './Toast';
import type { AILibraryApprovalRequest } from '../types';

interface ApprovalsPanelProps {
  className?: string;
  compact?: boolean;
  hideWhenEmpty?: boolean;
  /** Reload trigger — incremented externally to force a refetch. */
  reloadKey?: number;
  /** Notified of count after each load (used to drive TopBar badge). */
  onCountChange?: (count: number) => void;
}

function _formatExpires(iso: string | null): string {
  if (!iso) return '';
  const t = new Date(iso).getTime();
  const diff = t - Date.now();
  if (diff <= 0) return 'expired';
  const HOUR = 3600_000;
  if (diff < HOUR) {
    return `${Math.round(diff / 60_000)}m left`;
  }
  return `${Math.round(diff / HOUR)}h left`;
}

export const ApprovalsPanel: React.FC<ApprovalsPanelProps> = ({
  className = '',
  compact = false,
  hideWhenEmpty = false,
  reloadKey,
  onCountChange,
}) => {
  const { t } = useTranslation();
  const { addToast } = useToast();
  const [items, setItems] = useState<AILibraryApprovalRequest[]>([]);
  const [loading, setLoading] = useState(true);
  const [actingId, setActingId] = useState<string | null>(null);

  const reload = useCallback(async () => {
    try {
      const resp = await aiLibraryService.listApprovalRequests();
      setItems(resp.items);
      onCountChange?.(resp.count);
    } catch (err) {
      console.error('[ApprovalsPanel] load failed:', err);
    } finally {
      setLoading(false);
    }
  }, [onCountChange]);

  useEffect(() => {
    void reload();
    const id = window.setInterval(reload, 30_000);
    return () => window.clearInterval(id);
  }, [reload, reloadKey]);

  const handleApprove = useCallback(
    async (id: string) => {
      setActingId(id);
      try {
        await aiLibraryService.approveRequest(id);
        setItems((prev) => prev.filter((r) => r.id !== id));
        addToast(t('approvals.approvedToast'), 'success');
        void reload();
      } catch (err) {
        const msg = err instanceof Error ? err.message : String(err);
        addToast(t('approvals.approveFailed', { message: msg }), 'error');
      } finally {
        setActingId(null);
      }
    },
    [addToast, reload, t],
  );

  const handleReject = useCallback(
    async (id: string) => {
      setActingId(id);
      try {
        await aiLibraryService.rejectRequest(id);
        setItems((prev) => prev.filter((r) => r.id !== id));
        addToast(t('approvals.rejectedToast'), 'info');
        void reload();
      } catch (err) {
        const msg = err instanceof Error ? err.message : String(err);
        addToast(t('approvals.rejectFailed', { message: msg }), 'error');
      } finally {
        setActingId(null);
      }
    },
    [addToast, reload, t],
  );

  if (hideWhenEmpty && !loading && items.length === 0) {
    return null;
  }

  return (
    <div className={`flex flex-col ${className}`}>
      {/* Header */}
      <div className="flex items-center justify-between px-3 py-2 border-b border-zinc-800">
        <div className="flex items-center gap-2 text-xs font-semibold text-zinc-300">
          <ShieldAlert className="w-3.5 h-3.5 text-amber-400" />
          {t('approvals.title')}
          {items.length > 0 && (
            <span className="px-1.5 py-0.5 rounded bg-amber-600/20 text-amber-300 text-[10px]">
              {items.length}
            </span>
          )}
        </div>
        <button
          type="button"
          onClick={reload}
          className="text-zinc-500 hover:text-zinc-300 p-1 rounded hover:bg-zinc-800 transition-colors"
          title={t('approvals.refresh')}
        >
          <RefreshCw className={`w-3 h-3 ${loading ? 'animate-spin' : ''}`} />
        </button>
      </div>

      {/* List */}
      <div className={`flex-1 overflow-y-auto ${compact ? 'max-h-96' : ''}`}>
        {loading && items.length === 0 ? (
          <div className="px-3 py-4 text-xs text-zinc-500 text-center">
            {t('approvals.loading')}
          </div>
        ) : items.length === 0 ? (
          <div className="px-3 py-6 text-xs text-zinc-500 text-center">
            <ShieldAlert className="w-6 h-6 mx-auto mb-2 opacity-30" />
            {t('approvals.empty')}
          </div>
        ) : (
          <ul className="divide-y divide-zinc-800">
            {items.map((req) => (
              <li
                key={req.id}
                className="px-3 py-3 hover:bg-zinc-900/50 transition-colors"
              >
                <div className="flex items-start gap-2 mb-2">
                  <div className="flex-1 min-w-0">
                    <div className="text-xs text-zinc-200 leading-relaxed font-medium">
                      {req.reason}
                    </div>
                    <div className="flex items-center gap-2 mt-1 text-[10px] text-zinc-500">
                      <span className="px-1.5 py-0.5 rounded bg-zinc-800 text-zinc-400 font-mono">
                        {req.hook_name}
                      </span>
                      {req.expires_at && (
                        <span className="flex items-center gap-1">
                          <Clock className="w-2.5 h-2.5" />
                          {_formatExpires(req.expires_at)}
                        </span>
                      )}
                    </div>
                  </div>
                </div>

                {/* Payload preview (compact mode hides full dump) */}
                {!compact && Object.keys(req.payload).length > 0 && (
                  <pre className="text-[10px] text-zinc-500 bg-zinc-950/50 rounded p-2 overflow-x-auto mb-2 max-h-24">
                    {JSON.stringify(req.payload, null, 2)}
                  </pre>
                )}

                {/* Actions */}
                <div className="flex items-center gap-2">
                  <button
                    type="button"
                    disabled={actingId === req.id}
                    onClick={() => handleApprove(req.id)}
                    className="flex items-center gap-1 px-2 py-1 text-xs rounded bg-green-600 hover:bg-green-700 text-white transition-colors disabled:opacity-40"
                  >
                    <Check className="w-3 h-3" />
                    {t('approvals.approve')}
                  </button>
                  <button
                    type="button"
                    disabled={actingId === req.id}
                    onClick={() => handleReject(req.id)}
                    className="flex items-center gap-1 px-2 py-1 text-xs rounded bg-zinc-800 hover:bg-red-900/40 text-zinc-300 hover:text-red-300 transition-colors disabled:opacity-40"
                  >
                    <X className="w-3 h-3" />
                    {t('approvals.reject')}
                  </button>
                </div>
              </li>
            ))}
          </ul>
        )}
      </div>
    </div>
  );
};

export default ApprovalsPanel;
