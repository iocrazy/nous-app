/**
 * O5 — Memory Visualization Page.
 *
 * Browse, filter and inspect what the agent system has learned about
 * you across all your sessions. Each row is one row from agent_memories.
 *
 * Sections:
 *  - Stat tiles (by_kind / by_status / by_agent count)
 *  - Filter bar: kind / status / agent
 *  - Memory list (table) with archive control
 *  - Click a row to see full detail (when_to_use, decay_score,
 *    reinforce_count, thread/session refs)
 */
import React, { useEffect, useState, useMemo, useCallback } from 'react';
import { useTranslation } from 'react-i18next';
import {
  Brain,
  Archive,
  RefreshCw,
  Tag as TagIcon,
  Activity,
  Filter,
} from 'lucide-react';
import { aiLibraryService } from '../services/aiLibraryService';
import { useToast } from '../components/Toast';
import type { AILibraryMemory } from '../types';

const KIND_COLORS: Record<string, string> = {
  declarative: 'bg-blue-500/15 text-blue-300 border-blue-700/40',
  procedural: 'bg-amber-500/15 text-amber-300 border-amber-700/40',
  episodic: 'bg-purple-500/15 text-purple-300 border-purple-700/40',
  unknown: 'bg-ink-700 text-ink-400 border-ink-700',
};

const STATUS_COLORS: Record<string, string> = {
  active: 'text-green-400',
  archived: 'text-ink-500',
  superseded: 'text-orange-400',
};

interface Stats {
  by_kind: Record<string, number>;
  by_status: Record<string, number>;
  by_agent: Record<string, number>;
}

