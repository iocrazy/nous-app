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
} from 'lucide-react';
import {
  fetchTags,
  fetchTagGroups,
  createTag,
  updateTag,
  deleteTag,
  Tag,
  TagGroup,
} from '../services/tagsService';

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

  // Group tags by group_name, preserving group sort order
  const groupedTags = useMemo(() => {
    const groupMap = new Map<string, Tag[]>();

    // Initialize groups in order
    for (const group of groups) {
      groupMap.set(group.name, []);
    }
    groupMap.set('__uncategorized__', []);

    for (const tag of filteredTags) {
      const key = tag.group_name || '__uncategorized__';
      if (!groupMap.has(key)) groupMap.set(key, []);
      groupMap.get(key)!.push(tag);
    }

    // Remove empty groups
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
  }, [filteredTags, groups]);

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
                          className={`inline-flex items-center gap-2 px-3 py-1.5 rounded-full border text-sm font-medium transition-all ${
                            tag.type !== 'system'
                              ? 'cursor-pointer hover:scale-105 hover:shadow-lg'
                              : 'cursor-default'
                          }`}
                          style={getTagStyle(tag.color, tag.enabled === false)}
                        >
                          <TagIcon size={12} />
                          <span>{getDisplayName(tag)}</span>
                          <span className="text-xs opacity-60">
                            {tag.media_count ?? 0}
                          </span>
                          {tag.type === 'system' && (
                            <span className="text-xs px-1.5 py-0.5 bg-zinc-700/50 rounded text-zinc-400 ml-1">
                              {t('settings.tags.system')}
                            </span>
                          )}
                          {/* Enabled/Disabled toggle */}
                          <button
                            onClick={(e) => handleToggleEnabled(tag, e)}
                            className={`ml-1 p-0.5 rounded transition-all ${
                              tag.enabled === false
                                ? 'text-zinc-600 hover:text-zinc-400'
                                : 'text-current opacity-40 hover:opacity-100'
                            }`}
                            title={tag.enabled === false ? 'Enable tag' : 'Disable tag'}
                          >
                            {togglingIds.has(tag.id) ? (
                              <Loader2 size={12} className="animate-spin" />
                            ) : tag.enabled === false ? (
                              <EyeOff size={12} />
                            ) : (
                              <Eye size={12} />
                            )}
                          </button>
                          {tag.type !== 'system' && (
                            <button
                              onClick={(e) => { e.stopPropagation(); setDeletingTagId(tag.id); }}
                              className="p-0.5 rounded hover:bg-red-500/20 hover:text-red-400 transition-all opacity-0 group-hover:opacity-100"
                              title={t('common.delete')}
                            >
                              <X size={12} />
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
                  onChange={(e) => setNewTagName(e.target.value)}
                  className="w-full px-4 py-3 bg-zinc-950 border border-zinc-800 rounded-lg text-zinc-200 placeholder-zinc-500 focus:outline-none focus:border-indigo-500 transition-colors"
                  autoFocus
                  onKeyDown={(e) => e.key === 'Enter' && handleCreateTag()}
                />
              </div>
              <div className="space-y-2">
                <label className="text-sm font-medium text-zinc-300">
                  {t('settings.tags.tagName')} (中文)
                  <span className="text-zinc-500 text-xs ml-2">Optional</span>
                </label>
                <input
                  type="text"
                  placeholder="例如：美食、旅行、音乐"
                  value={newTagNameZh}
                  onChange={(e) => setNewTagNameZh(e.target.value)}
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
