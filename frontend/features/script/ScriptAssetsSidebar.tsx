import { useState, useEffect, useCallback } from 'react';
import { useParams } from 'react-router-dom';
import {
  Globe, User, MapPin, Package, Milestone,
  Plus, Trash2, ChevronDown, ChevronRight, Edit2, Check, X,
  Sparkles, Download, Copy, PanelLeftClose,
} from 'lucide-react';
import {
  fetchScriptAssets,
  createScriptAsset,
  updateScriptAsset,
  deleteScriptAsset,
} from '../../services/scriptService';
import { useScriptCanvasStore } from '../../stores/scriptCanvasStore';
import type { ScriptAsset, ScriptAssetType } from '../../types';

const ASSET_CATEGORIES: { type: ScriptAssetType; label: string; icon: typeof Globe }[] = [
  { type: 'worldview', label: 'Worldview', icon: Globe },
  { type: 'character', label: 'Characters', icon: User },
  { type: 'location', label: 'Locations', icon: MapPin },
  { type: 'prop', label: 'Props', icon: Package },
  { type: 'plot_point', label: 'Plot Points', icon: Milestone },
];

interface Props {
  collapsed?: boolean;
  onToggle?: () => void;
  onExport?: () => void;
  onNavigateToChapter?: (nodeId: string) => void;
}

