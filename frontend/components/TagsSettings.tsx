/**
 * TagsSettings Component - Tag management in Settings
 * Shows tags grouped by tag_groups with usage counts and enabled status.
 */

import React, { useState, useEffect, useMemo } from 'react';
import { useTranslation } from 'react-i18next';
import {
  Tag as TagIcon,
  Plus,
  Search,
  Trash2,
  X,
  Check,
  Loader2,
  AlertCircle,
  Eye,
  EyeOff,
  FolderOpen,
  LayoutGrid,
  Circle,
  GripVertical,
} from 'lucide-react';
import type { Tag } from '../types';
import {
  fetchAllTags as fetchTags,
  fetchTagGroups,
  createTag,
  createTagGroup,
  renameTagGroup,
  deleteTagGroup,
  reorderTagGroups,
  updateTag,
  deleteTag,
  type TagGroup,
} from '../services/unifiedTagService';

// Color palette for tags
const TAG_COLORS = [
  { name: 'Red', value: '#ef4444' },
  { name: 'Orange', value: '#f97316' },
  { name: 'Yellow', value: '#eab308' },
  { name: 'Green', value: '#22c55e' },
  { name: 'Teal', value: '#14b8a6' },
  { name: 'Blue', value: '#3b82f6' },
  { name: 'Violet', value: '#8b5cf6' },
  { name: 'Pink', value: '#ec4899' },
];

// Helper to generate tag style from color
const getTagStyle = (color: string | null, disabled = false) => {
  const baseColor = color || '#3b82f6';
  return {
    backgroundColor: disabled ? 'transparent' : `${baseColor}15`,
    color: disabled ? '#71717a' : baseColor,
    borderColor: disabled ? '#3f3f46' : `${baseColor}30`,
    opacity: disabled ? 0.5 : 1,
  };
};

