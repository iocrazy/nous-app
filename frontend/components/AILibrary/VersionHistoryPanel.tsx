/**
 * Phase 3 — Version history panel for AI Library entities.
 *
 * List newest-first snapshots from *_versions tables (mig 141).
 * Click a row to expand metadata (model / temp / max_tokens for
 * agents). Rollback button creates a new version with the old body
 * via POST /agents/{slug}/rollback/{version_number}.
 *
 * Used as a tab in AgentEditor / SkillEditor.
 */
import React, { useCallback, useEffect, useState } from 'react';
import { useTranslation } from 'react-i18next';
import { History, RefreshCw, RotateCcw, ChevronDown, ChevronRight } from 'lucide-react';
import { aiLibraryService } from '../../services/aiLibraryService';
import { useToast } from '../Toast';
import type { AILibraryVersionItem } from '../../types';

interface VersionHistoryPanelProps {
  /** What kind of entity we're listing versions for. */
  kind: 'agent' | 'skill';
  /** Slug of the entity (agent slug or skill slug). */
  slug: string;
  /** Optional callback after successful rollback so parent can refetch. */
  onRollback?: (newVersion: number) => void;
}

function _fmtTime(iso: string | null): string {
  if (!iso) return '—';
  return new Date(iso).toLocaleString();
}

