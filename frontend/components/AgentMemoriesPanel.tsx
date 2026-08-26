import React, { useEffect, useState } from 'react';
import { Cpu, Trash2, AlertTriangle, X } from 'lucide-react';
import Loading from './common/Loading';
import { useTranslation } from 'react-i18next';
import {
  listAgentMemories,
  deleteAgentMemory,
  type AgentMemoryItem,
} from '../services/agentMemoryService';

/**
 * "My Agent Memories" panel — shows facts/decisions the AI has saved
 * (own private memories + team-shared memories from teammates).
 * Rendered in AISettings immediately after MemoryPanel.
 * Mirrors MemoryPanel's section shell (ink palette, header, loading, error, empty, list).
 */
export const AgentMemoriesPanel: React.FC = () => {
  const { t } = useTranslation();
  const [items, setItems] = useState<AgentMemoryItem[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [confirmDeleteId, setConfirmDeleteId] = useState<number | null>(null);

  useEffect(() => {
    // Guard against a late-resolving fetch touching state after unmount —
    // otherwise the setState fires in a torn-down jsdom (window is undefined)
    // and surfaces as an unhandled rejection in CI.
    let cancelled = false;
    setLoading(true);
    setError(null);
    listAgentMemories()
      .then((data) => {
        if (!cancelled) setItems(data);
      })
      .catch((err) => {
        if (!cancelled) {
          setError(err instanceof Error ? err.message : t('agentMemories.loadError'));
        }
      })
      .finally(() => {
        if (!cancelled) setLoading(false);
      });
    return () => {
      cancelled = true;
    };
  }, []);

  const handleDelete = async (id: number) => {
    // Capture item + position before optimistic removal so we can revert on failure
    const idx = items.findIndex((item) => item.id === id);
    if (idx === -1) return;
    const removed = items[idx];

    // Optimistically remove from local state + clear confirm
    setItems((prev) => prev.filter((item) => item.id !== id));
    setConfirmDeleteId(null);

    try {
      await deleteAgentMemory(id);
      // SUCCESS: item already removed from state, nothing more needed
    } catch (err) {
      // FAILURE: revert — re-insert the captured item at its original position
      setItems((prev) => {
        const next = [...prev];
        next.splice(idx, 0, removed);
        return next;
      });
      setError(err instanceof Error ? err.message : t('agentMemories.deleteError'));
    }
  };

  return (
    <section className="mt-8 bg-ink-900/40 border border-ink-800 rounded-lg overflow-hidden">
      <div className="px-6 py-4 flex items-center gap-3 border-b border-ink-800">
        <div className="p-2 rounded-lg bg-purple-500/10 text-purple-400">
          <Cpu size={18} />
        </div>
        <div className="flex-1">
          <h3 className="font-semibold text-ink-200">{t('agentMemories.title')}</h3>
          <p className="text-xs text-ink-500 mt-0.5">{t('agentMemories.subtitle')}</p>
        </div>
      </div>

      <div className="p-6 space-y-4">
        {error && (
          <div className="flex items-center gap-2 text-xs text-red-400 bg-red-500/10 border border-red-500/20 rounded-lg px-3 py-2">
            <AlertTriangle size={14} />
            {error}
          </div>
        )}

        {loading ? (
          <Loading center label={t('common.loading')} />
        ) : items.length === 0 ? (
          <p className="text-xs text-ink-500">{t('agentMemories.empty')}</p>
        ) : (
          <ul className="space-y-2">
            {items.map((item) => (
              <li
                key={item.id}
                className="group bg-ink-950 border border-ink-800 rounded-lg px-4 py-3 space-y-1.5"
              >
                <div className="flex items-start gap-2">
                  <div className="flex-1 min-w-0">
                    <div className="flex items-center gap-2 flex-wrap">
                      <span className="text-sm font-semibold text-ink-200">{item.title}</span>
                      <span className="px-1.5 py-0.5 rounded text-[10px] font-medium bg-ink-800 text-ink-400">
                        {item.kind}
                      </span>
                      {item.is_owner && item.visibility === 'private' ? (
                        <span className="px-1.5 py-0.5 rounded text-[10px] font-medium bg-blue-500/10 text-blue-400">
                          {t('agentMemories.personal')}
                        </span>
                      ) : item.visibility === 'shared' ? (
                        <span className="px-1.5 py-0.5 rounded text-[10px] font-medium bg-green-500/10 text-green-400">
                          {t('agentMemories.shared')}
                        </span>
                      ) : null}
                    </div>
                    {item.when_to_use && (
                      <p className="text-[11px] text-ink-500 mt-0.5">{item.when_to_use}</p>
                    )}
                    <p className="text-xs text-ink-300 mt-1 leading-relaxed">{item.body_md}</p>
                  </div>

                  <div className="shrink-0 pt-0.5">
                    {item.is_owner ? (
                      confirmDeleteId === item.id ? (
                        <div className="flex items-center gap-1.5">
                          <button
                            onClick={() => handleDelete(item.id)}
                            className="flex items-center gap-1 px-2 py-1 rounded text-[11px] font-medium bg-red-500/20 text-red-300 border border-red-500/40 hover:bg-red-500/30 transition-colors"
                          >
                            <Trash2 size={11} />
                            {t('agentMemories.deleteConfirm')}
                          </button>
                          <button
                            onClick={() => setConfirmDeleteId(null)}
                            className="p-1 text-ink-500 hover:text-ink-300 transition-colors"
                            aria-label={t('common.cancel')}
                          >
                            <X size={13} />
                          </button>
                        </div>
                      ) : (
                        <button
                          onClick={() => setConfirmDeleteId(item.id)}
                          title={t('agentMemories.delete')}
                          className="text-ink-600 hover:text-red-400 transition-colors opacity-0 group-hover:opacity-100"
                        >
                          <Trash2 size={13} />
                        </button>
                      )
                    ) : (
                      <span className="text-[10px] text-ink-600 italic">
                        {t('agentMemories.sharedByTeammate')}
                      </span>
                    )}
                  </div>
                </div>
              </li>
            ))}
          </ul>
        )}
      </div>
    </section>
  );
};

export default AgentMemoriesPanel;
