/**
 * O4 — User-facing followups list.
 *
 * Lists the caller's pending agent commitments (next_session / time / event
 * triggers) with fulfill/dismiss controls. Intended to be embedded
 * above the session list in AIChatPanel or as a standalone panel
 * the user can open from the chat drawer.
 *
 * Design notes:
 *  - Pending only by default (filter via `status` prop)
 *  - Fulfill = "I took care of this" → marks fulfilled
 *  - Dismiss = "not relevant anymore" → marks cancelled
 *  - Auto-refreshes every 30s while mounted
 */
import React, { useEffect, useState, useCallback } from 'react';
import { useTranslation } from 'react-i18next';
import { Check, X, Clock, ListTodo, RefreshCw } from 'lucide-react';
import { aiLibraryService } from '../services/aiLibraryService';
import { useToast } from './Toast';
import type { AILibraryCommitment } from '../types';

interface CommitmentsPanelProps {
  status?: 'pending' | 'fulfilled' | 'cancelled' | 'failed' | 'expired';
  limit?: number;
  className?: string;
  emptyText?: string;
  /** When true, the entire panel collapses to nothing if list is empty
   *  (after first load). Useful when embedded as an optional sidebar
   *  section so it never takes vertical space when not relevant. */
  hideWhenEmpty?: boolean;
}

const TRIGGER_KEY: Record<string, string> = {
  time: 'chat.followups.trigger_time',
  event: 'chat.followups.trigger_event',
  next_session: 'chat.followups.trigger_next_session',
};

function formatRelative(iso: string | null): string {
  if (!iso) return '';
  const t = new Date(iso).getTime();
  const diff = t - Date.now();
  const abs = Math.abs(diff);
  const HOUR = 3600_000;
  const DAY = 86400_000;
  if (abs < HOUR) {
    const m = Math.round(abs / 60_000);
    return diff > 0 ? `in ${m}m` : `${m}m ago`;
  }
  if (abs < DAY) {
    const h = Math.round(abs / HOUR);
    return diff > 0 ? `in ${h}h` : `${h}h ago`;
  }
  const d = Math.round(abs / DAY);
  return diff > 0 ? `in ${d}d` : `${d}d ago`;
}

export const CommitmentsPanel: React.FC<CommitmentsPanelProps> = ({
  status = 'pending',
  limit = 50,
  className = '',
  emptyText,
  hideWhenEmpty = false,
}) => {
  const { t } = useTranslation();
  const { addToast } = useToast();
  const resolvedEmptyText = emptyText ?? t('chat.followups.empty');
  const [items, setItems] = useState<AILibraryCommitment[]>([]);
  const [loading, setLoading] = useState(true);
  const [actingId, setActingId] = useState<number | null>(null);

  const reload = useCallback(async () => {
    try {
      const resp = await aiLibraryService.listCommitments({ status, limit });
      setItems(resp.items);
    } catch (err) {
      console.error('[CommitmentsPanel] load failed:', err);
    } finally {
      setLoading(false);
    }
  }, [status, limit]);

  useEffect(() => {
    void reload();
    const id = window.setInterval(reload, 30_000);
    return () => window.clearInterval(id);
  }, [reload]);

  const handleFulfill = useCallback(
    async (id: number) => {
      setActingId(id);
      try {
        await aiLibraryService.fulfillCommitment(id);
        setItems((prev) => prev.filter((c) => c.id !== id));
        addToast(t('chat.followups.doneToast'), 'success');
      } catch (err) {
        const msg = err instanceof Error ? err.message : String(err);
        addToast(t('chat.followups.fulfillFailed', { message: msg }), 'error');
      } finally {
        setActingId(null);
      }
    },
    [addToast, t],
  );

  const handleCancel = useCallback(
    async (id: number) => {
      setActingId(id);
      try {
        await aiLibraryService.cancelCommitment(id);
        setItems((prev) => prev.filter((c) => c.id !== id));
        addToast(t('chat.followups.dismissToast'), 'info');
      } catch (err) {
        const msg = err instanceof Error ? err.message : String(err);
        addToast(t('chat.followups.cancelFailed', { message: msg }), 'error');
      } finally {
        setActingId(null);
      }
    },
    [addToast, t],
  );

  if (hideWhenEmpty && !loading && items.length === 0) {
    return null;
  }

  return (
    <div className={`flex flex-col ${className}`}>
      <div className="flex items-center justify-between px-3 py-2 border-b border-zinc-800">
        <div className="flex items-center gap-2 text-xs font-semibold text-zinc-300">
          <ListTodo className="w-3.5 h-3.5" />
          {t('chat.followups.title')}
          {items.length > 0 && (
            <span className="px-1.5 py-0.5 rounded bg-blue-600/20 text-blue-300 text-[10px]">
              {items.length}
            </span>
          )}
        </div>
        <button
          type="button"
          onClick={reload}
          className="text-zinc-500 hover:text-zinc-300 p-1 rounded hover:bg-zinc-800 transition-colors"
          title={t('chat.followups.refresh')}
        >
          <RefreshCw className={`w-3 h-3 ${loading ? 'animate-spin' : ''}`} />
        </button>
      </div>

      <div className="flex-1 overflow-y-auto">
        {loading && items.length === 0 ? (
          <div className="px-3 py-4 text-xs text-zinc-500 text-center">
            {t('chat.followups.loading')}
          </div>
        ) : items.length === 0 ? (
          <div className="px-3 py-4 text-xs text-zinc-500 text-center">
            {resolvedEmptyText}
          </div>
        ) : (
          <ul className="divide-y divide-zinc-800">
            {items.map((c) => (
              <li key={c.id} className="px-3 py-2 hover:bg-zinc-900/50 transition-colors">
                <div className="text-xs text-zinc-300 leading-relaxed">
                  {c.description}
                </div>
                <div className="flex items-center justify-between mt-1.5">
                  <div className="flex items-center gap-2 text-[10px] text-zinc-500">
                    {c.trigger_type && (
                      <span className="flex items-center gap-1">
                        <Clock className="w-2.5 h-2.5" />
                        {t(TRIGGER_KEY[c.trigger_type]) || c.trigger_type}
                      </span>
                    )}
                    {c.trigger_at && (
                      <span>{formatRelative(c.trigger_at)}</span>
                    )}
                    {!c.trigger_at && c.expires_at && (
                      <span>expires {formatRelative(c.expires_at)}</span>
                    )}
                  </div>
                  <div className="flex items-center gap-1">
                    <button
                      type="button"
                      disabled={actingId === c.id}
                      onClick={() => handleFulfill(c.id)}
                      className="text-green-400 hover:text-green-300 hover:bg-green-900/30 p-1 rounded transition-colors disabled:opacity-40"
                      title={t('chat.followups.markDone')}
                    >
                      <Check className="w-3 h-3" />
                    </button>
                    <button
                      type="button"
                      disabled={actingId === c.id}
                      onClick={() => handleCancel(c.id)}
                      className="text-zinc-500 hover:text-zinc-300 hover:bg-zinc-800 p-1 rounded transition-colors disabled:opacity-40"
                      title={t('chat.followups.dismiss')}
                    >
                      <X className="w-3 h-3" />
                    </button>
                  </div>
                </div>
              </li>
            ))}
          </ul>
        )}
      </div>
    </div>
  );
};

export default CommitmentsPanel;
