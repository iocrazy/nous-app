import React, { useState, useRef, useEffect, useCallback, useMemo } from 'react';
import { createPortal } from 'react-dom';
import { Search, Settings, X, Plus, Palette } from 'lucide-react';
import { useTranslation } from 'react-i18next';
import { CategorySidebar } from './CategorySidebar';
import { TagContent } from './TagContent';
import { SettingsPopover } from './SettingsPopover';
import type { Tag } from '../../types';
import type { PickerSettings, PanelSize } from '../../services/tagPreferencesService';

const TAG_COLORS = [
  '#ef4444', '#f97316', '#eab308', '#22c55e',
  '#14b8a6', '#3b82f6', '#8b5cf6', '#ec4899',
];

interface FloatingPanelProps {
  triggerRef: React.RefObject<HTMLElement | null>;
  allTags: Tag[];
  selectedIds: Set<string>;
  starredIds: string[];
  settings: PickerSettings;
  panelSize: PanelSize;
  onToggleTag: (tagId: string) => void;
  onToggleStar: (tagId: string) => void;
  onUpdateSettings: (partial: Partial<PickerSettings>) => void;
  onPanelResize: (size: PanelSize) => void;
  onClose: () => void;
  onCreate?: (name: string, color: string) => Promise<Tag | null>;
}

