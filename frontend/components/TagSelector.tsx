/**
 * TagSelector Component - Tag management for videos
 */

import React, { useState, useEffect, useRef } from 'react';
import { Tag as TagIcon, Plus, X, Check, Loader2, Search } from 'lucide-react';
import {
  fetchTags,
  createTag,
  addTagsToVideo,
  removeTagFromVideo,
  getVideoTags,
  generateTagColor,
  Tag,
} from '../services/tagsService';

interface TagSelectorProps {
  videoId: string;
  onTagsChange?: (tags: Tag[]) => void;
  compact?: boolean;
  inline?: boolean;  // When true, shows dropdown content directly without toggle button
  initialTagNames?: string[];  // Initial tag names to display immediately (from data.tags)
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

// Helper to generate consistent colors from strings
const getTagStyle = (color: string | null) => {
  const baseColor = color || '#3b82f6';
  return {
    backgroundColor: `${baseColor}15`,
    color: baseColor,
    borderColor: `${baseColor}30`,
  };
};

export const TagSelector: React.FC<TagSelectorProps> = ({
  videoId,
  onTagsChange,
  compact = false,
  inline = false,
  initialTagNames = [],
}) => {
  const [isOpen, setIsOpen] = useState(inline);  // Always open in inline mode
  const [allTags, setAllTags] = useState<Tag[]>([]);
  const [videoTags, setVideoTags] = useState<Tag[]>([]);
  const [displayTagNames, setDisplayTagNames] = useState<string[]>(initialTagNames);  // For immediate display
  const [searchQuery, setSearchQuery] = useState('');
  const [isLoading, setIsLoading] = useState(false);
  const [isCreating, setIsCreating] = useState(false);
  const [newTagName, setNewTagName] = useState('');
  const [newTagColor, setNewTagColor] = useState(TAG_COLORS[5].value);
  const [showCreateForm, setShowCreateForm] = useState(false);
  const dropdownRef = useRef<HTMLDivElement>(null);

  // Update display names when initialTagNames changes
  useEffect(() => {
    setDisplayTagNames(initialTagNames);
  }, [initialTagNames]);

  // Load full tag objects only when dropdown is opened (for editing)
  useEffect(() => {
    const loadVideoTags = async () => {
      try {
        const currentTags = await getVideoTags(videoId);
        setVideoTags(currentTags);
        // Update display names with full data
        setDisplayTagNames(currentTags.map(t => t.name));
      } catch (error) {
        console.error('Failed to load video tags:', error);
      }
    };
    // Only fetch when dropdown opens or in inline mode
    if (isOpen || inline) {
      loadVideoTags();
    }
  }, [videoId, isOpen, inline]);

  // Load all available tags when dropdown is opened
  useEffect(() => {
    const loadAllTags = async () => {
      try {
        const tags = await fetchTags();
        setAllTags(tags);
      } catch (error) {
        console.error('Failed to load tags:', error);
      }
    };

    if (isOpen || inline) {
      loadAllTags();
    }
  }, [isOpen, inline]);

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

  // Filter tags based on search
  const filteredTags = allTags.filter(
    (tag) =>
      tag.name.toLowerCase().includes(searchQuery.toLowerCase()) &&
      !videoTags.some((vt) => vt.id === tag.id)
  );

  const handleAddTag = async (tag: Tag) => {
    setIsLoading(true);
    try {
      await addTagsToVideo(videoId, [tag.id]);
      const newVideoTags = [...videoTags, tag];
      setVideoTags(newVideoTags);
      onTagsChange?.(newVideoTags);
    } catch (error) {
      console.error('Failed to add tag:', error);
    } finally {
      setIsLoading(false);
    }
  };

  const handleRemoveTag = async (tagId: string) => {
    setIsLoading(true);
    try {
      await removeTagFromVideo(videoId, tagId);
      const newVideoTags = videoTags.filter((t) => t.id !== tagId);
      setVideoTags(newVideoTags);
      onTagsChange?.(newVideoTags);
    } catch (error) {
      console.error('Failed to remove tag:', error);
    } finally {
      setIsLoading(false);
    }
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
      await handleAddTag(newTag);
      setNewTagName('');
      setShowCreateForm(false);
    } catch (error) {
      console.error('Failed to create tag:', error);
    } finally {
      setIsCreating(false);
    }
  };

