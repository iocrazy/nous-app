import React, { useState, useEffect, useCallback, useRef } from 'react';
import { Cookie, Upload, Trash2, AlertCircle, Check, Loader2, ChevronDown } from 'lucide-react';
import { fetchCookieStatuses, setCookie, deleteCookie, CookieStatus } from '../services/cookiesService';
import { useToast } from './Toast';

interface PlatformConfig {
  id: string;
  name: string;
  icon: string; // path to SVG in /icons/
}

const PLATFORMS: PlatformConfig[] = [
  { id: 'douyin', name: 'Douyin', icon: '/icons/douyin.svg' },
  { id: 'bilibili', name: 'Bilibili', icon: '/icons/bilibili.svg' },
  { id: 'youtube', name: 'YouTube', icon: '/icons/youtube.svg' },
];

type InputTab = 'paste' | 'upload';

interface CardState {
  expanded: boolean;
  activeTab: InputTab;
  pasteValue: string;
  fileContent: string | null;
  fileName: string | null;
  isDragging: boolean;
  saving: boolean;
  deleting: boolean;
}

const DEFAULT_CARD_STATE: CardState = {
  expanded: false,
  activeTab: 'paste',
  pasteValue: '',
  fileContent: null,
  fileName: null,
  isDragging: false,
  saving: false,
  deleting: false,
};

function formatUpdatedAt(isoString: string): string {
  const date = new Date(isoString);
  return date.toLocaleString(undefined, {
    year: 'numeric',
    month: 'short',
    day: 'numeric',
    hour: '2-digit',
    minute: '2-digit',
  });
}

function StatusBadge({ status }: { status: CookieStatus | undefined }) {
  if (!status || !status.has_cookie) {
    return (
      <span className="inline-flex items-center gap-1.5 px-2.5 py-1 rounded-full text-xs font-medium bg-zinc-800 text-zinc-400 border border-zinc-700">
        Not configured
      </span>
    );
  }
  if (!status.is_valid) {
    return (
      <span className="inline-flex items-center gap-1.5 px-2.5 py-1 rounded-full text-xs font-medium bg-red-500/10 text-red-400 border border-red-500/20">
        <AlertCircle size={11} />
        Invalid
      </span>
    );
  }
  return (
    <span className="inline-flex items-center gap-1.5 px-2.5 py-1 rounded-full text-xs font-medium bg-green-500/10 text-green-400 border border-green-500/20">
      <Check size={11} />
      Configured
    </span>
  );
}

