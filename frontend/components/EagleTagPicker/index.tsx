import React, { useState, useRef, useMemo, useCallback } from 'react';
import { Plus, Palette } from 'lucide-react';
import { useTranslation } from 'react-i18next';
import { TagPill } from './TagPill';
import { FloatingPanel } from './FloatingPanel';
import { useTagPreferences } from './useTagPreferences';
import type { EagleTagPickerProps } from './types';
import type { Tag } from '../../types';

const TAG_COLORS = [
  '#ef4444', '#f97316', '#eab308', '#22c55e',
  '#14b8a6', '#3b82f6', '#8b5cf6', '#ec4899',
];

export const EagleTagPicker: React.FC<EagleTagPickerProps> = ({
  assignedTags,
  onAdd,
  onRemove,
  selectedTagIds,
  onTagsChange,
  allTags,
  readOnly = false,
  onCreate,
}) => {
  const { t } = useTranslation();
  const [panelOpen, setPanelOpen] = useState(false);
  const [showCreate, setShowCreate] = useState(false);
  const [newName, setNewName] = useState('');
  const [newColor, setNewColor] = useState(TAG_COLORS[5]);
  const triggerRef = useRef<HTMLButtonElement>(null);
  const { prefs, toggleStar, updateSettings, updatePanelSize } = useTagPreferences();

  // Determine mode and selected IDs
  const isMode1 = assignedTags !== undefined;
  const selectedIds = useMemo(() => {
    if (isMode1) return new Set(assignedTags!.map((t) => String(t.id)));
    return new Set(selectedTagIds || []);
  }, [isMode1, assignedTags, selectedTagIds]);

  // Tags to display as pills (assigned in Mode 1, selected from allTags in Mode 2)
  const displayTags = useMemo(() => {
    if (isMode1) return assignedTags!;
    return allTags.filter((t) => selectedIds.has(String(t.id)));
  }, [isMode1, assignedTags, allTags, selectedIds]);

  const handleToggleTag = useCallback(
    (tagId: string) => {
      if (isMode1) {
        if (selectedIds.has(tagId)) {
          onRemove?.(tagId);
        } else {
          onAdd?.(tagId);
        }
      } else {
        const current = selectedTagIds || [];
        const next = current.includes(tagId)
          ? current.filter((id) => id !== tagId)
          : [...current, tagId];
        onTagsChange?.(next);
      }
    },
    [isMode1, selectedIds, onAdd, onRemove, selectedTagIds, onTagsChange],
  );

  const handleRemove = useCallback(
    (tagId: string) => {
      if (isMode1) {
        onRemove?.(tagId);
      } else {
        onTagsChange?.((selectedTagIds || []).filter((id) => id !== tagId));
      }
    },
    [isMode1, onRemove, onTagsChange, selectedTagIds],
  );

  const handleCreate = useCallback(async () => {
    const name = newName.trim();
    if (!name || !onCreate) return;
    const created = await onCreate(name, newColor);
    if (created) {
      handleToggleTag(String(created.id));
      setNewName('');
      setShowCreate(false);
    }
  }, [newName, newColor, onCreate, handleToggleTag]);

  return (
    <div className="space-y-2">
      {/* Assigned / selected tag pills */}
      <div className="flex flex-wrap gap-1.5">
        {displayTags.map((tag) => (
          <TagPill key={tag.id} tag={tag} onRemove={handleRemove} readOnly={readOnly} />
        ))}
        {!readOnly && (
          <>
            <button
              ref={triggerRef}
              onClick={() => setPanelOpen(!panelOpen)}
              className="inline-flex items-center gap-1 px-2.5 py-1 text-xs rounded-full bg-zinc-800 text-zinc-400 hover:text-zinc-200 hover:bg-zinc-700 transition-colors"
            >
              <Plus size={10} />
              {t('resources.addTag', 'Add Tag')}
            </button>

            {/* Inline create */}
            {onCreate && !showCreate && (
              <button
                onClick={() => setShowCreate(true)}
                className="inline-flex items-center gap-1 px-2 py-1 text-xs rounded-full text-zinc-500 hover:text-zinc-300 transition-colors"
              >
                <Palette size={10} />
                {t('resources.createTag', 'Create')}
              </button>
            )}
          </>
        )}
      </div>

      {/* Inline create form */}
      {showCreate && onCreate && (
        <div className="flex items-center gap-2 p-2 bg-zinc-800/50 rounded-lg">
          <input
            type="text"
            value={newName}
            onChange={(e) => setNewName(e.target.value)}
            placeholder="Tag name..."
            className="flex-1 bg-zinc-800 border border-zinc-700/50 rounded px-2 py-1 text-xs text-zinc-200 placeholder-zinc-600 focus:outline-none focus:border-indigo-500/50"
            autoFocus
            onKeyDown={(e) => { if (e.key === 'Enter') handleCreate(); if (e.key === 'Escape') setShowCreate(false); }}
          />
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
          <button
            onClick={handleCreate}
            disabled={!newName.trim()}
            className="px-2 py-1 text-xs bg-indigo-600 hover:bg-indigo-500 text-white rounded disabled:opacity-50 transition-colors"
          >
            Create
          </button>
        </div>
      )}

      {/* Floating panel */}
      {panelOpen && (
        <FloatingPanel
          triggerRef={triggerRef}
          allTags={allTags}
          selectedIds={selectedIds}
          starredIds={prefs.starred_tag_ids}
          settings={prefs.picker_settings}
          panelSize={prefs.panel_size}
          onToggleTag={handleToggleTag}
          onToggleStar={toggleStar}
          onUpdateSettings={updateSettings}
          onPanelResize={updatePanelSize}
          onClose={() => setPanelOpen(false)}
        />
      )}
    </div>
  );
};
