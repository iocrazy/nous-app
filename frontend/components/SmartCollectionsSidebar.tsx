/**
 * SmartCollectionsSidebar Component - Display smart collections in sidebar
 */

import React, { useState, useEffect } from 'react';
import {
  Sparkles,
  Clock,
  Heart,
  Eye,
  Tag,
  Loader2,
  RefreshCw,
  ChevronDown,
  Plus,
  Settings,
  Folder,
} from 'lucide-react';
import {
  fetchSmartCollections,
  SmartCollection,
} from '../services/smartCollectionService';

interface SmartCollectionsSidebarProps {
  activeCollectionId: string | null;
  onSelectCollection: (collection: SmartCollection | null) => void;
  onCreateCollection?: () => void;
  isCollapsed?: boolean;
  isInline?: boolean; // When true, renders as inline submenu items without header
}

// Icon mapping for presets
const getCollectionIcon = (icon: string | null, name: string) => {
  const iconMap: Record<string, React.ElementType> = {
    clock: Clock,
    heart: Heart,
    eye: Eye,
    tag: Tag,
    sparkles: Sparkles,
    folder: Folder,
  };

  // Match by icon name or preset name
  if (icon && iconMap[icon.toLowerCase()]) {
    return iconMap[icon.toLowerCase()];
  }

  // Fallback based on name
  const nameLower = name.toLowerCase();
  if (nameLower.includes('recent')) return Clock;
  if (nameLower.includes('favorite')) return Heart;
  if (nameLower.includes('viewed') || nameLower.includes('most')) return Eye;
  if (nameLower.includes('untag')) return Tag;

  return Sparkles;
};

