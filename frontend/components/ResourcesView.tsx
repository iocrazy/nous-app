import React, { useState, useEffect } from 'react';
import { useTranslation } from 'react-i18next';
import { FolderOpen, Plus, Loader2, Search, ArrowLeft, Film } from 'lucide-react';
import { Collection } from '../types';
import { fetchMyCollections } from '../services/collectionService';

interface ResourcesViewProps {
  teamId: string;
  onCreateCollection: () => void;
}

export const ResourcesView: React.FC<ResourcesViewProps> = ({ teamId, onCreateCollection }) => {
  const { t } = useTranslation();
  const [loading, setLoading] = useState(true);
  const [collections, setCollections] = useState<Collection[]>([]);
  const [searchQuery, setSearchQuery] = useState('');
  const [selectedCollection, setSelectedCollection] = useState<Collection | null>(null);

  useEffect(() => {
    const loadData = async () => {
      setLoading(true);
      try {
        const allCollections = await fetchMyCollections().catch(() => []);
        const teamCollections = allCollections.filter(c => c.team_id === teamId);
        setCollections(teamCollections);
      } finally {
        setLoading(false);
      }
    };
    loadData();
  }, [teamId]);

  const filteredCollections = collections.filter(c => {
    if (!searchQuery) return true;
    return c.name.toLowerCase().includes(searchQuery.toLowerCase());
  });

  if (loading) {
    return (
      <div className="flex items-center justify-center h-64 text-zinc-400">
        <Loader2 className="animate-spin" size={24} />
      </div>
    );
  }

  return (
    <div className="max-w-5xl mx-auto space-y-6 animate-in fade-in slide-in-from-bottom-4 duration-500">
      {selectedCollection ? (
        <>
          {/* Breadcrumb + Back button */}
          <div className="flex items-center gap-2 text-sm">
            <button
              onClick={() => setSelectedCollection(null)}
              className="flex items-center gap-1.5 text-zinc-400 hover:text-zinc-200 transition-colors"
            >
              <ArrowLeft size={16} />
              {t('resources.title')}
            </button>
            <span className="text-zinc-600">/</span>
            <span className="text-zinc-200">{selectedCollection.name}</span>
          </div>

          {/* Collection header */}
          <div className="flex items-center gap-3">
            <h2 className="text-2xl font-bold text-zinc-100">{selectedCollection.name}</h2>
            <span className="bg-zinc-800 text-zinc-400 rounded-full px-2.5 py-0.5 text-sm">
              {selectedCollection.video_count || 0}
            </span>
          </div>

          {/* Placeholder content */}
          <div className="bg-zinc-900 border border-zinc-800 rounded-xl p-12 text-center">
            <Film size={48} className="mx-auto text-zinc-600 mb-4" />
            <p className="text-zinc-400">{t('resources.noVideos')}</p>
          </div>
        </>
      ) : (
        <>
          {/* Header row */}
          <div className="flex items-center justify-between">
            <div className="flex items-center gap-3">
              <h2 className="text-2xl font-bold text-zinc-100">{t('resources.title')}</h2>
              <span className="bg-zinc-800 text-zinc-400 rounded-full px-2.5 py-0.5 text-sm">
                {collections.length}
              </span>
            </div>
            <button
              onClick={onCreateCollection}
              className="flex items-center gap-2 bg-indigo-600 hover:bg-indigo-500 text-white rounded-lg px-4 py-2 text-sm font-medium transition-colors"
            >
              <Plus size={16} />
              {t('resources.newCollection')}
            </button>
          </div>

          {/* Search bar */}
          <div className="relative">
            <Search size={16} className="absolute left-3 top-1/2 -translate-y-1/2 text-zinc-500" />
            <input
              type="text"
              value={searchQuery}
              onChange={(e) => setSearchQuery(e.target.value)}
              placeholder={t('resources.searchPlaceholder')}
              className="w-full bg-zinc-900 border border-zinc-800 rounded-lg pl-10 pr-4 py-2.5 text-sm text-zinc-200 placeholder-zinc-500 focus:outline-none focus:border-indigo-500"
            />
          </div>

          {/* Collection grid or empty state */}
          {filteredCollections.length > 0 ? (
            <div className="grid grid-cols-2 lg:grid-cols-3 gap-4">
              {filteredCollections.map(c => (
                <button
                  key={c.id}
                  onClick={() => setSelectedCollection(c)}
                  className="bg-zinc-900 border border-zinc-800 hover:border-indigo-500/50 rounded-xl overflow-hidden text-left transition-all duration-200 group"
                >
                  {/* Thumbnail area */}
                  <div className="aspect-video bg-zinc-800 relative overflow-hidden">
                    {c.thumbnail_url ? (
                      <img src={c.thumbnail_url} alt={c.name} className="w-full h-full object-cover" />
                    ) : (
                      <div className="w-full h-full flex items-center justify-center">
                        <FolderOpen size={32} className="text-zinc-600" />
                      </div>
                    )}
                  </div>
                  {/* Info */}
                  <div className="p-4">
                    <p className="text-sm font-medium text-zinc-200 group-hover:text-indigo-400 transition-colors truncate">{c.name}</p>
                    <p className="text-xs text-zinc-500 mt-1">{t('resources.videoCount', { count: c.video_count || 0 })}</p>
                  </div>
                </button>
              ))}
            </div>
          ) : (
            <div className="bg-zinc-900 border border-zinc-800 rounded-xl p-12 text-center">
              <FolderOpen size={48} className="mx-auto text-zinc-600 mb-4" />
              <p className="text-zinc-400">{t('resources.noCollections')}</p>
              <p className="text-sm text-zinc-500 mt-1">{t('resources.noCollectionsHint')}</p>
            </div>
          )}
        </>
      )}
    </div>
  );
};