  // Inline mode - render dropdown content directly without wrapper
  if (inline) {
    return (
      <TagDropdownContent
        videoTags={videoTags}
        filteredTags={filteredTags}
        searchQuery={searchQuery}
        setSearchQuery={setSearchQuery}
        showCreateForm={showCreateForm}
        setShowCreateForm={setShowCreateForm}
        newTagName={newTagName}
        setNewTagName={setNewTagName}
        newTagColor={newTagColor}
        setNewTagColor={setNewTagColor}
        isLoading={isLoading}
        isCreating={isCreating}
        onAddTag={handleAddTag}
        onRemoveTag={handleRemoveTag}
        onCreateTag={handleCreateTag}
      />
    );
  }

  if (compact) {
    return (
      <div className="relative" ref={dropdownRef}>
        <button
          onClick={() => setIsOpen(!isOpen)}
          className="p-2 rounded-lg bg-zinc-800 hover:bg-zinc-700 text-zinc-400 hover:text-white transition-colors border border-zinc-700"
          title="Manage Tags"
        >
          <TagIcon size={16} />
        </button>

        {isOpen && (
          <div className="absolute bottom-full right-0 mb-2 w-72 bg-zinc-900 border border-zinc-700 rounded-xl shadow-xl z-50 overflow-hidden">
            <TagDropdownContent
              videoTags={videoTags}
              filteredTags={filteredTags}
              searchQuery={searchQuery}
              setSearchQuery={setSearchQuery}
              showCreateForm={showCreateForm}
              setShowCreateForm={setShowCreateForm}
              newTagName={newTagName}
              setNewTagName={setNewTagName}
              newTagColor={newTagColor}
              setNewTagColor={setNewTagColor}
              isLoading={isLoading}
              isCreating={isCreating}
              onAddTag={handleAddTag}
              onRemoveTag={handleRemoveTag}
              onCreateTag={handleCreateTag}
            />
          </div>
        )}
      </div>
    );
  }

  // Use videoTags if loaded, otherwise show displayTagNames for immediate display
  const tagsToDisplay = videoTags.length > 0 ? videoTags : displayTagNames.map((name, i) => ({
    id: `temp-${i}`,
    name,
    color: null,
    icon: null,
    type: 'user' as const,
    created_at: '',
  }));

  return (
    <div className="space-y-3">
      {/* Current Tags */}
      <div className="flex flex-wrap gap-2">
        {tagsToDisplay.map((tag) => (
          <span
            key={tag.id}
            className="inline-flex items-center gap-1.5 px-2.5 py-1 rounded-full border text-xs font-medium"
            style={getTagStyle(tag.color)}
          >
            <TagIcon size={10} />
            {tag.name}
            {/* Only show remove button if we have full tag data */}
            {videoTags.length > 0 && (
              <button
                onClick={() => handleRemoveTag(tag.id)}
                className="ml-1 hover:opacity-70 transition-opacity"
                disabled={isLoading}
              >
                <X size={12} />
              </button>
            )}
          </span>
        ))}

        {/* Add Tag Button */}
        <div className="relative" ref={dropdownRef}>
          <button
            onClick={() => setIsOpen(!isOpen)}
            className="inline-flex items-center gap-1.5 px-2.5 py-1 rounded-full border border-dashed border-zinc-600 text-zinc-400 hover:text-white hover:border-zinc-500 text-xs font-medium transition-colors"
          >
            <Plus size={12} />
            Add Tag
          </button>

          {isOpen && (
            <div className="absolute top-full left-0 mt-2 w-72 bg-zinc-900 border border-zinc-700 rounded-xl shadow-xl z-50 overflow-hidden">
              <TagDropdownContent
                videoTags={videoTags}
                filteredTags={filteredTags}
                searchQuery={searchQuery}
                setSearchQuery={setSearchQuery}
                showCreateForm={showCreateForm}
                setShowCreateForm={setShowCreateForm}
                newTagName={newTagName}
                setNewTagName={setNewTagName}
                newTagColor={newTagColor}
                setNewTagColor={setNewTagColor}
                isLoading={isLoading}
                isCreating={isCreating}
                onAddTag={handleAddTag}
                onRemoveTag={handleRemoveTag}
                onCreateTag={handleCreateTag}
              />
            </div>
          )}
        </div>
      </div>
    </div>
  );
};

