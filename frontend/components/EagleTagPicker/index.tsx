import React, { useState, useRef, useMemo, useCallback } from 'react';
import { Plus } from 'lucide-react';
import { useTranslation } from 'react-i18next';
import { TagPill } from './TagPill';
import { FloatingPanel } from './FloatingPanel';
import { useTagPreferences } from './useTagPreferences';
import type { EagleTagPickerProps } from './types';

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

  const handleCreate = useCallback(async (name: string, color: string) => {
    if (!name.trim() || !onCreate) return null;
    const created = await onCreate(name.trim(), color);
    if (created) {
      handleToggleTag(String(created.id));
    }
    return created;
  }, [onCreate, handleToggleTag]);

  return (
    <div className="space-y-2">
      {/* Assigned / selected tag pills */}
      <div className="flex flex-wrap gap-1.5">
        {displayTags.map((tag) => (
          <TagPill key={tag.id} tag={tag} onRemove={handleRemove} readOnly={readOnly} />
        ))}
        {!readOnly && (
          <button
            ref={triggerRef}
            onClick={() => setPanelOpen(!panelOpen)}
            className="inline-flex items-center gap-1 px-2.5 py-1 text-xs rounded-full bg-zinc-800 text-zinc-400 hover:text-zinc-200 hover:bg-zinc-700 transition-colors"
          >
            <Plus size={10} />
            {t('resources.addTag', 'Add Tag')}
          </button>
        )}
      </div>

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
          onCreate={onCreate ? handleCreate : undefined}
        />
      )}
    </div>
  );
};
