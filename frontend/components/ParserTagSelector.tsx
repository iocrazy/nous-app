/**
 * ParserTagSelector Component - Tag selection for parser interface
 * Allows selecting/creating tags before parsing a video (no video ID required)
 */

import React, { useState, useEffect, useRef } from 'react';
import { Tag as TagIcon, Plus, X, Check, Loader2, Search, ChevronDown } from 'lucide-react';
import {
  fetchTags,
  createTag,
  Tag,
} from '../services/tagsService';

interface ParserTagSelectorProps {
  selectedTagIds: string[];
  onTagsChange: (tagIds: string[]) => void;
}

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

// Helper to generate tag style
const getTagStyle = (color: string | null) => {
  const baseColor = color || '#3b82f6';
  return {
    backgroundColor: `${baseColor}15`,
    color: baseColor,
    borderColor: `${baseColor}30`,
  };
};

export const ParserTagSelector: React.FC<ParserTagSelectorProps> = ({
  selectedTagIds,
  onTagsChange,
}) => {
  const [isOpen, setIsOpen] = useState(false);
  const [allTags, setAllTags] = useState<Tag[]>([]);
  const [searchQuery, setSearchQuery] = useState('');
  const [isLoading, setIsLoading] = useState(false);
  const [isCreating, setIsCreating] = useState(false);
  const [newTagName, setNewTagName] = useState('');
  const [newTagColor, setNewTagColor] = useState(TAG_COLORS[5].value);
  const [showCreateForm, setShowCreateForm] = useState(false);
  const dropdownRef = useRef<HTMLDivElement>(null);

  // Load all tags on mount
  useEffect(() => {
    const loadTags = async () => {
      setIsLoading(true);
      try {
        const tags = await fetchTags();
        setAllTags(tags);
      } catch (error) {
        console.error('Failed to load tags:', error);
      } finally {
        setIsLoading(false);
      }
    };
    loadTags();
  }, []);

  // Close dropdown on outside click
  useEffect(() => {
    const handleClickOutside = (event: MouseEvent) => {
      if (dropdownRef.current && !dropdownRef.current.contains(event.target as Node)) {
        setIsOpen(false);
        setShowCreateForm(false);
      }
    };

    if (isOpen) {
      document.addEventListener('mousedown', handleClickOutside);
    }
    return () => document.removeEventListener('mousedown', handleClickOutside);
  }, [isOpen]);

  // Get selected tag objects
  const selectedTags = allTags.filter(t => selectedTagIds.includes(t.id));

  // Filter available tags (not yet selected)
  const filteredTags = allTags.filter(
    (tag) =>
      tag.name.toLowerCase().includes(searchQuery.toLowerCase()) &&
      !selectedTagIds.includes(tag.id)
  );

  const handleAddTag = (tagId: string) => {
    onTagsChange([...selectedTagIds, tagId]);
  };

  const handleRemoveTag = (tagId: string) => {
    onTagsChange(selectedTagIds.filter(id => id !== tagId));
  };

  const handleCreateTag = async () => {
    if (!newTagName.trim()) return;
    setIsCreating(true);
    try {
      const newTag = await createTag({
        name: newTagName.trim(),
        color: newTagColor,
      });
      setAllTags([...allTags, newTag]);
      handleAddTag(newTag.id);
      setNewTagName('');
      setShowCreateForm(false);
    } catch (error) {
      console.error('Failed to create tag:', error);
    } finally {
      setIsCreating(false);
    }
  };

  return (
    <div className="relative" ref={dropdownRef}>
      {/* Trigger Button - Shows selected tags or placeholder */}
      <button
        type="button"
        onClick={() => setIsOpen(!isOpen)}
        className="w-full bg-zinc-900/50 border border-zinc-800 rounded-xl px-4 py-3.5 text-left focus:border-indigo-500 outline-none transition-colors flex items-center justify-between gap-2 hover:border-zinc-700"
      >
        <div className="flex items-center gap-2 flex-1 min-w-0">
          <span className="text-xs font-semibold uppercase tracking-wider text-zinc-600 flex-shrink-0">Tags</span>
          {selectedTags.length > 0 ? (
            <div className="flex flex-wrap gap-1.5 flex-1">
              {selectedTags.slice(0, 3).map((tag) => (
                <span
                  key={tag.id}
                  className="inline-flex items-center gap-1 px-2 py-0.5 rounded-full border text-xs"
                  style={getTagStyle(tag.color)}
                  onClick={(e) => {
                    e.stopPropagation();
                    handleRemoveTag(tag.id);
                  }}
                >
                  {tag.name}
                  <X size={10} className="opacity-60 hover:opacity-100" />
                </span>
              ))}
              {selectedTags.length > 3 && (
                <span className="text-xs text-zinc-500">+{selectedTags.length - 3}</span>
              )}
            </div>
          ) : (
            <span className="text-zinc-600 text-sm">Select tags (Optional)</span>
          )}
        </div>
        <ChevronDown size={16} className={`text-zinc-500 transition-transform ${isOpen ? 'rotate-180' : ''}`} />
      </button>

      {/* Dropdown */}
      {isOpen && (
        <div className="absolute top-full left-0 right-0 mt-2 bg-zinc-900 border border-zinc-700 rounded-xl shadow-xl z-50 overflow-hidden animate-in fade-in slide-in-from-top-2 duration-200">
          {/* Search */}
          <div className="p-2 border-b border-zinc-800">
            <div className="relative">
              <Search size={14} className="absolute left-3 top-1/2 -translate-y-1/2 text-zinc-500" />
              <input
                type="text"
                placeholder="Search tags..."
                value={searchQuery}
                onChange={(e) => setSearchQuery(e.target.value)}
                className="w-full pl-9 pr-3 py-2 bg-zinc-800 border border-zinc-700 rounded-lg text-sm text-white placeholder-zinc-500 focus:outline-none focus:border-indigo-500"
              />
            </div>
          </div>

          {/* Selected Tags */}
          {selectedTags.length > 0 && (
            <div className="p-2 border-b border-zinc-800">
              <p className="text-xs text-zinc-500 px-2 mb-2">Selected</p>
              <div className="flex flex-wrap gap-1.5">
                {selectedTags.map((tag) => (
                  <span
                    key={tag.id}
                    className="inline-flex items-center gap-1 px-2 py-0.5 rounded-full border text-xs cursor-pointer hover:opacity-80"
                    style={getTagStyle(tag.color)}
                    onClick={() => handleRemoveTag(tag.id)}
                  >
                    {tag.name}
                    <X size={10} />
                  </span>
                ))}
              </div>
            </div>
          )}

          {/* Available Tags */}
          <div className="max-h-40 overflow-y-auto">
            {isLoading ? (
              <div className="p-4 text-center">
                <Loader2 size={16} className="animate-spin mx-auto text-zinc-500" />
              </div>
            ) : filteredTags.length > 0 ? (
              <div className="p-2 space-y-1">
                {filteredTags.map((tag) => (
                  <button
                    key={tag.id}
                    type="button"
                    onClick={() => handleAddTag(tag.id)}
                    className="w-full flex items-center justify-between px-3 py-2 rounded-lg hover:bg-zinc-800 transition-colors"
                  >
                    <span
                      className="inline-flex items-center gap-2 text-sm"
                      style={{ color: tag.color || '#fff' }}
                    >
                      <TagIcon size={14} />
                      {tag.name}
                    </span>
                    <span className="text-xs text-zinc-500">{tag.video_count ?? 0} videos</span>
                  </button>
                ))}
              </div>
            ) : (
              <div className="p-4 text-center text-sm text-zinc-500">
                {searchQuery ? 'No matching tags' : 'No available tags'}
              </div>
            )}
          </div>

          {/* Create New Tag */}
          <div className="border-t border-zinc-800">
            {showCreateForm ? (
              <div className="p-3 space-y-3">
                <input
                  type="text"
                  placeholder="Tag name"
                  value={newTagName}
                  onChange={(e) => setNewTagName(e.target.value)}
                  className="w-full px-3 py-2 bg-zinc-800 border border-zinc-700 rounded-lg text-sm text-white placeholder-zinc-500 focus:outline-none focus:border-indigo-500"
                  autoFocus
                />
                <div className="flex gap-1.5">
                  {TAG_COLORS.map((color) => (
                    <button
                      key={color.value}
                      type="button"
                      onClick={() => setNewTagColor(color.value)}
                      className={`w-6 h-6 rounded-full border-2 transition-all ${
                        newTagColor === color.value ? 'border-white scale-110' : 'border-transparent'
                      }`}
                      style={{ backgroundColor: color.value }}
                      title={color.name}
                    />
                  ))}
                </div>
                <div className="flex gap-2">
                  <button
                    type="button"
                    onClick={() => setShowCreateForm(false)}
                    className="flex-1 px-3 py-2 bg-zinc-800 hover:bg-zinc-700 text-zinc-300 rounded-lg text-sm transition-colors"
                  >
                    Cancel
                  </button>
                  <button
                    type="button"
                    onClick={handleCreateTag}
                    disabled={!newTagName.trim() || isCreating}
                    className="flex-1 px-3 py-2 bg-indigo-600 hover:bg-indigo-500 text-white rounded-lg text-sm transition-colors disabled:opacity-50 flex items-center justify-center gap-2"
                  >
                    {isCreating ? <Loader2 size={14} className="animate-spin" /> : <Check size={14} />}
                    Create
                  </button>
                </div>
              </div>
            ) : (
              <button
                type="button"
                onClick={() => setShowCreateForm(true)}
                className="w-full flex items-center justify-center gap-2 px-4 py-3 text-sm text-indigo-400 hover:bg-zinc-800 transition-colors"
              >
                <Plus size={16} />
                Create New Tag
              </button>
            )}
          </div>
        </div>
      )}
    </div>
  );
};

export default ParserTagSelector;
