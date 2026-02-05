/**
 * TagsSettings Component - Tag management in Settings
 */

import React, { useState, useEffect } from 'react';
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
} from 'lucide-react';
import {
  fetchTags,
  createTag,
  updateTag,
  deleteTag,
  Tag,
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

// Bilingual tag mapping for display
const TAG_TRANSLATIONS: Record<string, { en: string; zh: string }> = {
  // English to Chinese
  'Sports': { en: 'Sports', zh: '运动' },
  'Drama': { en: 'Drama', zh: '剧情' },
  'Music': { en: 'Music', zh: '音乐' },
  'Text': { en: 'Text', zh: '文字' },
  'Family': { en: 'Family', zh: '亲子' },
  'Beauty': { en: 'Beauty', zh: '颜值' },
  'Filming': { en: 'Filming', zh: '拍摄' },
  'Post-production': { en: 'Post-production', zh: '后期' },
  'Recreation': { en: 'Recreation', zh: '仿拍' },
  'Travel': { en: 'Travel', zh: '旅行' },
  'Tech': { en: 'Tech', zh: '科技' },
  'Finance': { en: 'Finance', zh: '财经' },
  'Variety': { en: 'Variety', zh: '综艺' },
  'Fashion': { en: 'Fashion', zh: '时尚' },
  'Comedy': { en: 'Comedy', zh: '搞笑' },
  'Tutorial': { en: 'Tutorial', zh: '教程' },
  'Other': { en: 'Other', zh: '其他' },
  'Food': { en: 'Food', zh: '美食' },
  'Dance': { en: 'Dance', zh: '舞蹈' },
  'Pets': { en: 'Pets', zh: '宠物' },
  'Gaming': { en: 'Gaming', zh: '游戏' },
  'Vlog': { en: 'Vlog', zh: '日常' },
  // Chinese to English (reverse mapping)
  '运动': { en: 'Sports', zh: '运动' },
  '剧情': { en: 'Drama', zh: '剧情' },
  '音乐': { en: 'Music', zh: '音乐' },
  '文字': { en: 'Text', zh: '文字' },
  '亲子': { en: 'Family', zh: '亲子' },
  '颜值': { en: 'Beauty', zh: '颜值' },
  '拍摄': { en: 'Filming', zh: '拍摄' },
  '后期': { en: 'Post-production', zh: '后期' },
  '仿拍': { en: 'Recreation', zh: '仿拍' },
  '旅行': { en: 'Travel', zh: '旅行' },
  '科技': { en: 'Tech', zh: '科技' },
  '财经': { en: 'Finance', zh: '财经' },
  '综艺': { en: 'Variety', zh: '综艺' },
  '时尚': { en: 'Fashion', zh: '时尚' },
  '搞笑': { en: 'Comedy', zh: '搞笑' },
  '教程': { en: 'Tutorial', zh: '教程' },
  '其他': { en: 'Other', zh: '其他' },
  '美食': { en: 'Food', zh: '美食' },
  '舞蹈': { en: 'Dance', zh: '舞蹈' },
  '宠物': { en: 'Pets', zh: '宠物' },
  '游戏': { en: 'Gaming', zh: '游戏' },
  '日常': { en: 'Vlog', zh: '日常' },
};

// Predefined tags with bilingual support
const PREDEFINED_TAGS: { en: string; zh: string; color: string }[] = [
  { en: 'Food', zh: '美食', color: '#f97316' },
  { en: 'Tutorial', zh: '教程', color: '#14b8a6' },
  { en: 'Comedy', zh: '搞笑', color: '#eab308' },
  { en: 'Dance', zh: '舞蹈', color: '#ec4899' },
  { en: 'Music', zh: '音乐', color: '#ec4899' },
  { en: 'Beauty', zh: '颜值', color: '#ec4899' },
  { en: 'Fashion', zh: '时尚', color: '#ec4899' },
  { en: 'Gaming', zh: '游戏', color: '#8b5cf6' },
  { en: 'Pets', zh: '宠物', color: '#f97316' },
  { en: 'Travel', zh: '旅行', color: '#22c55e' },
  { en: 'Tech', zh: '科技', color: '#3b82f6' },
  { en: 'Sports', zh: '运动', color: '#22c55e' },
  { en: 'Vlog', zh: '日常', color: '#3b82f6' },
  { en: 'Other', zh: '其他', color: '#71717a' },
  { en: 'Drama', zh: '剧情', color: '#8b5cf6' },
  { en: 'Text', zh: '文字', color: '#3b82f6' },
  { en: 'Family', zh: '亲子', color: '#f97316' },
  { en: 'Filming', zh: '拍摄', color: '#14b8a6' },
  { en: 'Post-production', zh: '后期', color: '#8b5cf6' },
  { en: 'Recreation', zh: '仿拍', color: '#eab308' },
  { en: 'Finance', zh: '财经', color: '#ef4444' },
  { en: 'Variety', zh: '综艺', color: '#f97316' },
];

// Helper to generate tag style from color
const getTagStyle = (color: string | null) => {
  const baseColor = color || '#3b82f6';
  return {
    backgroundColor: `${baseColor}15`,
    color: baseColor,
    borderColor: `${baseColor}30`,
  };
};

export const TagsSettings: React.FC = () => {
  const { t, i18n } = useTranslation();
  const isZh = i18n.language?.startsWith('zh');

  const [tags, setTags] = useState<Tag[]>([]);
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

  // Load tags
  useEffect(() => {
    loadTags();
  }, []);

  const loadTags = async () => {
    setIsLoading(true);
    setError(null);
    try {
      const fetchedTags = await fetchTags();
      setTags(fetchedTags);
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Failed to load tags');
    } finally {
      setIsLoading(false);
    }
  };

  // Get display name based on current language
  // Priority: database name_zh > TAG_TRANSLATIONS > original name
  const getDisplayName = (tag: Tag): string => {
    if (isZh) {
      // Chinese mode: use name_zh from DB, fallback to translation map, then original
      if (tag.name_zh) return tag.name_zh;
      const translation = TAG_TRANSLATIONS[tag.name];
      if (translation) return translation.zh;
      return tag.name;
    } else {
      // English mode: use original name
      return tag.name;
    }
  };

  // Filter tags based on search (search both English and Chinese names)
  const filteredTags = tags.filter((tag) => {
    const query = searchQuery.toLowerCase();
    const nameMatch = tag.name.toLowerCase().includes(query);
    const nameZhMatch = tag.name_zh?.toLowerCase().includes(query);
    const translationMatch = TAG_TRANSLATIONS[tag.name]?.zh.toLowerCase().includes(query);
    return nameMatch || nameZhMatch || translationMatch;
  });

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
      setTags([...tags, newTag]);
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
      const updated = await updateTag(editingTag.id as unknown as number, {
        name: editTagName.trim(),
        color: editTagColor,
      });
      setTags(tags.map((t) => (t.id === editingTag.id ? updated : t)));
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
      await deleteTag(tagId as unknown as number);
      setTags(tags.filter((t) => t.id !== tagId));
      setDeletingTagId(null);
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Failed to delete tag');
    }
  };

  // Create predefined tags with bilingual support
  const handleCreatePredefinedTags = async () => {
    setIsCreating(true);
    try {
      // Check existing tags by both English and Chinese names
      const existingNames = tags.map((t) => t.name.toLowerCase());
      const existingNamesZh = tags.map((t) => t.name_zh?.toLowerCase()).filter(Boolean);
      const tagsToCreate = PREDEFINED_TAGS.filter(
        (pt) =>
          !existingNames.includes(pt.en.toLowerCase()) &&
          !existingNamesZh.includes(pt.zh.toLowerCase())
      );

      for (const predefined of tagsToCreate) {
        // Always store English as primary name, Chinese as name_zh
        const newTag = await createTag({
          name: predefined.en,
          name_zh: predefined.zh,
          color: predefined.color,
        });
        setTags((prev) => [...prev, newTag]);
      }
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Failed to create tags');
    } finally {
      setIsCreating(false);
    }
  };

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
            <p className="text-xs text-zinc-500">{t('settings.tags.subtitle')}</p>
          </div>
        </div>
        <div className="flex items-center gap-2">
          <button
            onClick={handleCreatePredefinedTags}
            disabled={isCreating}
            className="px-3 py-2 rounded-lg border border-zinc-700 text-zinc-300 hover:bg-zinc-800 transition-colors text-sm font-medium flex items-center gap-2"
            title={t('settings.tags.addPresetHint')}
          >
            {isCreating ? (
              <Loader2 size={14} className="animate-spin" />
            ) : (
              <Plus size={14} />
            )}
            {t('settings.tags.addPreset')}
          </button>
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

      {/* Tags Grid - Compact flex layout */}
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
          <div className="flex flex-wrap gap-2">
            {filteredTags.map((tag) => (
              <div
                key={tag.id}
                className="group relative"
              >
                {deletingTagId === tag.id ? (
                  // Delete confirmation
                  <div className="inline-flex items-center gap-1 px-3 py-1.5 rounded-full border border-red-500/30 bg-red-500/10 text-sm">
                    <span className="text-red-400 mr-1">{t('common.delete')}?</span>
                    <button
                      onClick={(e) => handleDeleteTag(tag.id, e)}
                      className="p-0.5 text-red-400 hover:text-red-300"
                      title={t('common.confirm')}
                    >
                      <Check size={14} />
                    </button>
                    <button
                      onClick={(e) => { e.stopPropagation(); setDeletingTagId(null); }}
                      className="p-0.5 text-zinc-400 hover:text-zinc-300"
                      title={t('common.cancel')}
                    >
                      <X size={14} />
                    </button>
                  </div>
                ) : (
                  // Normal tag display
                  <div
                    onClick={() => tag.type !== 'system' && handleStartEdit(tag)}
                    className={`inline-flex items-center gap-2 px-3 py-1.5 rounded-full border text-sm font-medium transition-all ${
                      tag.type !== 'system'
                        ? 'cursor-pointer hover:scale-105 hover:shadow-lg'
                        : 'cursor-default opacity-70'
                    }`}
                    style={getTagStyle(tag.color)}
                    title={tag.type !== 'system' ? t('settings.tags.clickToEdit') : t('settings.tags.system')}
                  >
                    <TagIcon size={12} />
                    <span>{getDisplayName(tag)}</span>
                    <span className="text-xs opacity-60">
                      {tag.video_count ?? 0}
                    </span>
                    {tag.type === 'system' && (
                      <span className="text-xs px-1.5 py-0.5 bg-zinc-700/50 rounded text-zinc-400 ml-1">
                        {t('settings.tags.system')}
                      </span>
                    )}
                    {tag.type !== 'system' && (
                      <button
                        onClick={(e) => { e.stopPropagation(); setDeletingTagId(tag.id); }}
                        className="ml-1 p-0.5 rounded hover:bg-red-500/20 hover:text-red-400 transition-all"
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