// Extracted dropdown content component
interface TagDropdownContentProps {
  videoTags: Tag[];
  filteredTags: Tag[];
  searchQuery: string;
  setSearchQuery: (query: string) => void;
  showCreateForm: boolean;
  setShowCreateForm: (show: boolean) => void;
  newTagName: string;
  setNewTagName: (name: string) => void;
  newTagColor: string;
  setNewTagColor: (color: string) => void;
  isLoading: boolean;
  isCreating: boolean;
  onAddTag: (tag: Tag) => void;
  onRemoveTag: (tagId: string) => void;
  onCreateTag: () => void;
}

const TagDropdownContent: React.FC<TagDropdownContentProps> = ({
  videoTags,
  filteredTags,
  searchQuery,
  setSearchQuery,
  showCreateForm,
  setShowCreateForm,
  newTagName,
  setNewTagName,
  newTagColor,
  setNewTagColor,
  isLoading,
  isCreating,
  onAddTag,
  onRemoveTag,
  onCreateTag,
}) => {
  return (
    <>
      {/* Header */}
      <div className="px-4 py-3 border-b border-zinc-800">
        <h4 className="text-sm font-semibold text-white">Tags</h4>
      </div>

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

      {/* Current Tags */}
      {videoTags.length > 0 && (
        <div className="p-2 border-b border-zinc-800">
          <p className="text-xs text-zinc-500 px-2 mb-2">Current Tags</p>
          <div className="flex flex-wrap gap-1.5">
            {videoTags.map((tag) => (
              <span
                key={tag.id}
                className="inline-flex items-center gap-1 px-2 py-0.5 rounded-full border text-xs"
                style={getTagStyle(tag.color)}
              >
                {tag.name}
                <button
                  onClick={() => onRemoveTag(tag.id)}
                  className="hover:opacity-70"
                  disabled={isLoading}
                >
                  <X size={10} />
                </button>
              </span>
            ))}
          </div>
        </div>
      )}

      {/* Available Tags */}
      <div className="max-h-40 overflow-y-auto">
        {filteredTags.length > 0 ? (
          <div className="p-2 space-y-1">
            {filteredTags.map((tag) => (
              <button
                key={tag.id}
                onClick={() => onAddTag(tag)}
                disabled={isLoading}
                className="w-full flex items-center justify-between px-3 py-2 rounded-lg hover:bg-zinc-800 transition-colors group"
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
                onClick={() => setShowCreateForm(false)}
                className="flex-1 px-3 py-2 bg-zinc-800 hover:bg-zinc-700 text-zinc-300 rounded-lg text-sm transition-colors"
              >
                Cancel
              </button>
              <button
                onClick={onCreateTag}
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
            onClick={() => setShowCreateForm(true)}
            className="w-full flex items-center justify-center gap-2 px-4 py-3 text-sm text-indigo-400 hover:bg-zinc-800 transition-colors"
          >
            <Plus size={16} />
            Create New Tag
          </button>
        )}
      </div>
    </>
  );
};

export default TagSelector;