export const MemoryViewerPage: React.FC = () => {
  const { t } = useTranslation();
  const { addToast } = useToast();
  const [items, setItems] = useState<AILibraryMemory[]>([]);
  const [stats, setStats] = useState<Stats | null>(null);
  const [loading, setLoading] = useState(true);
  const [filterKind, setFilterKind] = useState<'all' | 'declarative' | 'procedural' | 'episodic'>('all');
  const [filterStatus, setFilterStatus] = useState<'all' | 'active' | 'archived' | 'superseded'>('active');
  const [archiving, setArchiving] = useState<string | null>(null);
  const [expandedId, setExpandedId] = useState<string | null>(null);

  const reload = useCallback(async () => {
    setLoading(true);
    try {
      const opts: Parameters<typeof aiLibraryService.listMemories>[0] = { limit: 200 };
      if (filterKind !== 'all') opts.kind = filterKind;
      if (filterStatus !== 'all') opts.status = filterStatus;
      const resp = await aiLibraryService.listMemories(opts);
      setItems(resp.items);
      setStats(resp.stats);
    } catch (err) {
      console.error('[MemoryViewerPage] load failed:', err);
      addToast(t('memoryViewer.loadFailed'), 'error');
    } finally {
      setLoading(false);
    }
  }, [filterKind, filterStatus, addToast, t]);

  useEffect(() => {
    void reload();
  }, [reload]);

  const handleArchive = useCallback(
    async (memoryId: string) => {
      if (!window.confirm(t('memoryViewer.archiveConfirm'))) {
        return;
      }
      setArchiving(memoryId);
      try {
        await aiLibraryService.archiveMemory(memoryId);
        setItems((prev) =>
          prev.map((m) => (m.id === memoryId ? { ...m, status: 'archived' } : m)),
        );
        addToast(t('memoryViewer.archivedToast'), 'success');
      } catch (err) {
        const msg = err instanceof Error ? err.message : String(err);
        addToast(t('memoryViewer.archiveFailed', { message: msg }), 'error');
      } finally {
        setArchiving(null);
      }
    },
    [addToast, t],
  );

  const totalCount = items.length;
  const visibleItems = useMemo(() => {
    if (filterStatus === 'all') return items;
    return items.filter((m) => m.status === filterStatus);
  }, [items, filterStatus]);

  return (
    <div className="flex flex-col h-full bg-ink-950 text-ink-200 overflow-hidden">
      {/* Header */}
      <div className="flex items-center gap-3 px-6 py-4 border-b border-ink-800 flex-shrink-0">
        <Brain className="w-5 h-5 text-blue-400" />
        <div className="flex-1">
          <h1 className="text-lg font-semibold">{t('memoryViewer.title')}</h1>
          <p className="text-xs text-ink-500">
            {t('memoryViewer.subtitle')}
          </p>
        </div>
        <button
          type="button"
          onClick={reload}
          className="p-1.5 rounded hover:bg-ink-800 text-ink-400"
          title={t('memoryViewer.refresh')}
        >
          <RefreshCw className={`w-4 h-4 ${loading ? 'animate-spin' : ''}`} />
        </button>
      </div>

      {/* Stat tiles */}
      {stats && (
        <div className="grid grid-cols-4 gap-3 px-6 py-4 flex-shrink-0">
          <div className="bg-ink-900 rounded-lg p-3 border border-ink-800">
            <div className="text-[10px] uppercase tracking-wide text-ink-500">
              {t('memoryViewer.stat_total')}
            </div>
            <div className="text-2xl font-bold mt-1">{totalCount}</div>
          </div>
          <div className="bg-ink-900 rounded-lg p-3 border border-ink-800">
            <div className="text-[10px] uppercase tracking-wide text-ink-500">
              {t('memoryViewer.stat_declarative')}
            </div>
            <div className="text-2xl font-bold mt-1 text-blue-300">
              {stats.by_kind.declarative ?? 0}
            </div>
          </div>
          <div className="bg-ink-900 rounded-lg p-3 border border-ink-800">
            <div className="text-[10px] uppercase tracking-wide text-ink-500">
              {t('memoryViewer.stat_procedural')}
            </div>
            <div className="text-2xl font-bold mt-1 text-amber-300">
              {stats.by_kind.procedural ?? 0}
            </div>
          </div>
          <div className="bg-ink-900 rounded-lg p-3 border border-ink-800">
            <div className="text-[10px] uppercase tracking-wide text-ink-500">
              {t('memoryViewer.stat_episodic')}
            </div>
            <div className="text-2xl font-bold mt-1 text-purple-300">
              {stats.by_kind.episodic ?? 0}
            </div>
          </div>
        </div>
      )}

      {/* Filter bar */}
      <div className="flex items-center gap-3 px-6 py-2 border-y border-ink-800 bg-ink-900/50 flex-shrink-0">
        <Filter className="w-3.5 h-3.5 text-ink-500" />
        <span className="text-xs text-ink-500">{t('memoryViewer.filter')}</span>

        <div className="flex items-center gap-1">
          <span className="text-xs text-ink-600">{t('memoryViewer.filter_kind')}</span>
          {(['all', 'declarative', 'procedural', 'episodic'] as const).map((k) => (
            <button
              key={k}
              type="button"
              onClick={() => setFilterKind(k)}
              className={`px-2 py-0.5 rounded text-xs transition-colors ${
                filterKind === k
                  ? 'bg-blue-600 text-white'
                  : 'text-ink-500 hover:bg-ink-800'
              }`}
            >
              {k}
            </button>
          ))}
        </div>

        <div className="flex items-center gap-1 ml-3">
          <span className="text-xs text-ink-600">{t('memoryViewer.filter_status')}</span>
          {(['all', 'active', 'archived', 'superseded'] as const).map((s) => (
            <button
              key={s}
              type="button"
              onClick={() => setFilterStatus(s)}
              className={`px-2 py-0.5 rounded text-xs transition-colors ${
                filterStatus === s
                  ? 'bg-blue-600 text-white'
                  : 'text-ink-500 hover:bg-ink-800'
              }`}
            >
              {s}
            </button>
          ))}
        </div>
      </div>

      {/* Memory list */}
      <div className="flex-1 overflow-y-auto px-6 py-4 min-h-0">
        {loading && items.length === 0 ? (
          <div className="text-center py-12 text-ink-500 text-sm">{t('memoryViewer.loading')}</div>
        ) : visibleItems.length === 0 ? (
          <div className="text-center py-12 text-ink-500 text-sm">
            {t('memoryViewer.noMatch')}
          </div>
        ) : (
          <ul className="space-y-2">
            {visibleItems.map((m) => {
              const kind = m.kind || 'unknown';
              const isExpanded = expandedId === m.id;
              return (
                <li
                  key={m.id}
                  className="bg-ink-900 border border-ink-800 rounded-lg p-3 hover:border-ink-700 transition-colors"
                >
                  <div className="flex items-start gap-3">
                    <span
                      className={`px-2 py-0.5 rounded text-[10px] font-medium border ${KIND_COLORS[kind]}`}
                    >
                      {kind}
                    </span>
                    <div className="flex-1 min-w-0">
                      <button
                        type="button"
                        onClick={() => setExpandedId(isExpanded ? null : m.id)}
                        className="text-left w-full"
                      >
                        <div className="text-sm text-ink-200 leading-relaxed">
                          {m.summary}
                        </div>
                      </button>
                      {isExpanded && (
                        <div className="mt-2 pt-2 border-t border-ink-800 space-y-1 text-xs text-ink-400">
                          {m.when_to_use && (
                            <div>
                              <span className="text-ink-500">{t('memoryViewer.whenToUse')}</span>{' '}
                              {m.when_to_use}
                            </div>
                          )}
                          <div className="flex flex-wrap gap-3">
                            <span>
                              <span className="text-ink-500">{t('memoryViewer.scope')}</span> {m.scope}
                            </span>
                            <span>
                              <span className="text-ink-500">{t('memoryViewer.reinforced')}</span>{' '}
                              {m.reinforce_count}×
                            </span>
                            {m.decay_score !== null && (
                              <span>
                                <span className="text-ink-500">{t('memoryViewer.decay')}</span>{' '}
                                {m.decay_score.toFixed(2)}
                              </span>
                            )}
                            {m.extracted_from && (
                              <span>
                                <span className="text-ink-500">{t('memoryViewer.from')}</span>{' '}
                                {m.extracted_from}
                              </span>
                            )}
                          </div>
                          <div className="text-ink-600 text-[10px]">
                            id: {m.id} · created{' '}
                            {m.created_at ? new Date(m.created_at).toLocaleString() : '?'}
                          </div>
                        </div>
                      )}
                      <div className="flex items-center gap-3 mt-1.5 text-[10px] text-ink-500">
                        <span className={STATUS_COLORS[m.status]}>
                          <Activity className="w-2.5 h-2.5 inline mr-0.5" />
                          {m.status}
                        </span>
                        {m.reinforce_count > 0 && (
                          <span>
                            <TagIcon className="w-2.5 h-2.5 inline mr-0.5" />
                            {m.reinforce_count} reinforce
                            {m.reinforce_count !== 1 ? 's' : ''}
                          </span>
                        )}
                        {m.thread_id && (
                          <span className="truncate max-w-[200px]" title={m.thread_id}>
                            thread: {m.thread_id.slice(0, 8)}
                          </span>
                        )}
                      </div>
                    </div>
                    {m.status === 'active' && (
                      <button
                        type="button"
                        disabled={archiving === m.id}
                        onClick={() => handleArchive(m.id)}
                        className="text-ink-500 hover:text-ink-300 hover:bg-ink-800 p-1.5 rounded transition-colors disabled:opacity-40"
                        title={t('memoryViewer.archive')}
                      >
                        <Archive className="w-3.5 h-3.5" />
                      </button>
                    )}
                  </div>
                </li>
              );
            })}
          </ul>
        )}
      </div>
    </div>
  );
};

export default MemoryViewerPage;