export const TagsSettings: React.FC = () => {
  const { t, i18n } = useTranslation();
  const isZh = i18n.language?.startsWith('zh');

  const [tags, setTags] = useState<Tag[]>([]);
  const [groups, setGroups] = useState<TagGroup[]>([]);
  const [isLoading, setIsLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [searchQuery, setSearchQuery] = useState('');

  // Create form state
  const [showCreateForm, setShowCreateForm] = useState(false);
  const [newTagName, setNewTagName] = useState('');
  const [newTagNameZh, setNewTagNameZh] = useState('');
  const [newTagColor, setNewTagColor] = useState(TAG_COLORS[5].value);
  const [isCreating, setIsCreating] = useState(false);

  // Edit state
  const [editingTag, setEditingTag] = useState<Tag | null>(null);
  const [editTagName, setEditTagName] = useState('');
  const [editTagColor, setEditTagColor] = useState('');
  const [isSaving, setIsSaving] = useState(false);

  // Delete confirmation
  const [deletingTagId, setDeletingTagId] = useState<string | null>(null);

  // Toggle enabled (optimistic)
  const [togglingIds, setTogglingIds] = useState<Set<string>>(new Set());

  // Sidebar selection: null = All, '__uncategorized__' = Uncategorized, group name = specific group
  const [selectedGroup, setSelectedGroup] = useState<string | null>(null);

  // Group management
  const [showCreateGroup, setShowCreateGroup] = useState(false);
  const [newGroupName, setNewGroupName] = useState('');
  const [creatingGroup, setCreatingGroup] = useState(false);
  const [deletingGroupId, setDeletingGroupId] = useState<string | null>(null);
  const [dragGroupId, setDragGroupId] = useState<string | null>(null);
  const [dragOverGroupId, setDragOverGroupId] = useState<string | null>(null);

  // Inline rename for groups (double-click)
  const [renamingGroupId, setRenamingGroupId] = useState<string | null>(null);
  const [renameGroupValue, setRenameGroupValue] = useState('');

  // Drag a tag onto a group sidebar row to reassign it. Tag drag uses a
  // separate state slot so it doesn't collide with the existing group
  // reorder drag (which targets the same drop zones).
  const [dragTagId, setDragTagId] = useState<string | null>(null);
  const [tagDropTargetGroupId, setTagDropTargetGroupId] = useState<string | null>(null);

  // Auto-translate (Chrome-extension parity) — when the user types Chinese
  // in the English field (or vice-versa) we hit mymemory.translated.net
  // after a 600ms idle to fill the other field.
  const translateTimerRef = React.useRef<number | null>(null);

  // Load tags and groups
  useEffect(() => {
    loadData();
  }, []);

  const loadData = async () => {
    setIsLoading(true);
    setError(null);
    try {
      const [fetchedTags, fetchedGroups] = await Promise.all([
        fetchTags(), // get all tags (settings page shows all including disabled)
        fetchTagGroups(),
      ]);
      setTags(fetchedTags);
      setGroups(fetchedGroups);
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Failed to load tags');
    } finally {
      setIsLoading(false);
    }
  };

  // Get display name based on current language
  const getDisplayName = (tag: Tag): string => {
    if (isZh && tag.name_zh) return tag.name_zh;
    return tag.name;
  };

  // Filter tags based on search
  const filteredTags = useMemo(() => {
    if (!searchQuery) return tags;
    const query = searchQuery.toLowerCase();
    return tags.filter((tag) => {
      const nameMatch = tag.name.toLowerCase().includes(query);
      const nameZhMatch = tag.name_zh?.toLowerCase().includes(query);
      const groupMatch = tag.group_name?.toLowerCase().includes(query);
      return nameMatch || nameZhMatch || groupMatch;
    });
  }, [tags, searchQuery]);

  // Names that collide with the sentinel "(no group)" display label — if
  // a real group has one of these names (legacy data — backend now blocks
  // creating new ones), we merge its tags into the sentinel bucket so the
  // UI doesn't show two "Uncategorized" sections at once.
  const RESERVED_GROUP_NAMES = useMemo(
    () => new Set(['uncategorized', '未分类']),
    [],
  );
  const isReservedGroupName = (name: string | null | undefined) =>
    !!name && RESERVED_GROUP_NAMES.has(name.trim().toLowerCase());

  // Real groups minus any whose name collides with the sentinel.
  const visibleGroups = useMemo(
    () => groups.filter((g) => !isReservedGroupName(g.name)),
    [groups],
  );

  // Count tags per group (for sidebar, uses unfiltered tags)
  const groupCounts = useMemo(() => {
    const counts = new Map<string, number>();
    for (const group of visibleGroups) {
      counts.set(group.name, 0);
    }
    counts.set('__uncategorized__', 0);
    for (const tag of tags) {
      // Treat tags from a reserved-named real group as sentinel-bucket
      // tags so the count and the right pane line up.
      const collisionGroup = isReservedGroupName(tag.group_name);
      const key =
        !tag.group_name || collisionGroup ? '__uncategorized__' : tag.group_name;
      counts.set(key, (counts.get(key) || 0) + 1);
    }
    return counts;
  }, [tags, visibleGroups]);

  // Group tags by group_name, preserving group sort order
  const groupedTags = useMemo(() => {
    const groupMap = new Map<string, Tag[]>();

    for (const group of visibleGroups) {
      groupMap.set(group.name, []);
    }
    groupMap.set('__uncategorized__', []);

    // Apply sidebar filter + search. Reserved-named groups are merged into
    // the sentinel for both the filter check and the bucket assignment.
    const source = filteredTags.filter((tag) => {
      const collisionGroup = isReservedGroupName(tag.group_name);
      if (selectedGroup === null) return true;
      if (selectedGroup === '__uncategorized__') {
        return !tag.group_name || collisionGroup;
      }
      return tag.group_name === selectedGroup;
    });

    for (const tag of source) {
      const collisionGroup = isReservedGroupName(tag.group_name);
      const key =
        !tag.group_name || collisionGroup ? '__uncategorized__' : tag.group_name;
      if (!groupMap.has(key)) groupMap.set(key, []);
      groupMap.get(key)!.push(tag);
    }

    const result: { name: string; tags: Tag[] }[] = [];
    for (const [name, groupTags] of groupMap) {
      if (groupTags.length > 0) {
        result.push({
          name: name === '__uncategorized__' ? 'Uncategorized' : name,
          tags: groupTags,
        });
      }
    }
    return result;
  }, [filteredTags, visibleGroups, selectedGroup]);

  // Toggle enabled status
  const handleToggleEnabled = async (tag: Tag, e: React.MouseEvent) => {
    e.stopPropagation();
    const newEnabled = !tag.enabled;

    // Optimistic update
    setTags((prev) =>
      prev.map((t) => (t.id === tag.id ? { ...t, enabled: newEnabled } : t))
    );
    setTogglingIds((prev) => new Set(prev).add(tag.id));

    try {
      await updateTag(tag.id, { enabled: newEnabled });
    } catch (err) {
      // Revert on failure
      setTags((prev) =>
        prev.map((t) => (t.id === tag.id ? { ...t, enabled: !newEnabled } : t))
      );
      setError(err instanceof Error ? err.message : 'Failed to toggle tag');
    } finally {
      setTogglingIds((prev) => {
        const next = new Set(prev);
        next.delete(tag.id);
        return next;
      });
    }
  };

  // Handle create tag
  const handleCreateTag = async () => {
    if (!newTagName.trim()) return;
    setIsCreating(true);
    try {
      const newTag = await createTag({
        name: newTagName.trim(),
        name_zh: newTagNameZh.trim() || undefined,
        color: newTagColor,
      });
      setTags((prev) => [...prev, newTag]);
      setNewTagName('');
      setNewTagNameZh('');
      setShowCreateForm(false);
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Failed to create tag');
    } finally {
      setIsCreating(false);
    }
  };

  // Handle edit tag
  const handleStartEdit = (tag: Tag, e?: React.MouseEvent) => {
    e?.stopPropagation();
    setEditingTag(tag);
    setEditTagName(tag.name);
    setEditTagColor(tag.color || TAG_COLORS[5].value);
  };

  const handleSaveEdit = async () => {
    if (!editingTag || !editTagName.trim()) return;
    setIsSaving(true);
    try {
      const updated = await updateTag(editingTag.id, {
        name: editTagName.trim(),
        color: editTagColor,
      });
      setTags((prev) => prev.map((t) => (t.id === editingTag.id ? updated : t)));
      setEditingTag(null);
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Failed to update tag');
    } finally {
      setIsSaving(false);
    }
  };

  const handleCancelEdit = () => {
    setEditingTag(null);
    setEditTagName('');
    setEditTagColor('');
  };

  // Handle delete tag
  const handleDeleteTag = async (tagId: string, e?: React.MouseEvent) => {
    e?.stopPropagation();
    try {
      await deleteTag(tagId);
      setTags((prev) => prev.filter((t) => t.id !== tagId));
      setDeletingTagId(null);
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Failed to delete tag');
    }
  };

  // Group management handlers
  const handleRenameGroupConfirm = async (groupId: string) => {
    const trimmed = renameGroupValue.trim();
    const original = groups.find((g) => g.id === groupId);
    if (!original || !trimmed || trimmed === original.name) {
      setRenamingGroupId(null);
      return;
    }
    // Optimistic update — flip name client-side, reconcile on failure.
    const oldName = original.name;
    setGroups((prev) =>
      prev.map((g) => (g.id === groupId ? { ...g, name: trimmed } : g)),
    );
    setTags((prev) =>
      prev.map((tt) =>
        tt.group_name === oldName ? { ...tt, group_name: trimmed } : tt,
      ),
    );
    if (selectedGroup === oldName) setSelectedGroup(trimmed);
    setRenamingGroupId(null);
    try {
      await renameTagGroup(groupId, trimmed);
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Failed to rename group');
      loadData();
    }
  };

  // mymemory.translated.net free MT — same endpoint the chrome-extension
  // popup uses, so the web UX matches when creating a tag.
  const isChinese = (text: string) => /[一-鿿]/.test(text);
  const fetchTranslation = async (
    source: string,
    direction: 'zh|en' | 'en|zh',
  ): Promise<string | null> => {
    try {
      const url = `https://api.mymemory.translated.net/get?q=${encodeURIComponent(source)}&langpair=${direction}&de=8512939@qq.com`;
      const res = await fetch(url);
      if (!res.ok) return null;
      const data = await res.json();
      const translated = data?.responseData?.translatedText;
      return translated && translated !== source ? translated : null;
    } catch (err) {
      console.error('Tag translate failed:', err);
      return null;
    }
  };
  // Debounced translate hook for the create-tag dialog. Mirrors the
  // chrome-extension popup: single input → fill the other language field
  // when the corresponding target is still empty.
  const scheduleTranslate = (source: string, originField: 'en' | 'zh') => {
    if (translateTimerRef.current) window.clearTimeout(translateTimerRef.current);
    const trimmed = source.trim();
    if (!trimmed) return;
    translateTimerRef.current = window.setTimeout(async () => {
      const sourceIsChinese = isChinese(trimmed);
      // EN field with Chinese input → user wants to type in Chinese; flip
      // the layout: fill EN with translation, mirror Chinese into ZH.
      if (originField === 'en' && sourceIsChinese) {
        const en = await fetchTranslation(trimmed, 'zh|en');
        if (en) {
          setNewTagName(en);
          // Mirror the original Chinese into the ZH field if it's empty.
          setNewTagNameZh((prev) => (prev.trim() ? prev : trimmed));
        }
        return;
      }
      // EN field with English input → translate to ZH if ZH is empty.
      if (originField === 'en' && !sourceIsChinese) {
        const zh = await fetchTranslation(trimmed, 'en|zh');
        if (zh) {
          setNewTagNameZh((prev) => (prev.trim() ? prev : zh));
        }
        return;
      }
      // ZH field with Chinese input → translate to EN if EN is empty.
      if (originField === 'zh' && sourceIsChinese) {
        const en = await fetchTranslation(trimmed, 'zh|en');
        if (en) {
          setNewTagName((prev) => (prev.trim() ? prev : en));
        }
      }
    }, 600);
  };

  const handleCreateGroup = async () => {
    if (!newGroupName.trim()) return;
    setCreatingGroup(true);
    try {
      const created = await createTagGroup(newGroupName.trim());
      setGroups((prev) => [...prev, created]);
      setNewGroupName('');
      setShowCreateGroup(false);
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Failed to create group');
    } finally {
      setCreatingGroup(false);
    }
  };

  const handleDeleteGroup = async (groupId: string) => {
    try {
      await deleteTagGroup(groupId);
      setGroups((prev) => prev.filter((g) => g.id !== groupId));
      // Tags in this group become uncategorized — refresh
      setTags((prev) =>
        prev.map((t) => {
          const group = groups.find((g) => g.id === groupId);
          if (group && t.group_name === group.name) {
            return { ...t, group_name: null, group_id: null };
          }
          return t;
        })
      );
      setDeletingGroupId(null);
      if (selectedGroup && groups.find((g) => g.id === groupId)?.name === selectedGroup) {
        setSelectedGroup(null);
      }
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Failed to delete group');
    }
  };

  const handleDragStart = (groupId: string) => {
    setDragGroupId(groupId);
  };

  const handleDragOver = (e: React.DragEvent, groupId: string) => {
    e.preventDefault();
    if (dragTagId) {
      // A tag is being dragged onto a group row — that path takes
      // precedence over group reordering.
      setTagDropTargetGroupId(groupId);
      return;
    }
    if (groupId !== dragGroupId) setDragOverGroupId(groupId);
  };

  // Drop handler doubles as: (a) group reorder when a group was dragged,
  // (b) tag reassignment when a tag was dragged onto the group row.
  const handleDrop = async (targetGroupId: string) => {
    // ── Tag drop: reassign the dragged tag to ``targetGroupId`` ────────
    if (dragTagId) {
      const draggedTag = tags.find((tt) => tt.id === dragTagId);
      const tagId = dragTagId;
      setDragTagId(null);
      setTagDropTargetGroupId(null);
      if (!draggedTag) return;
      const newGroupId =
        targetGroupId === '__uncategorized__' ? null : targetGroupId;
      // Skip the network call if the tag is already in this group.
      if (
        (draggedTag as any).group_id === newGroupId ||
        (newGroupId == null && !(draggedTag as any).group_id)
      ) {
        return;
      }
      // Optimistic update so the UI flips immediately; reconcile on error.
      const targetGroup = groups.find((g) => g.id === newGroupId);
      setTags((prev) =>
        prev.map((tt) =>
          tt.id === tagId
            ? { ...tt, group_id: newGroupId, group_name: targetGroup?.name || null }
            : tt,
        ),
      );
      try {
        await updateTag(tagId, { group_id: newGroupId });
      } catch (err) {
        setError(err instanceof Error ? err.message : 'Failed to move tag');
        loadData();
      }
      return;
    }

    if (!dragGroupId || dragGroupId === targetGroupId) {
      setDragGroupId(null);
      setDragOverGroupId(null);
      return;
    }
    const oldIndex = groups.findIndex((g) => g.id === dragGroupId);
    const newIndex = groups.findIndex((g) => g.id === targetGroupId);
    if (oldIndex === -1 || newIndex === -1) return;

    const reordered = [...groups];
    const [moved] = reordered.splice(oldIndex, 1);
    reordered.splice(newIndex, 0, moved);
    setGroups(reordered);
    setDragGroupId(null);
    setDragOverGroupId(null);

    try {
      await reorderTagGroups(reordered.map((g) => g.id));
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Failed to reorder');
      loadData();
    }
  };

  const enabledCount = tags.filter((t) => t.enabled !== false).length;

  return (
    <section className="bg-zinc-900 border border-zinc-800 rounded-xl overflow-hidden animate-in fade-in duration-300">
      {/* Header */}
      <div className="px-6 py-4 border-b border-zinc-800 bg-zinc-900/50 flex items-center justify-between">
        <div className="flex items-center gap-3">
          <div className="p-2 bg-amber-500/10 rounded-lg text-amber-400">
            <TagIcon size={20} />
          </div>
          <div>
            <h2 className="font-semibold text-zinc-200">{t('settings.tags.title')}</h2>
            <p className="text-xs text-zinc-500">
              {tags.length} tags, {enabledCount} enabled
            </p>
          </div>
        </div>
        <div className="flex items-center gap-2">
          <button
            onClick={() => setShowCreateForm(true)}
            className="bg-zinc-100 hover:bg-white text-zinc-900 px-4 py-2 rounded-lg text-sm font-semibold transition-colors flex items-center gap-2"
          >
            <Plus size={16} />
            {t('settings.tags.createNew')}
          </button>
        </div>
      </div>

      {/* Error message */}
      {error && (
        <div className="mx-6 mt-4 p-3 bg-red-500/10 border border-red-500/20 rounded-lg flex items-center gap-2 text-red-400 text-sm">
          <AlertCircle size={16} />
          {error}
          <button onClick={() => setError(null)} className="ml-auto">
            <X size={14} />
          </button>
        </div>
      )}

      {/* Main layout: Sidebar + Content */}
      <div className="flex">
        {/* Sidebar */}
        <div className="w-56 shrink-0 border-r border-zinc-800 bg-zinc-950/30">
          <div className="p-3 space-y-0.5">
            {/* All */}
            <button
              onClick={() => setSelectedGroup(null)}
              className={`w-full flex items-center gap-2.5 px-3 py-2 rounded-lg text-sm transition-colors ${
                selectedGroup === null
                  ? 'bg-indigo-500/15 text-indigo-400'
                  : 'text-zinc-400 hover:bg-zinc-800 hover:text-zinc-300'
              }`}
            >
              <LayoutGrid size={15} />
              <span className="flex-1 text-left font-medium">All</span>
              <span className="text-xs text-zinc-500">{tags.length}</span>
            </button>

            {/* Uncategorized */}
            <button
              onClick={() => setSelectedGroup('__uncategorized__')}
              className={`w-full flex items-center gap-2.5 px-3 py-2 rounded-lg text-sm transition-colors ${
                selectedGroup === '__uncategorized__'
                  ? 'bg-indigo-500/15 text-indigo-400'
                  : 'text-zinc-400 hover:bg-zinc-800 hover:text-zinc-300'
              }`}
            >
              <Circle size={15} />
              <span className="flex-1 text-left font-medium">Uncategorized</span>
              <span className="text-xs text-zinc-500">{groupCounts.get('__uncategorized__') || 0}</span>
            </button>

            {/* Collision banner — a real group whose name matches the
                sentinel "Uncategorized" exists from legacy data. We've
                merged its tags into the sentinel above; offer a one-click
                cleanup so the user can delete the orphan group (its tags
                stay, just become truly uncategorized). */}
            {groups.some((g) => isReservedGroupName(g.name)) && (
              <div className="mx-3 mt-2 p-2 rounded-md bg-amber-500/10 border border-amber-500/30 text-[11px] text-amber-200/90 space-y-2">
                <div>
                  Found a custom group named "Uncategorized" — its tags
                  have been merged into the bucket above.
                </div>
                <button
                  onClick={async () => {
                    const orphans = groups.filter((g) =>
                      isReservedGroupName(g.name),
                    );
                    for (const g of orphans) {
                      try {
                        await deleteTagGroup(g.id);
                      } catch (err) {
                        console.error('Cleanup orphan group failed:', err);
                      }
                    }
                    loadData();
                  }}
                  className="w-full text-center px-2 py-1 rounded bg-amber-500/20 hover:bg-amber-500/30 text-amber-100 transition-colors"
                >
                  Clean up
                </button>
              </div>
            )}

            {/* Groups header */}
            <div className="flex items-center justify-between pt-4 pb-1 px-3">
              <span className="text-xs font-medium text-zinc-500 uppercase tracking-wider">
                Groups ({visibleGroups.length})
              </span>
              <button
                onClick={() => setShowCreateGroup(!showCreateGroup)}
                className="text-zinc-500 hover:text-zinc-300 transition-colors"
                title="Add group"
              >
                {showCreateGroup ? <X size={14} /> : <Plus size={14} />}
              </button>
            </div>

            {/* Create group form */}
            {showCreateGroup && (
              <div className="px-3 pb-2">
                <div className="flex gap-1.5">
                  <input
                    type="text"
                    value={newGroupName}
                    onChange={(e) => setNewGroupName(e.target.value)}
                    onKeyDown={(e) => e.key === 'Enter' && handleCreateGroup()}
                    placeholder="Group name"
                    autoFocus
                    className="flex-1 min-w-0 px-2.5 py-1.5 rounded-md bg-zinc-800 border border-zinc-700 text-xs text-zinc-200 placeholder-zinc-500 outline-none focus:border-indigo-500"
                  />
                  <button
                    onClick={handleCreateGroup}
                    disabled={!newGroupName.trim() || creatingGroup}
                    className="px-2 py-1.5 rounded-md bg-indigo-600 text-xs text-white disabled:opacity-40 hover:bg-indigo-500 transition-colors"
                  >
                    {creatingGroup ? <Loader2 size={12} className="animate-spin" /> : <Check size={12} />}
                  </button>
                </div>
              </div>
            )}

            {/* Group list */}
            {visibleGroups.map((group) => {
              const isTagDropTarget = tagDropTargetGroupId === group.id;
              return (
              <div
                key={group.id}
                className={`group/item relative ${
                  isTagDropTarget
                    ? 'ring-2 ring-indigo-500 rounded-lg'
                    : dragOverGroupId === group.id
                      ? 'border-t-2 border-indigo-500'
                      : ''
                }`}
                draggable={renamingGroupId !== group.id}
                onDragStart={() => handleDragStart(group.id)}
                onDragOver={(e) => handleDragOver(e, group.id)}
                onDragLeave={() => {
                  setDragOverGroupId(null);
                  if (tagDropTargetGroupId === group.id) setTagDropTargetGroupId(null);
                }}
                onDrop={() => handleDrop(group.id)}
                onDragEnd={() => { setDragGroupId(null); setDragOverGroupId(null); }}
              >
                {renamingGroupId === group.id ? (
                  <div className="flex gap-1.5 px-3 py-2">
                    <input
                      type="text"
                      value={renameGroupValue}
                      autoFocus
                      onChange={(e) => setRenameGroupValue(e.target.value)}
                      onKeyDown={(e) => {
                        if (e.key === 'Enter') handleRenameGroupConfirm(group.id);
                        if (e.key === 'Escape') setRenamingGroupId(null);
                      }}
                      onBlur={() => handleRenameGroupConfirm(group.id)}
                      className="flex-1 min-w-0 px-2 py-1 rounded-md bg-zinc-800 border border-indigo-500 text-xs text-zinc-200 outline-none"
                    />
                  </div>
                ) : (
                <button
                  onClick={() => setSelectedGroup(group.name)}
                  onDoubleClick={(e) => {
                    e.stopPropagation();
                    setRenamingGroupId(group.id);
                    setRenameGroupValue(group.name);
                  }}
                  title={t('settings.tags.doubleClickToRename', 'Double-click to rename')}
                  className={`w-full flex items-center gap-2 px-3 py-2 rounded-lg text-sm transition-colors ${
                    dragGroupId === group.id ? 'opacity-40' : ''
                  } ${
                    selectedGroup === group.name
                      ? 'bg-indigo-500/15 text-indigo-400'
                      : 'text-zinc-400 hover:bg-zinc-800 hover:text-zinc-300'
                  }`}
                >
                  <GripVertical size={12} className="text-zinc-600 shrink-0 cursor-grab active:cursor-grabbing" />
                  <FolderOpen size={14} className="shrink-0" />
                  <span className="flex-1 text-left truncate">{group.name}</span>
                  <span className="text-xs text-zinc-500 group-hover/item:hidden">{groupCounts.get(group.name) || 0}</span>
                </button>
                )}
                {/* Delete button on hover */}
                {deletingGroupId === group.id ? (
                  <div className="absolute right-1 top-1/2 -translate-y-1/2 flex items-center gap-1">
                    <button
                      onClick={() => handleDeleteGroup(group.id)}
                      className="p-1 rounded text-red-400 hover:bg-red-500/20 text-[10px]"
                    >
                      <Check size={12} />
                    </button>
                    <button
                      onClick={() => setDeletingGroupId(null)}
                      className="p-1 rounded text-zinc-400 hover:bg-zinc-700 text-[10px]"
                    >
                      <X size={12} />
                    </button>
                  </div>
                ) : (
                  <button
                    onClick={(e) => { e.stopPropagation(); setDeletingGroupId(group.id); }}
                    className="absolute right-2 top-1/2 -translate-y-1/2 hidden group-hover/item:block p-1 rounded text-zinc-600 hover:text-red-400 hover:bg-zinc-800 transition-colors"
                  >
                    <Trash2 size={12} />
                  </button>
                )}
              </div>
              );
            })}
          </div>
        </div>

        {/* Right content */}
        <div className="flex-1 min-w-0">
          {/* Search */}
          <div className="p-4 border-b border-zinc-800">
            <div className="relative">
              <Search
                size={16}
                className="absolute left-3 top-1/2 -translate-y-1/2 text-zinc-500"
              />
              <input
                type="text"
                placeholder={t('settings.tags.searchPlaceholder')}
                value={searchQuery}
                onChange={(e) => setSearchQuery(e.target.value)}
                className="w-full pl-10 pr-4 py-2.5 bg-zinc-950 border border-zinc-800 rounded-lg text-sm text-zinc-200 placeholder-zinc-500 focus:outline-none focus:border-indigo-500 transition-colors"
              />
            </div>
          </div>

          {/* Tags by Group */}
          <div className="p-4">
            {isLoading ? (
              <div className="flex items-center justify-center py-12">
                <Loader2 size={24} className="animate-spin text-zinc-500" />
              </div>
            ) : filteredTags.length === 0 ? (
              <div className="text-center py-12 text-zinc-500">
                <TagIcon size={40} className="mx-auto mb-3 opacity-30" />
                <p>{searchQuery ? t('settings.tags.noResults') : t('settings.tags.empty')}</p>
              </div>
            ) : (
              <div className="space-y-5">
                {groupedTags.map(({ name, tags: groupTags }) => (
                  <div key={name}>
                    <div className="flex items-center gap-2 mb-2">
                      <FolderOpen size={14} className="text-zinc-500" />
                      <span className="text-xs font-medium text-zinc-400 uppercase tracking-wider">
                        {name}
                      </span>
                      <span className="text-xs text-zinc-600">({groupTags.length})</span>
                    </div>
                    <div className="flex flex-wrap gap-2">
                      {groupTags.map((tag) => (
                        <div key={tag.id} className="group relative">
                          {deletingTagId === tag.id ? (
                            <div className="inline-flex items-center gap-1 px-3 py-1.5 rounded-full border border-red-500/30 bg-red-500/10 text-sm">
                              <span className="text-red-400 mr-1">{t('common.delete')}?</span>
                              <button
                                onClick={(e) => handleDeleteTag(tag.id, e)}
                                className="p-0.5 text-red-400 hover:text-red-300"
                              >
                                <Check size={14} />
                              </button>
                              <button
                                onClick={(e) => { e.stopPropagation(); setDeletingTagId(null); }}
                                className="p-0.5 text-zinc-400 hover:text-zinc-300"
                              >
                                <X size={14} />
                              </button>
                            </div>
                          ) : (
                            <div
                              onClick={() => tag.type !== 'system' && handleStartEdit(tag)}
                              draggable={tag.type !== 'system'}
                              onDragStart={(e) => {
                                if (tag.type === 'system') {
                                  e.preventDefault();
                                  return;
                                }
                                setDragTagId(tag.id);
                                // Required for the drag image to render in
                                // some browsers; payload is unused since
                                // dragTagId state carries the id.
                                e.dataTransfer.effectAllowed = 'move';
                                try {
                                  e.dataTransfer.setData('text/plain', tag.id);
                                } catch {
                                  /* ignore — some browsers reject for non-text mime */
                                }
                              }}
                              onDragEnd={() => {
                                setDragTagId(null);
                                setTagDropTargetGroupId(null);
                              }}
                              className={`inline-flex items-center gap-1.5 px-2.5 py-1 rounded-full border text-xs font-medium transition-all ${
                                tag.type !== 'system'
                                  ? 'cursor-pointer hover:scale-105 hover:shadow-lg'
                                  : 'cursor-default'
                              } ${dragTagId === tag.id ? 'opacity-40' : ''}`}
                              style={getTagStyle(tag.color, tag.enabled === false)}
                            >
                              <TagIcon size={11} className="shrink-0" />
                              <span className="truncate">{getDisplayName(tag)}</span>
                              <span className="opacity-50 shrink-0">
                                {tag.media_count ?? 0}
                              </span>
                              <button
                                onClick={(e) => handleToggleEnabled(tag, e)}
                                className={`p-0.5 rounded transition-all ${
                                  tag.enabled === false
                                    ? 'text-zinc-600 hover:text-zinc-400'
                                    : 'text-current opacity-40 hover:opacity-100'
                                }`}
                                title={tag.enabled === false ? 'Enable tag' : 'Disable tag'}
                              >
                                {togglingIds.has(tag.id) ? (
                                  <Loader2 size={11} className="animate-spin" />
                                ) : tag.enabled === false ? (
                                  <EyeOff size={11} />
                                ) : (
                                  <Eye size={11} />
                                )}
                              </button>
                              {tag.type !== 'system' && (
                                <button
                                  onClick={(e) => { e.stopPropagation(); setDeletingTagId(tag.id); }}
                                  className="p-0.5 rounded hover:bg-red-500/20 hover:text-red-400 transition-all opacity-0 group-hover:opacity-100"
                                  title={t('common.delete')}
                                >
                                  <X size={11} />
                                </button>
                              )}
                            </div>
                          )}
                        </div>
                      ))}
                    </div>
                  </div>
                ))}
              </div>
            )}
          </div>
        </div>
      </div>

      {/* Create Tag Modal */}
      {showCreateForm && (
        <div className="fixed inset-0 bg-black/80 backdrop-blur-sm z-50 flex items-center justify-center p-4">
          <div className="bg-zinc-900 border border-zinc-800 rounded-2xl w-full max-w-md shadow-2xl overflow-hidden animate-in zoom-in-95 duration-200">
            <div className="px-6 py-4 border-b border-zinc-800 flex justify-between items-center">
              <h3 className="text-lg font-bold text-white">
                {t('settings.tags.createNew')}
              </h3>
              <button
                onClick={() => setShowCreateForm(false)}
                className="text-zinc-500 hover:text-zinc-300"
              >
                <X size={20} />
              </button>
            </div>
            <div className="p-6 space-y-4">
              <div className="space-y-2">
                <label className="text-sm font-medium text-zinc-300">
                  {t('settings.tags.tagName')} (English)
                </label>
                <input
                  type="text"
                  placeholder="e.g. Food, Travel, Music"
                  value={newTagName}
                  onChange={(e) => {
                    const value = e.target.value;
                    setNewTagName(value);
                    scheduleTranslate(value, 'en');
                  }}
                  className="w-full px-4 py-3 bg-zinc-950 border border-zinc-800 rounded-lg text-zinc-200 placeholder-zinc-500 focus:outline-none focus:border-indigo-500 transition-colors"
                  autoFocus
                  onKeyDown={(e) => e.key === 'Enter' && handleCreateTag()}
                />
              </div>
              <div className="space-y-2">
                <div className="flex items-center justify-between">
                  <label className="text-sm font-medium text-zinc-300">
                    {t('settings.tags.tagName')} (中文)
                    <span className="text-zinc-500 text-xs ml-2">Optional</span>
                  </label>
                  {/* "=" — chrome-extension parity. For terms that should
                      stay identical in both languages (Agent, Skill,
                      LLM, brand names), click to copy the English value
                      into this field verbatim and skip translation. */}
                  <button
                    type="button"
                    onClick={() => {
                      // Cancel any pending auto-translate so it doesn't
                      // overwrite the value we just copied in.
                      if (translateTimerRef.current) {
                        window.clearTimeout(translateTimerRef.current);
                        translateTimerRef.current = null;
                      }
                      const src = newTagName.trim();
                      if (!src) return;
                      setNewTagNameZh(src);
                    }}
                    disabled={!newTagName.trim()}
                    title={t('settings.tags.sameAsEnglish', 'Use the same value as English (skip translation)')}
                    className="text-[11px] px-2 py-0.5 rounded border border-zinc-700 text-zinc-400 hover:text-zinc-200 hover:border-zinc-500 disabled:opacity-40 disabled:cursor-not-allowed transition-colors"
                  >
                    = EN
                  </button>
                </div>
                <input
                  type="text"
                  placeholder="例如：美食、旅行、音乐"
                  value={newTagNameZh}
                  onChange={(e) => {
                    const value = e.target.value;
                    setNewTagNameZh(value);
                    scheduleTranslate(value, 'zh');
                  }}
                  className="w-full px-4 py-3 bg-zinc-950 border border-zinc-800 rounded-lg text-zinc-200 placeholder-zinc-500 focus:outline-none focus:border-indigo-500 transition-colors"
                  onKeyDown={(e) => e.key === 'Enter' && handleCreateTag()}
                />
              </div>
              <div className="space-y-2">
                <label className="text-sm font-medium text-zinc-300">
                  {t('settings.tags.tagColor')}
                </label>
                <div className="flex gap-2">
                  {TAG_COLORS.map((color) => (
                    <button
                      key={color.value}
                      onClick={() => setNewTagColor(color.value)}
                      className={`w-8 h-8 rounded-full border-2 transition-all ${
                        newTagColor === color.value
                          ? 'border-white scale-110'
                          : 'border-transparent hover:scale-105'
                      }`}
                      style={{ backgroundColor: color.value }}
                      title={color.name}
                    />
                  ))}
                </div>
              </div>
              {/* Preview */}
              <div className="pt-2">
                <label className="text-sm font-medium text-zinc-300 block mb-2">
                  {t('settings.tags.preview')}
                </label>
                <div className="flex gap-2 flex-wrap">
                  <span
                    className="inline-flex items-center gap-2 px-3 py-1.5 rounded-full border text-sm font-medium"
                    style={getTagStyle(newTagColor)}
                  >
                    <TagIcon size={12} />
                    {newTagName || 'Tag Name'}
                  </span>
                  {newTagNameZh && (
                    <span
                      className="inline-flex items-center gap-2 px-3 py-1.5 rounded-full border text-sm font-medium"
                      style={getTagStyle(newTagColor)}
                    >
                      <TagIcon size={12} />
                      {newTagNameZh}
                    </span>
                  )}
                </div>
              </div>
            </div>
            <div className="px-6 py-4 border-t border-zinc-800 bg-zinc-950/50 flex justify-end gap-3">
              <button
                onClick={() => setShowCreateForm(false)}
                className="px-5 py-2.5 rounded-lg border border-zinc-700 text-zinc-300 hover:bg-zinc-800 transition-colors font-medium text-sm"
              >
                {t('common.cancel')}
              </button>
              <button
                onClick={handleCreateTag}
                disabled={!newTagName.trim() || isCreating}
                className="px-5 py-2.5 rounded-lg bg-indigo-600 hover:bg-indigo-500 text-white transition-colors font-medium text-sm disabled:opacity-50 disabled:cursor-not-allowed flex items-center gap-2"
              >
                {isCreating ? (
                  <Loader2 size={14} className="animate-spin" />
                ) : (
                  <Plus size={14} />
                )}
                {t('common.create')}
              </button>
            </div>
          </div>
        </div>
      )}

      {/* Edit Tag Modal */}
      {editingTag && (
        <div className="fixed inset-0 bg-black/80 backdrop-blur-sm z-50 flex items-center justify-center p-4">
          <div className="bg-zinc-900 border border-zinc-800 rounded-2xl w-full max-w-md shadow-2xl overflow-hidden animate-in zoom-in-95 duration-200">
            <div className="px-6 py-4 border-b border-zinc-800 flex justify-between items-center">
              <h3 className="text-lg font-bold text-white">
                {t('settings.tags.editTag')}
              </h3>
              <button
                onClick={handleCancelEdit}
                className="text-zinc-500 hover:text-zinc-300"
              >
                <X size={20} />
              </button>
            </div>
            <div className="p-6 space-y-4">
              <div className="space-y-2">
                <label className="text-sm font-medium text-zinc-300">
                  {t('settings.tags.tagName')}
                </label>
                <input
                  type="text"
                  value={editTagName}
                  onChange={(e) => setEditTagName(e.target.value)}
                  className="w-full px-4 py-3 bg-zinc-950 border border-zinc-800 rounded-lg text-zinc-200 placeholder-zinc-500 focus:outline-none focus:border-indigo-500 transition-colors"
                  autoFocus
                  onKeyDown={(e) => e.key === 'Enter' && handleSaveEdit()}
                />
              </div>
              <div className="space-y-2">
                <label className="text-sm font-medium text-zinc-300">
                  {t('settings.tags.tagColor')}
                </label>
                <div className="flex gap-2">
                  {TAG_COLORS.map((color) => (
                    <button
                      key={color.value}
                      onClick={() => setEditTagColor(color.value)}
                      className={`w-8 h-8 rounded-full border-2 transition-all ${
                        editTagColor === color.value
                          ? 'border-white scale-110'
                          : 'border-transparent hover:scale-105'
                      }`}
                      style={{ backgroundColor: color.value }}
                      title={color.name}
                    />
                  ))}
                </div>
              </div>
              {/* Preview */}
              <div className="pt-2">
                <label className="text-sm font-medium text-zinc-300 block mb-2">
                  {t('settings.tags.preview')}
                </label>
                <span
                  className="inline-flex items-center gap-2 px-3 py-1.5 rounded-full border text-sm font-medium"
                  style={getTagStyle(editTagColor)}
                >
                  <TagIcon size={12} />
                  {editTagName || t('settings.tags.tagNamePlaceholder')}
                </span>
              </div>
            </div>
            <div className="px-6 py-4 border-t border-zinc-800 bg-zinc-950/50 flex justify-between">
              <button
                onClick={() => setDeletingTagId(editingTag.id)}
                className="px-4 py-2.5 rounded-lg border border-red-500/30 text-red-400 hover:bg-red-500/10 transition-colors font-medium text-sm flex items-center gap-2"
              >
                <Trash2 size={14} />
                {t('common.delete')}
              </button>
              <div className="flex gap-3">
                <button
                  onClick={handleCancelEdit}
                  className="px-5 py-2.5 rounded-lg border border-zinc-700 text-zinc-300 hover:bg-zinc-800 transition-colors font-medium text-sm"
                >
                  {t('common.cancel')}
                </button>
                <button
                  onClick={handleSaveEdit}
                  disabled={!editTagName.trim() || isSaving}
                  className="px-5 py-2.5 rounded-lg bg-indigo-600 hover:bg-indigo-500 text-white transition-colors font-medium text-sm disabled:opacity-50 disabled:cursor-not-allowed flex items-center gap-2"
                >
                  {isSaving ? (
                    <Loader2 size={14} className="animate-spin" />
                  ) : (
                    <Check size={14} />
                  )}
                  {t('common.save')}
                </button>
              </div>
            </div>
          </div>
        </div>
      )}
    </section>
  );
};

export default TagsSettings;