export const FloatingPanel: React.FC<FloatingPanelProps> = ({
  triggerRef,
  allTags,
  selectedIds,
  starredIds,
  settings,
  panelSize,
  onToggleTag,
  onToggleStar,
  onUpdateSettings,
  onPanelResize,
  onClose,
  onCreate,
}) => {
  const { t } = useTranslation();
  const panelRef = useRef<HTMLDivElement>(null);
  const [search, setSearch] = useState('');
  const [selectedGroup, setSelectedGroup] = useState<string | null>(null);
  const [showSettings, setShowSettings] = useState(false);
  const [size, setSize] = useState(panelSize);
  const [showColorPicker, setShowColorPicker] = useState(false);
  const [newColor, setNewColor] = useState(TAG_COLORS[5]);
  const [position, setPosition] = useState({ top: 0, left: 0 });
  const resizeRef = useRef<{ startX: number; startY: number; startW: number; startH: number } | null>(null);
  const debounceRef = useRef<ReturnType<typeof setTimeout>>(undefined);

  // Position panel to the left of trigger
  useEffect(() => {
    const trigger = triggerRef.current;
    if (!trigger) return;
    const rect = trigger.getBoundingClientRect();
    const panelW = size.width;
    const panelH = size.height;

    let left = rect.left - panelW - 8;
    let top = rect.top;

    // Fallback: if not enough space on left, try right
    if (left < 8) {
      left = rect.right + 8;
    }
    // Fallback: if not enough space on right either, center
    if (left + panelW > window.innerWidth - 8) {
      left = Math.max(8, (window.innerWidth - panelW) / 2);
    }
    // Vertical bounds
    if (top + panelH > window.innerHeight - 8) {
      top = Math.max(8, window.innerHeight - panelH - 8);
    }

    setPosition({ top, left });
  }, [triggerRef, size.width, size.height]);

  // Close on ESC
  useEffect(() => {
    const handler = (e: KeyboardEvent) => { if (e.key === 'Escape') onClose(); };
    document.addEventListener('keydown', handler);
    return () => document.removeEventListener('keydown', handler);
  }, [onClose]);

  // Close on click outside
  useEffect(() => {
    const handler = (e: MouseEvent) => {
      if (panelRef.current && !panelRef.current.contains(e.target as Node)) {
        onClose();
      }
    };
    // Use setTimeout to avoid closing immediately from the same click that opened
    const timer = setTimeout(() => document.addEventListener('mousedown', handler), 0);
    return () => { clearTimeout(timer); document.removeEventListener('mousedown', handler); };
  }, [onClose]);

  // Resize handlers
  const handleResizeStart = useCallback((e: React.MouseEvent) => {
    e.preventDefault();
    resizeRef.current = { startX: e.clientX, startY: e.clientY, startW: size.width, startH: size.height };
    const handleMove = (me: MouseEvent) => {
      if (!resizeRef.current) return;
      const newW = Math.min(1200, Math.max(300, resizeRef.current.startW + me.clientX - resizeRef.current.startX));
      const newH = Math.min(800, Math.max(250, resizeRef.current.startH + me.clientY - resizeRef.current.startY));
      setSize({ width: newW, height: newH });
    };
    const handleUp = () => {
      document.removeEventListener('mousemove', handleMove);
      document.removeEventListener('mouseup', handleUp);
      resizeRef.current = null;
      // Debounced persist
      if (debounceRef.current) clearTimeout(debounceRef.current);
      debounceRef.current = setTimeout(() => {
        setSize((s) => { onPanelResize(s); return s; });
      }, 500);
    };
    document.addEventListener('mousemove', handleMove);
    document.addEventListener('mouseup', handleUp);
  }, [size, onPanelResize]);

  // Compute group info
  const { groups, totalCount, uncategorizedCount } = useMemo(() => {
    const groupMap = new Map<string, number>();
    let uncat = 0;
    for (const tag of allTags) {
      if (tag.group_name) {
        groupMap.set(tag.group_name, (groupMap.get(tag.group_name) || 0) + 1);
      } else {
        uncat++;
      }
    }
    return {
      groups: Array.from(groupMap.entries()).map(([name, count]) => ({ name, count })),
      totalCount: allTags.length,
      uncategorizedCount: uncat,
    };
  }, [allTags]);

  // Check if search term has no exact match (for create option)
  const noExactMatch = useMemo(() => {
    if (!search.trim()) return false;
    const q = search.trim().toLowerCase();
    return !allTags.some((t) => t.name.toLowerCase() === q);
  }, [search, allTags]);

  const handleCreate = useCallback(async () => {
    const name = search.trim();
    if (!name || !onCreate) return;
    const created = await onCreate(name, newColor);
    if (created) {
      setSearch('');
      setShowColorPicker(false);
    }
  }, [search, newColor, onCreate]);

  return createPortal(
    <div
      ref={panelRef}
      className="fixed z-[60] bg-zinc-900 border border-zinc-700 rounded-xl shadow-2xl flex flex-col overflow-hidden"
      style={{ top: position.top, left: position.left, width: size.width, height: size.height }}
    >
      {/* Top bar: search + settings */}
      <div className="flex items-center gap-2 px-3 py-2 border-b border-zinc-800">
        <div className="flex-1 relative">
          <Search size={12} className="absolute left-2 top-1/2 -translate-y-1/2 text-zinc-500" />
          <input
            type="text"
            value={search}
            onChange={(e) => setSearch(e.target.value)}
            placeholder={t('resources.searchTags', 'Search tags...')}
            className="w-full bg-zinc-800 border border-zinc-700/50 rounded pl-7 pr-2 py-1.5 text-xs text-zinc-200 placeholder-zinc-600 focus:outline-none focus:border-indigo-500/50"
            autoFocus
            onKeyDown={(e) => {
              if (e.key === 'Enter' && noExactMatch && onCreate) {
                e.preventDefault();
                handleCreate();
              }
            }}
          />
        </div>
        <div className="relative">
          <button
            onClick={() => setShowSettings(!showSettings)}
            className="p-1.5 rounded text-zinc-400 hover:text-zinc-200 hover:bg-zinc-800 transition-colors"
          >
            <Settings size={14} />
          </button>
          {showSettings && (
            <SettingsPopover settings={settings} onUpdate={onUpdateSettings} onClose={() => setShowSettings(false)} />
          )}
        </div>
        <button
          onClick={onClose}
          className="p-1.5 rounded text-zinc-400 hover:text-zinc-200 hover:bg-zinc-800 transition-colors"
        >
          <X size={14} />
        </button>
      </div>

      {/* Create tag option (Eagle-style: shown at top when search has no exact match) */}
      {noExactMatch && onCreate && (
        <div className="border-b border-zinc-800 px-3 py-1.5">
          {!showColorPicker ? (
            <button
              onClick={() => setShowColorPicker(true)}
              className="flex items-center gap-2 w-full px-2 py-1.5 text-xs text-zinc-300 hover:bg-zinc-800 rounded transition-colors"
            >
              <Plus size={12} className="text-indigo-400" />
              <span>Create &quot;{search.trim()}&quot;</span>
            </button>
          ) : (
            <div className="space-y-1.5 py-1">
              <div className="flex items-center gap-2">
                <Palette size={10} className="text-zinc-500 shrink-0" />
                <div className="flex gap-1">
                  {TAG_COLORS.map((c) => (
                    <button
                      key={c}
                      onClick={() => setNewColor(c)}
                      className={`w-4 h-4 rounded-full border-2 transition-all ${
                        newColor === c ? 'border-white scale-110' : 'border-transparent'
                      }`}
                      style={{ backgroundColor: c }}
                    />
                  ))}
                </div>
              </div>
              <button
                onClick={handleCreate}
                className="w-full py-1.5 text-xs bg-indigo-600 hover:bg-indigo-500 text-white rounded transition-colors"
              >
                Create &quot;{search.trim()}&quot;
              </button>
            </div>
          )}
        </div>
      )}

      {/* Main content: sidebar + tags */}
      <div className="flex flex-1 min-h-0">
        <CategorySidebar
          totalCount={totalCount}
          uncategorizedCount={uncategorizedCount}
          groups={groups}
          selectedGroup={selectedGroup}
          onSelectGroup={setSelectedGroup}
        />
        <TagContent
          allTags={allTags}
          selectedIds={selectedIds}
          starredIds={starredIds}
          settings={settings}
          selectedGroup={selectedGroup}
          search={search}
          onToggleTag={onToggleTag}
          onToggleStar={onToggleStar}
        />
      </div>

      {/* Resize handle */}
      <div
        onMouseDown={handleResizeStart}
        className="absolute bottom-0 right-0 w-4 h-4 cursor-se-resize"
        style={{ background: 'linear-gradient(135deg, transparent 50%, rgba(113,113,122,0.4) 50%)' }}
      />
    </div>,
    document.body,
  );
};