export const VersionHistoryPanel: React.FC<VersionHistoryPanelProps> = ({
  kind,
  slug,
  onRollback,
}) => {
  const { t } = useTranslation();
  const { addToast } = useToast();
  const [items, setItems] = useState<AILibraryVersionItem[]>([]);
  const [currentVersion, setCurrentVersion] = useState<number | null>(null);
  const [loading, setLoading] = useState(true);
  const [expandedId, setExpandedId] = useState<string | null>(null);
  const [rollingBack, setRollingBack] = useState<number | null>(null);

  const reload = useCallback(async () => {
    setLoading(true);
    try {
      const resp =
        kind === 'agent'
          ? await aiLibraryService.listAgentVersions(slug)
          : await aiLibraryService.listSkillVersions(slug);
      setItems(resp.items);
      setCurrentVersion(resp.current_version);
    } catch (err) {
      console.error('[VersionHistoryPanel] load failed:', err);
      addToast(t('versionHistory.loadFailed'), 'error');
    } finally {
      setLoading(false);
    }
  }, [kind, slug, addToast, t]);

  useEffect(() => {
    void reload();
  }, [reload]);

  const handleRollback = useCallback(
    async (versionNumber: number) => {
      if (kind !== 'agent') {
        addToast(t('versionHistory.rollbackNotSupported'), 'info');
        return;
      }
      if (!window.confirm(t('versionHistory.rollbackConfirm', { version: versionNumber }))) {
        return;
      }
      setRollingBack(versionNumber);
      try {
        const result = await aiLibraryService.rollbackAgent(slug, versionNumber);
        addToast(t('versionHistory.rollbackDone'), 'success');
        const newV = (result.new_version as number | undefined) ?? null;
        if (newV !== null) onRollback?.(newV);
        await reload();
      } catch (err) {
        const msg = err instanceof Error ? err.message : String(err);
        addToast(t('versionHistory.rollbackFailed', { message: msg }), 'error');
      } finally {
        setRollingBack(null);
      }
    },
    [kind, slug, onRollback, addToast, reload, t],
  );

  return (
    <div className="flex flex-col h-full">
      {/* Header */}
      <div className="flex items-center justify-between px-3 py-2 border-b border-zinc-800">
        <div className="flex items-center gap-2 text-xs font-semibold text-zinc-300">
          <History className="w-3.5 h-3.5 text-blue-400" />
          {t('versionHistory.title')}
          {currentVersion !== null && (
            <span className="px-1.5 py-0.5 rounded bg-blue-600/20 text-blue-300 text-[10px]">
              v{currentVersion} {t('versionHistory.currentLabel')}
            </span>
          )}
        </div>
        <button
          type="button"
          onClick={reload}
          className="p-1 rounded text-zinc-500 hover:text-zinc-300 hover:bg-zinc-800 transition-colors"
          title={t('versionHistory.refresh')}
        >
          <RefreshCw className={`w-3 h-3 ${loading ? 'animate-spin' : ''}`} />
        </button>
      </div>

      {/* List */}
      <div className="flex-1 overflow-y-auto">
        {loading && items.length === 0 ? (
          <div className="px-3 py-4 text-xs text-zinc-500 text-center">
            {t('versionHistory.loading')}
          </div>
        ) : items.length === 0 ? (
          <div className="px-3 py-6 text-xs text-zinc-500 text-center">
            <History className="w-6 h-6 mx-auto mb-2 opacity-30" />
            {t('versionHistory.empty')}
          </div>
        ) : (
          <ul className="divide-y divide-zinc-800">
            {items.map((v) => {
              const isCurrent = v.version_number === currentVersion;
              const isExpanded = expandedId === v.id;
              return (
                <li
                  key={v.id}
                  className={`px-3 py-2 hover:bg-zinc-900/50 transition-colors ${
                    isCurrent ? 'bg-blue-950/20' : ''
                  }`}
                >
                  <div className="flex items-start gap-2">
                    <button
                      type="button"
                      onClick={() => setExpandedId(isExpanded ? null : v.id)}
                      className="text-zinc-500 hover:text-zinc-300 mt-0.5"
                      title={isExpanded ? t('versionHistory.collapse') : t('versionHistory.expand')}
                    >
                      {isExpanded ? (
                        <ChevronDown className="w-3 h-3" />
                      ) : (
                        <ChevronRight className="w-3 h-3" />
                      )}
                    </button>
                    <div className="flex-1 min-w-0">
                      <div className="flex items-center gap-2 text-xs">
                        <span className="font-semibold text-zinc-200">
                          v{v.version_number}
                        </span>
                        {isCurrent && (
                          <span className="text-[10px] text-blue-400">
                            {t('versionHistory.currentLabel')}
                          </span>
                        )}
                        {v.notes && (
                          <span className="text-zinc-400 truncate italic">
                            {v.notes}
                          </span>
                        )}
                      </div>
                      <div className="text-[10px] text-zinc-500 mt-0.5">
                        {_fmtTime(v.created_at)}
                      </div>
                      {isExpanded && kind === 'agent' && (
                        <div className="mt-2 grid grid-cols-3 gap-2 text-[10px] text-zinc-500 bg-zinc-950/50 rounded p-2">
                          <div>
                            <div className="text-zinc-600">
                              {t('aiLibrary.agents.modelLabel', 'Model')}
                            </div>
                            <div className="text-zinc-300">{v.model ?? '—'}</div>
                          </div>
                          <div>
                            <div className="text-zinc-600">
                              {t('aiLibrary.agents.temperatureLabel', 'Temperature')}
                            </div>
                            <div className="text-zinc-300">{v.temperature ?? '—'}</div>
                          </div>
                          <div>
                            <div className="text-zinc-600">
                              {t('aiLibrary.agents.maxTokensLabel', 'Max tokens')}
                            </div>
                            <div className="text-zinc-300">{v.max_tokens ?? '—'}</div>
                          </div>
                        </div>
                      )}
                    </div>
                    {kind === 'agent' && !isCurrent && (
                      <button
                        type="button"
                        disabled={rollingBack === v.version_number}
                        onClick={() => handleRollback(v.version_number)}
                        className="flex items-center gap-1 px-2 py-1 text-xs rounded text-zinc-400 hover:text-blue-300 hover:bg-blue-950/30 transition-colors disabled:opacity-40"
                        title={t('versionHistory.rollback')}
                      >
                        <RotateCcw className="w-3 h-3" />
                        {t('versionHistory.rollback')}
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

export default VersionHistoryPanel;