export const SmartCollectionsSidebar: React.FC<SmartCollectionsSidebarProps> = ({
  activeCollectionId,
  onSelectCollection,
  onCreateCollection,
  isCollapsed = false,
  isInline = false,
}) => {
  const [collections, setCollections] = useState<SmartCollection[]>([]);
  const [isLoading, setIsLoading] = useState(true);
  const [isExpanded, setIsExpanded] = useState(true);
  const [error, setError] = useState<string | null>(null);

  // Load collections
  useEffect(() => {
    const loadCollections = async () => {
      setIsLoading(true);
      try {
        const data = await fetchSmartCollections();
        setCollections(data);
        setError(null);
      } catch (err) {
        console.error('Failed to load smart collections:', err);
        setError('Failed to load collections');
      } finally {
        setIsLoading(false);
      }
    };

    loadCollections();
  }, []);

  // Refresh collections
  const handleRefresh = async () => {
    setIsLoading(true);
    try {
      const data = await fetchSmartCollections();
      setCollections(data);
      setError(null);
    } catch (err) {
      console.error('Failed to refresh collections:', err);
    } finally {
      setIsLoading(false);
    }
  };

  // Separate presets and custom collections
  const presetCollections = collections.filter((c) => c.is_preset);
  const customCollections = collections.filter((c) => !c.is_preset);

  if (isCollapsed) {
    return (
      <div className="px-2 py-4 border-t border-ink-800">
        <button
          onClick={() => setIsExpanded(!isExpanded)}
          className="w-full p-2 rounded-lg hover:bg-ink-800 text-ink-400 transition-colors"
          title="Smart Collections"
        >
          <Sparkles size={20} />
        </button>
      </div>
    );
  }

  // Inline mode - renders as simple submenu items (like Settings > General)
  if (isInline) {
    return (
      <>
        {error ? (
          <div className="px-4 py-2 text-xs text-red-400">{error}</div>
        ) : (
          <>
            {/* All Videos */}
            <button
              onClick={() => onSelectCollection(null)}
              className={`w-full text-left px-4 py-2 text-sm rounded-r-lg transition-colors ${
                activeCollectionId === null
                  ? 'text-indigo-400 bg-indigo-500/5'
                  : 'text-ink-500 hover:text-ink-300'
              }`}
            >
              <div className="flex items-center gap-2">
                <Folder size={14} />
                <span>All Videos</span>
              </div>
            </button>

            {/* Preset Collections */}
            {presetCollections.map((collection) => {
              const Icon = getCollectionIcon(collection.icon, collection.name);
              return (
                <button
                  key={collection.id}
                  onClick={() => onSelectCollection(collection)}
                  className={`w-full text-left px-4 py-2 text-sm rounded-r-lg transition-colors ${
                    activeCollectionId === collection.id
                      ? 'text-indigo-400 bg-indigo-500/5'
                      : 'text-ink-500 hover:text-ink-300'
                  }`}
                >
                  <div className="flex items-center justify-between">
                    <div className="flex items-center gap-2">
                      <Icon size={14} />
                      <span>{collection.name}</span>
                    </div>
                    {collection.video_count !== null && (
                      <span className="text-xs text-ink-600">{collection.video_count}</span>
                    )}
                  </div>
                </button>
              );
            })}
          </>
        )}
      </>
    );
  }

  return (
    <div className="border-t border-ink-800">
      {/* Section Header */}
      <button
        onClick={() => setIsExpanded(!isExpanded)}
        className="w-full flex items-center justify-between px-4 py-3 text-xs font-semibold text-ink-400 uppercase tracking-wider hover:bg-ink-800/50 transition-colors"
      >
        <div className="flex items-center gap-2">
          <Sparkles size={14} className="text-indigo-400" />
          Smart Collections
        </div>
        <div className="flex items-center gap-1">
          {isLoading && <Loader2 size={12} className="animate-spin" />}
          <ChevronDown
            size={14}
            className={`transition-transform ${isExpanded ? '' : '-rotate-90'}`}
          />
        </div>
      </button>

      {isExpanded && (
        <div className="pb-2 space-y-1">
          {error ? (
            <div className="px-4 py-2 text-xs text-red-400">{error}</div>
          ) : (
            <>
              {/* All Videos (clear filter) */}
              <CollectionItem
                name="All Videos"
                icon={Folder}
                color="#6b7280"
                videoCount={null}
                isActive={activeCollectionId === null}
                onClick={() => onSelectCollection(null)}
              />

              {/* Preset Collections */}
              {presetCollections.map((collection) => {
                const Icon = getCollectionIcon(collection.icon, collection.name);
                return (
                  <CollectionItem
                    key={collection.id}
                    name={collection.name}
                    icon={Icon}
                    color={collection.color}
                    videoCount={collection.video_count}
                    isActive={activeCollectionId === collection.id}
                    onClick={() => onSelectCollection(collection)}
                    isPreset
                  />
                );
              })}

              {/* Custom Collections */}
              {customCollections.length > 0 && (
                <>
                  <div className="px-4 pt-2 pb-1">
                    <span className="text-[10px] text-ink-600 uppercase tracking-wider">
                      Custom
                    </span>
                  </div>
                  {customCollections.map((collection) => {
                    const Icon = getCollectionIcon(collection.icon, collection.name);
                    return (
                      <CollectionItem
                        key={collection.id}
                        name={collection.name}
                        icon={Icon}
                        color={collection.color}
                        videoCount={collection.video_count}
                        isActive={activeCollectionId === collection.id}
                        onClick={() => onSelectCollection(collection)}
                      />
                    );
                  })}
                </>
              )}

              {/* Create Collection Button */}
              {onCreateCollection && (
                <button
                  onClick={onCreateCollection}
                  className="w-full flex items-center gap-2 px-4 py-2 text-sm text-ink-500 hover:text-ink-300 hover:bg-ink-800/50 transition-colors"
                >
                  <Plus size={16} />
                  New Collection
                </button>
              )}
            </>
          )}
        </div>
      )}
    </div>
  );
};

// Collection item component
interface CollectionItemProps {
  name: string;
  icon: React.ElementType;
  color: string | null;
  videoCount: number | null;
  isActive: boolean;
  onClick: () => void;
  isPreset?: boolean;
}

const CollectionItem: React.FC<CollectionItemProps> = ({
  name,
  icon: Icon,
  color,
  videoCount,
  isActive,
  onClick,
  isPreset = false,
}) => {
  const iconColor = color || '#6b7280';

  return (
    <button
      onClick={onClick}
      className={`w-full flex items-center justify-between px-4 py-2.5 text-sm transition-all group ${
        isActive
          ? 'bg-indigo-600/10 text-indigo-400 border-l-2 border-indigo-500'
          : 'text-ink-400 hover:bg-ink-800/50 hover:text-ink-200 border-l-2 border-transparent'
      }`}
    >
      <div className="flex items-center gap-3">
        <Icon
          size={16}
          style={{ color: isActive ? undefined : iconColor }}
          className={isActive ? 'text-indigo-400' : ''}
        />
        <span className="truncate">{name}</span>
      </div>
      {videoCount !== null && (
        <span
          className={`text-xs ${
            isActive ? 'text-indigo-400/70' : 'text-ink-600 group-hover:text-ink-500'
          }`}
        >
          {videoCount}
        </span>
      )}
    </button>
  );
};

export default SmartCollectionsSidebar;