export const CookiesSettings: React.FC = () => {
  const { addToast } = useToast();
  const [statuses, setStatuses] = useState<CookieStatus[]>([]);
  const [loading, setLoading] = useState(true);
  const [loadError, setLoadError] = useState<string | null>(null);

  const [cardStates, setCardStates] = useState<Record<string, CardState>>(
    () => Object.fromEntries(PLATFORMS.map(p => [p.id, { ...DEFAULT_CARD_STATE }]))
  );

  const fileInputRefs = useRef<Record<string, HTMLInputElement | null>>({});

  const loadStatuses = useCallback(async () => {
    setLoading(true);
    setLoadError(null);
    try {
      const data = await fetchCookieStatuses();
      setStatuses(data);
    } catch (err) {
      console.error('Failed to load cookie statuses:', err);
      setLoadError(err instanceof Error ? err.message : 'Failed to load cookie statuses');
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    loadStatuses();
  }, [loadStatuses]);

  const updateCard = (platformId: string, patch: Partial<CardState>) => {
    setCardStates(prev => ({
      ...prev,
      [platformId]: { ...prev[platformId], ...patch },
    }));
  };

  const toggleExpand = (platformId: string) => {
    setCardStates(prev => ({
      ...prev,
      [platformId]: { ...prev[platformId], expanded: !prev[platformId].expanded },
    }));
  };

  const handleFileChange = (platformId: string, file: File | null) => {
    if (!file) return;
    const reader = new FileReader();
    reader.onload = (e) => {
      const content = e.target?.result as string;
      updateCard(platformId, { fileContent: content, fileName: file.name });
    };
    reader.readAsText(file);
  };

  const handleDrop = (platformId: string, e: React.DragEvent) => {
    e.preventDefault();
    updateCard(platformId, { isDragging: false });
    const file = e.dataTransfer.files[0];
    if (file) handleFileChange(platformId, file);
  };

  const handleSave = async (platformId: string) => {
    const card = cardStates[platformId];
    const cookieText = card.activeTab === 'paste' ? card.pasteValue.trim() : (card.fileContent ?? '');

    if (!cookieText) {
      addToast('Please provide cookie content before saving.', 'error');
      return;
    }

    updateCard(platformId, { saving: true });
    try {
      await setCookie(platformId, cookieText);
      addToast(`${PLATFORMS.find(p => p.id === platformId)?.name} cookie saved successfully.`, 'success');
      updateCard(platformId, {
        expanded: false,
        pasteValue: '',
        fileContent: null,
        fileName: null,
      });
      await loadStatuses();
    } catch (err) {
      console.error('Failed to save cookie:', err);
      addToast(err instanceof Error ? err.message : 'Failed to save cookie', 'error');
    } finally {
      updateCard(platformId, { saving: false });
    }
  };

  const handleDelete = async (platformId: string) => {
    if (!confirm(`Remove the ${PLATFORMS.find(p => p.id === platformId)?.name} cookie?`)) return;

    updateCard(platformId, { deleting: true });
    try {
      await deleteCookie(platformId);
      addToast(`${PLATFORMS.find(p => p.id === platformId)?.name} cookie removed.`, 'success');
      updateCard(platformId, { expanded: false });
      await loadStatuses();
    } catch (err) {
      console.error('Failed to delete cookie:', err);
      addToast(err instanceof Error ? err.message : 'Failed to delete cookie', 'error');
    } finally {
      updateCard(platformId, { deleting: false });
    }
  };

  const getStatus = (platformId: string): CookieStatus | undefined =>
    statuses.find(s => s.platform === platformId);

  return (
    <div className="space-y-6 animate-in fade-in duration-300">
      {/* Header */}
      <div>
        <h2 className="text-base font-semibold text-white flex items-center gap-2">
          <Cookie size={18} className="text-indigo-400" />
          Cookie Management
        </h2>
        <p className="text-sm text-zinc-500 mt-1">
          Configure platform cookies for enhanced parsing quality
        </p>
      </div>

      {/* Loading state */}
      {loading && (
        <div className="flex items-center justify-center gap-3 py-16 text-zinc-500">
          <Loader2 size={20} className="animate-spin" />
          <span className="text-sm">Loading cookie statuses...</span>
        </div>
      )}

      {/* Error state */}
      {!loading && loadError && (
        <div className="p-4 bg-red-500/10 border border-red-500/20 rounded-xl flex items-center gap-3 text-red-400 text-sm">
          <AlertCircle size={18} className="flex-shrink-0" />
          <span className="flex-1">{loadError}</span>
          <button
            onClick={loadStatuses}
            className="text-xs underline hover:no-underline flex-shrink-0"
          >
            Retry
          </button>
        </div>
      )}

      {/* Platform cards grid */}
      {!loading && !loadError && (
        <div className="grid grid-cols-1 md:grid-cols-3 gap-4">
          {PLATFORMS.map((platform) => {
            const status = getStatus(platform.id);
            const card = cardStates[platform.id];

            return (
              <div
                key={platform.id}
                className="bg-zinc-900 border border-zinc-800 rounded-xl overflow-hidden transition-all duration-200"
              >
                {/* Card header — click to expand */}
                <button
                  onClick={() => toggleExpand(platform.id)}
                  className="w-full text-left px-4 py-4 flex items-start justify-between gap-3 hover:bg-zinc-800/40 transition-colors"
                >
                  <div className="flex items-center gap-3 min-w-0">
                    <img src={platform.icon} alt={platform.name} className="w-6 h-6 flex-shrink-0" />
                    <div className="min-w-0">
                      <p className="text-sm font-semibold text-white">{platform.name}</p>
                      <div className="mt-1.5">
                        <StatusBadge status={status} />
                      </div>
                      {status?.has_cookie && status.updated_at && (
                        <p className="text-xs text-zinc-600 mt-1.5">
                          Updated {formatUpdatedAt(status.updated_at)}
                        </p>
                      )}
                    </div>
                  </div>
                  <ChevronDown
                    size={16}
                    className={`flex-shrink-0 text-zinc-500 mt-0.5 transition-transform duration-200 ${card.expanded ? 'rotate-180' : ''}`}
                  />
                </button>

                {/* Invalid error message */}
                {status?.has_cookie && !status.is_valid && status.error_message && (
                  <div className="mx-4 mb-3 px-3 py-2.5 bg-red-500/10 border border-red-500/20 rounded-lg flex items-start gap-2 text-xs text-red-400">
                    <AlertCircle size={13} className="flex-shrink-0 mt-0.5" />
                    <span>{status.error_message}</span>
                  </div>
                )}

                {/* Expanded edit area */}
                {card.expanded && (
                  <div className="border-t border-zinc-800 px-4 pb-4 pt-3 space-y-3 animate-in fade-in slide-in-from-top-2 duration-200">
                    {/* Tabs */}
                    <div className="flex gap-1 bg-zinc-950/60 rounded-lg p-1">
                      {(['paste', 'upload'] as InputTab[]).map((tab) => (
                        <button
                          key={tab}
                          onClick={() => updateCard(platform.id, { activeTab: tab })}
                          className={`flex-1 py-1.5 text-xs font-medium rounded-md transition-colors capitalize ${
                            card.activeTab === tab
                              ? 'bg-zinc-700 text-white'
                              : 'text-zinc-500 hover:text-zinc-300'
                          }`}
                        >
                          {tab}
                        </button>
                      ))}
                    </div>

                    {/* Paste tab */}
                    {card.activeTab === 'paste' && (
                      <textarea
                        rows={5}
                        placeholder="Paste cookie string here..."
                        value={card.pasteValue}
                        onChange={(e) => updateCard(platform.id, { pasteValue: e.target.value })}
                        className="w-full bg-zinc-950 border border-zinc-800 rounded-lg px-3 py-2.5 text-xs text-zinc-300 placeholder-zinc-600 resize-none outline-none focus:border-indigo-500 transition-colors font-mono"
                      />
                    )}

                    {/* Upload tab */}
                    {card.activeTab === 'upload' && (
                      <div
                        onDragOver={(e) => { e.preventDefault(); updateCard(platform.id, { isDragging: true }); }}
                        onDragLeave={() => updateCard(platform.id, { isDragging: false })}
                        onDrop={(e) => handleDrop(platform.id, e)}
                        onClick={() => fileInputRefs.current[platform.id]?.click()}
                        className={`flex flex-col items-center justify-center gap-2 border-2 border-dashed rounded-lg py-6 cursor-pointer transition-colors text-center ${
                          card.isDragging
                            ? 'border-indigo-500 bg-indigo-500/10'
                            : 'border-zinc-700 hover:border-zinc-600 bg-zinc-950/40'
                        }`}
                      >
                        <Upload size={20} className="text-zinc-500" />
                        {card.fileName ? (
                          <p className="text-xs text-indigo-400 font-medium px-2 truncate max-w-full">
                            {card.fileName}
                          </p>
                        ) : (
                          <>
                            <p className="text-xs text-zinc-400">Drop a .txt file here</p>
                            <p className="text-xs text-zinc-600">or click to browse</p>
                          </>
                        )}
                        <input
                          ref={(el) => { fileInputRefs.current[platform.id] = el; }}
                          type="file"
                          accept=".txt"
                          className="hidden"
                          onChange={(e) => handleFileChange(platform.id, e.target.files?.[0] ?? null)}
                        />
                      </div>
                    )}

                    {/* Action buttons */}
                    <div className="flex items-center gap-2 pt-1">
                      <button
                        onClick={() => handleSave(platform.id)}
                        disabled={card.saving || card.deleting}
                        className="flex-1 flex items-center justify-center gap-1.5 px-3 py-2 bg-indigo-600 hover:bg-indigo-500 disabled:opacity-50 disabled:cursor-not-allowed text-white text-xs font-semibold rounded-lg transition-colors"
                      >
                        {card.saving ? (
                          <Loader2 size={13} className="animate-spin" />
                        ) : (
                          <Check size={13} />
                        )}
                        {card.saving ? 'Saving...' : 'Save'}
                      </button>

                      {status?.has_cookie && (
                        <button
                          onClick={() => handleDelete(platform.id)}
                          disabled={card.saving || card.deleting}
                          className="flex items-center justify-center gap-1.5 px-3 py-2 bg-red-500/10 hover:bg-red-500/20 disabled:opacity-50 disabled:cursor-not-allowed text-red-400 text-xs font-semibold rounded-lg border border-red-500/20 transition-colors"
                        >
                          {card.deleting ? (
                            <Loader2 size={13} className="animate-spin" />
                          ) : (
                            <Trash2 size={13} />
                          )}
                          {card.deleting ? 'Removing...' : 'Remove'}
                        </button>
                      )}
                    </div>
                  </div>
                )}
              </div>
            );
          })}
        </div>
      )}
    </div>
  );
};