export function ScriptAssetsSidebar({ collapsed, onToggle, onExport, onNavigateToChapter }: Props) {
  const { scriptId } = useParams<{ scriptId: string }>();
  const [assets, setAssets] = useState<ScriptAsset[]>([]);
  const nodes = useScriptCanvasStore((s) => s.nodes);
  const chapterNodes = nodes.filter((n) => n.type === 'chapterNode');
  const [chaptersExpanded, setChaptersExpanded] = useState(true);
  const [expandedTypes, setExpandedTypes] = useState<Set<ScriptAssetType>>(new Set(['character']));
  const [editingId, setEditingId] = useState<string | null>(null);
  const [editContent, setEditContent] = useState('');
  const [creatingType, setCreatingType] = useState<ScriptAssetType | null>(null);
  const [newAssetName, setNewAssetName] = useState('');

  const loadAssets = useCallback(async () => {
    if (!scriptId) return;
    try {
      const result = await fetchScriptAssets(scriptId);
      setAssets(result);
    } catch (err) {
      console.error('Failed to load script assets:', err);
    }
  }, [scriptId]);

  useEffect(() => { loadAssets(); }, [loadAssets]);

  const toggleType = (type: ScriptAssetType) => {
    setExpandedTypes((prev) => {
      const next = new Set(prev);
      if (next.has(type)) next.delete(type);
      else next.add(type);
      return next;
    });
  };

  const handleStartCreate = (type: ScriptAssetType) => {
    setCreatingType(type);
    setNewAssetName('');
  };

  const handleConfirmCreate = async () => {
    if (!scriptId || !creatingType || !newAssetName.trim()) return;
    try {
      await createScriptAsset({ script_id: scriptId, asset_type: creatingType, name: newAssetName.trim() });
      setCreatingType(null);
      setNewAssetName('');
      await loadAssets();
    } catch (err) {
      console.error('Failed to create asset:', err);
    }
  };

  const handleCancelCreate = () => {
    setCreatingType(null);
    setNewAssetName('');
  };

  const handleDelete = async (assetId: string) => {
    if (!window.confirm('Delete this asset?')) return;
    try {
      await deleteScriptAsset(assetId);
      setAssets((prev) => prev.filter((a) => a.id !== assetId));
    } catch (err) {
      console.error('Failed to delete asset:', err);
    }
  };

  const handleSaveContent = async (assetId: string) => {
    try {
      await updateScriptAsset(assetId, { content: editContent });
      setAssets((prev) =>
        prev.map((a) => (a.id === assetId ? { ...a, content: editContent } : a)),
      );
      setEditingId(null);
    } catch (err) {
      console.error('Failed to update asset:', err);
    }
  };

  if (collapsed) {
    return (
      <div className="w-10 border-r border-zinc-800 bg-zinc-900 flex flex-col items-center py-3 gap-2">
        <button
          onClick={onToggle}
          className="p-1.5 text-zinc-500 hover:text-zinc-300 rounded hover:bg-zinc-800"
          title="Expand sidebar"
        >
          <PanelLeftClose size={14} />
        </button>
        {ASSET_CATEGORIES.map(({ type, icon: Icon }) => (
          <button
            key={type}
            onClick={onToggle}
            className="p-1.5 text-zinc-500 hover:text-zinc-300 rounded hover:bg-zinc-800"
            title={type}
          >
            <Icon size={14} />
          </button>
        ))}
      </div>
    );
  }

  return (
    <div className="w-64 border-r border-zinc-800 bg-zinc-900 flex flex-col overflow-hidden">
      <div className="px-3 py-2 border-b border-zinc-800 flex items-center justify-between">
        <h3 className="text-xs font-semibold text-zinc-400 uppercase tracking-wider">Assets</h3>
        <div className="flex items-center gap-0.5">
          <button
            onClick={() => {
              try {
                navigator.clipboard.writeText(
                  chapterNodes.map((n) => `${n.data.chapterNumber}. ${n.data.title}\n${n.data.summary}`).join('\n\n'),
                );
              } catch (err) {
                console.error('Copy failed:', err);
              }
            }}
            className="p-1 text-zinc-500 hover:text-zinc-300 rounded hover:bg-zinc-800"
            title="Copy outline"
          >
            <Copy size={13} />
          </button>
          <button
            onClick={onExport}
            className="p-1 text-zinc-500 hover:text-zinc-300 rounded hover:bg-zinc-800"
            title="Export script"
          >
            <Download size={13} />
          </button>
          <button
            className="p-1 text-zinc-500 hover:text-indigo-400 rounded hover:bg-zinc-800"
            title="AI suggestions"
          >
            <Sparkles size={13} />
          </button>
          {onToggle && (
            <button
              onClick={onToggle}
              className="p-1 text-zinc-500 hover:text-zinc-300 rounded hover:bg-zinc-800"
              title="Collapse sidebar"
            >
              <PanelLeftClose size={13} />
            </button>
          )}
        </div>
      </div>

      <div className="flex-1 overflow-y-auto py-1">
        {/* Chapters / Story Outline group */}
        <div className="mb-0.5">
          <div
            role="button"
            tabIndex={0}
            onClick={() => setChaptersExpanded((v) => !v)}
            onKeyDown={(e) => { if (e.key === 'Enter' || e.key === ' ') setChaptersExpanded((v) => !v); }}
            className="w-full flex items-center gap-2 px-3 py-1.5 text-xs font-medium text-zinc-400 hover:text-zinc-200 hover:bg-zinc-800/50 cursor-pointer"
          >
            {chaptersExpanded ? <ChevronDown size={12} /> : <ChevronRight size={12} />}
            <Sparkles size={12} className="text-indigo-400" />
            <span className="flex-1 text-left">Story Outline</span>
            <span className="text-[10px] text-zinc-600">{chapterNodes.length}</span>
            <button
              onClick={(e) => { e.stopPropagation(); }}
              className="p-0.5 text-zinc-600 hover:text-indigo-400 rounded"
              title="Add chapter"
            >
              <Plus size={11} />
            </button>
          </div>

          {chaptersExpanded && chapterNodes.length > 0 && (
            <div className="ml-4 border-l border-zinc-800">
              {chapterNodes.map((node) => {
                const { title, chapterNumber, branchLabel } = node.data;
                const isBranch = Boolean(branchLabel);

                return (
                  <div key={node.id} className={isBranch ? 'ml-3' : ''}>
                    <button
                      onClick={() => onNavigateToChapter?.(node.id)}
                      className="w-full flex items-center gap-1.5 px-3 py-1.5 group hover:bg-zinc-800/50"
                    >
                      {isBranch && (
                        <Sparkles size={10} className="text-amber-400 shrink-0" />
                      )}
                      <span className="text-[10px] text-zinc-600 shrink-0 w-4 text-right">
                        {chapterNumber}.
                      </span>
                      <span className={`text-xs flex-1 truncate text-left group-hover:text-zinc-200 transition-colors ${isBranch ? 'text-amber-400/80' : 'text-zinc-300'}`}>
                        {title || `Chapter ${chapterNumber}`}
                      </span>
                    </button>
                  </div>
                );
              })}
            </div>
          )}

          {chaptersExpanded && chapterNodes.length === 0 && (
            <p className="ml-4 px-3 py-1 text-[10px] text-zinc-600 italic">No chapters yet</p>
          )}
        </div>

        {ASSET_CATEGORIES.map(({ type, label, icon: Icon }) => {
          const items = assets.filter((a) => a.asset_type === type);
          const isExpanded = expandedTypes.has(type);

          return (
            <div key={type} className="mb-0.5">
              <div
                role="button"
                tabIndex={0}
                onClick={() => toggleType(type)}
                onKeyDown={(e) => { if (e.key === 'Enter' || e.key === ' ') toggleType(type); }}
                className="w-full flex items-center gap-2 px-3 py-1.5 text-xs font-medium text-zinc-400 hover:text-zinc-200 hover:bg-zinc-800/50 cursor-pointer"
              >
                {isExpanded ? <ChevronDown size={12} /> : <ChevronRight size={12} />}
                <Icon size={12} />
                <span className="flex-1 text-left">{label}</span>
                <span className="text-[10px] text-zinc-600">{items.length}</span>
                <button
                  onClick={(e) => { e.stopPropagation(); handleStartCreate(type); }}
                  className="p-0.5 text-zinc-600 hover:text-indigo-400 rounded"
                  title={`Add ${label}`}
                >
                  <Plus size={11} />
                </button>
              </div>

              {isExpanded && creatingType === type && (
                <div className="ml-4 px-3 py-1.5 flex items-center gap-1">
                  <input
                    type="text"
                    className="flex-1 bg-zinc-800 text-xs text-zinc-300 rounded px-2 py-1 outline-none focus:ring-1 focus:ring-indigo-500/50"
                    placeholder={`New ${label.toLowerCase()} name...`}
                    value={newAssetName}
                    onChange={(e) => setNewAssetName(e.target.value)}
                    onKeyDown={(e) => {
                      if (e.key === 'Enter') handleConfirmCreate();
                      if (e.key === 'Escape') handleCancelCreate();
                    }}
                    autoFocus
                  />
                  <button
                    onClick={handleConfirmCreate}
                    className="p-0.5 text-green-500 hover:text-green-400"
                    disabled={!newAssetName.trim()}
                  >
                    <Check size={11} />
                  </button>
                  <button
                    onClick={handleCancelCreate}
                    className="p-0.5 text-zinc-500 hover:text-zinc-300"
                  >
                    <X size={11} />
                  </button>
                </div>
              )}

              {isExpanded && items.length > 0 && (
                <div className="ml-4 border-l border-zinc-800">
                  {items.map((asset) => (
                    <div key={asset.id} className="px-3 py-1.5 group">
                      <div className="flex items-center gap-1.5">
                        <span className="text-xs text-zinc-300 flex-1 truncate">{asset.name}</span>
                        <button
                          onClick={() => {
                            setEditingId(asset.id);
                            setEditContent(asset.content ?? '');
                          }}
                          className="hidden group-hover:block p-0.5 text-zinc-600 hover:text-zinc-300"
                        >
                          <Edit2 size={10} />
                        </button>
                        <button
                          onClick={() => handleDelete(asset.id)}
                          className="hidden group-hover:block p-0.5 text-zinc-600 hover:text-red-400"
                        >
                          <Trash2 size={10} />
                        </button>
                      </div>

                      {editingId === asset.id ? (
                        <div className="mt-1">
                          <textarea
                            className="w-full bg-zinc-800 text-[11px] text-zinc-300 rounded px-2 py-1 resize-none outline-none focus:ring-1 focus:ring-indigo-500/50 min-h-[48px]"
                            value={editContent}
                            onChange={(e) => setEditContent(e.target.value)}
                            autoFocus
                          />
                          <div className="flex gap-1 mt-1">
                            <button
                              onClick={() => handleSaveContent(asset.id)}
                              className="p-0.5 text-green-500 hover:text-green-400"
                            >
                              <Check size={11} />
                            </button>
                            <button
                              onClick={() => setEditingId(null)}
                              className="p-0.5 text-zinc-500 hover:text-zinc-300"
                            >
                              <X size={11} />
                            </button>
                          </div>
                        </div>
                      ) : asset.content ? (
                        <p className="text-[10px] text-zinc-500 mt-0.5 line-clamp-2">{asset.content}</p>
                      ) : null}
                    </div>
                  ))}
                </div>
              )}

              {isExpanded && items.length === 0 && (
                <p className="ml-4 px-3 py-1 text-[10px] text-zinc-600 italic">No {label.toLowerCase()} yet</p>
              )}
            </div>
          );
        })}
      </div>
    </div>
  );
}
