import React, { useState, useRef, useEffect, useCallback } from 'react';
import { X, Plus, Palette } from 'lucide-react';
import { useTranslation } from 'react-i18next';
import type { Tag } from '../types';

const TAG_COLORS = [
  '#ef4444', '#f97316', '#eab308', '#22c55e',
  '#14b8a6', '#3b82f6', '#8b5cf6', '#ec4899',
];

interface UnifiedTagPickerProps {
  /** Currently assigned tags */
  assignedTags: Tag[];
  /** All available tags (fetched by parent) */
  allTags: Tag[];
  /** Read-only mode (no add/remove) */
  readOnly?: boolean;
  /** Called when user adds a tag */
  onAdd: (tagId: string) => void;
  /** Called when user removes a tag */
  onRemove: (tagId: string) => void;
  /** Called when user creates a new tag; should return the created tag */
  onCreate?: (name: string, color: string) => Promise<Tag | null>;
}

export const UnifiedTagPicker: React.FC<UnifiedTagPickerProps> = ({
  assignedTags,
  allTags,
  readOnly = false,
  onAdd,
  onRemove,
  onCreate,
}) => {
  const { t, i18n } = useTranslation();
  const tagLabel = (tag: Tag) =>
    i18n.language === 'zh' && tag.name_zh ? tag.name_zh : tag.name;
  const [showDropdown, setShowDropdown] = useState(false);
  const [search, setSearch] = useState('');
  const [showCreate, setShowCreate] = useState(false);
  const [newColor, setNewColor] = useState(TAG_COLORS[5]); // default blue
  const dropdownRef = useRef<HTMLDivElement>(null);

  // Close dropdown on outside click
  useEffect(() => {
    const handleClickOutside = (e: MouseEvent) => {
      if (dropdownRef.current && !dropdownRef.current.contains(e.target as Node)) {
        setShowDropdown(false);
        setSearch('');
        setShowCreate(false);
      }
    };
    document.addEventListener('mousedown', handleClickOutside);
    return () => document.removeEventListener('mousedown', handleClickOutside);
  }, []);

  const assignedIds = new Set(assignedTags.map((tag) => String(tag.id)));
  const available = allTags.filter((tag) => !assignedIds.has(String(tag.id)));
  const filtered = search
    ? available.filter((tag) =>
        tag.name.toLowerCase().includes(search.toLowerCase()) ||
        (tag.name_zh && tag.name_zh.toLowerCase().includes(search.toLowerCase()))
      )
    : available;

  const handleCreate = useCallback(async () => {
    const name = search.trim();
    if (!name || !onCreate) return;
    const created = await onCreate(name, newColor);
    if (created) {
      onAdd(String(created.id));
      setSearch('');
      setShowCreate(false);
      setShowDropdown(false);
    }
  }, [search, newColor, onCreate, onAdd]);

  const noExactMatch = search.trim() && !allTags.some(
    (tag) => tag.name.toLowerCase() === search.trim().toLowerCase(),
  );

  return (
    <div className="px-4 mt-4 border-t border-zinc-800/60 pt-3">
      <h4 className="text-[11px] font-semibold text-zinc-500 uppercase tracking-widest mb-2">
        {t('resources.tags')}
      </h4>
      <div className="flex flex-wrap gap-1.5">
        {/* Assigned tags */}
        {assignedTags.map((tag) => (
          <span
            key={tag.id}
            className="group inline-flex items-center gap-1 px-2 py-0.5 text-xs rounded-full transition-opacity"
            style={{
              backgroundColor: (tag.color || '#6366f1') + '20',
              color: tag.color || '#6366f1',
            }}
          >
            {tagLabel(tag)}
            {!readOnly && (
              <button
                onClick={() => onRemove(String(tag.id))}
                className="opacity-0 group-hover:opacity-100 transition-opacity hover:text-white"
                title="Remove"
              >
                <X size={10} />
              </button>
            )}
          </span>
        ))}

        {/* Add tag button + dropdown */}
        {!readOnly && (
          <div className="relative" ref={dropdownRef}>
            <button
              onClick={() => { setShowDropdown(!showDropdown); setSearch(''); setShowCreate(false); }}
              className="inline-flex items-center gap-1 px-2 py-0.5 text-xs rounded-full bg-zinc-800 text-zinc-400 hover:text-zinc-200 hover:bg-zinc-700 transition-colors"
            >
              <Plus size={10} />
              {t('resources.addTag')}
            </button>

            {showDropdown && (
              <div className="absolute left-0 top-full mt-1 z-30 bg-zinc-900 border border-zinc-700 rounded-lg shadow-xl w-52 py-1">
                {/* Search */}
                <div className="px-2 pb-1">
                  <input
                    type="text"
                    value={search}
                    onChange={(e) => { setSearch(e.target.value); setShowCreate(false); }}
                    placeholder={t('resources.searchTags', 'Search tags...')}
                    className="w-full bg-zinc-800 border border-zinc-700/50 rounded px-2 py-1 text-xs text-zinc-200 placeholder-zinc-600 focus:outline-none focus:border-indigo-500/50"
                    autoFocus
                    onKeyDown={(e) => {
                      if (e.key === 'Enter' && noExactMatch && onCreate) {
                        e.preventDefault();
                        handleCreate();
                      }
                    }}
                  />
                </div>

                {/* Available tags list */}
                <div className="max-h-36 overflow-y-auto">
                  {filtered.map((tag) => (
                    <button
                      key={tag.id}
                      onClick={() => { onAdd(String(tag.id)); setShowDropdown(false); setSearch(''); }}
                      className="w-full flex items-center gap-2 px-3 py-1.5 text-xs text-zinc-400 hover:bg-zinc-800 hover:text-zinc-200 transition-colors"
                    >
                      <span
                        className="w-2.5 h-2.5 rounded-full shrink-0"
                        style={{ backgroundColor: tag.color || '#6366f1' }}
                      />
                      <span className="truncate">{tagLabel(tag)}</span>
                      {tag.type === 'system' && (
                        <span className="ml-auto text-[10px] text-zinc-600">system</span>
                      )}
                    </button>
                  ))}
                  {filtered.length === 0 && !noExactMatch && (
                    <p className="text-xs text-zinc-600 text-center py-2">
                      {t('resources.noTagsAvailable', 'No tags available')}
                    </p>
                  )}
                </div>

                {/* Create new tag */}
                {noExactMatch && onCreate && (
                  <div className="border-t border-zinc-800 mt-1 pt-1 px-2">
                    {!showCreate ? (
                      <button
                        onClick={() => setShowCreate(true)}
                        className="w-full flex items-center gap-2 px-1 py-1.5 text-xs text-indigo-400 hover:text-indigo-300 transition-colors"
                      >
                        <Plus size={10} />
                        Create &quot;{search.trim()}&quot;
                      </button>
                    ) : (
                      <div className="py-1 space-y-1.5">
                        <div className="flex items-center gap-1.5">
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
                          className="w-full py-1 text-xs bg-indigo-600 hover:bg-indigo-500 text-white rounded transition-colors"
                        >
                          Create &quot;{search.trim()}&quot;
                        </button>
                      </div>
                    )}
                  </div>
                )}
              </div>
            )}
          </div>
        )}
      </div>
    </div>
  );
};
