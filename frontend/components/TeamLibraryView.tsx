import React from 'react';
import { ChevronRight, Loader2, Video } from 'lucide-react';
import { Collection, Team } from '../types';
import { CollectionFolderCard } from './CollectionFolderCard';
import { NewCollectionCard } from './NewCollectionCard';

interface TeamLibraryViewProps {
  collections: Collection[];
  activeCollectionId: string | null;
  onSelectCollection: (collectionId: string) => void;
  onBackToFolders: () => void;
  onCreateCollection: () => void;
  currentTeam: Team | null;
  isLoading?: boolean;
}

export const TeamLibraryView: React.FC<TeamLibraryViewProps> = ({
  collections,
  activeCollectionId,
  onSelectCollection,
  onBackToFolders,
  onCreateCollection,
  currentTeam,
  isLoading = false,
}) => {
  const currentCollection = collections.find(c => c.id === activeCollectionId);

  // Show folder grid when no collection is selected
  if (!activeCollectionId) {
    return (
      <div className="h-full">
        {/* No Team Selected */}
        {!currentTeam && (
          <div className="flex flex-col items-center justify-center h-96 text-zinc-500">
            <p className="text-lg">Select a team to view collections</p>
            <p className="text-sm mt-2">Use the team selector in the header to choose a team</p>
          </div>
        )}

        {/* Loading */}
        {currentTeam && isLoading && (
          <div className="flex flex-col items-center justify-center h-96 text-zinc-500">
            <Loader2 className="w-8 h-8 animate-spin mb-4 text-indigo-500" />
            <p>Loading collections...</p>
          </div>
        )}

        {/* Collection Folder Grid */}
        {currentTeam && !isLoading && (
          <div className="p-6">
            <div className="grid grid-cols-2 md:grid-cols-4 lg:grid-cols-5 xl:grid-cols-6 gap-4">
              <NewCollectionCard onClick={onCreateCollection} />
              {collections.map(collection => (
                <CollectionFolderCard
                  key={collection.id}
                  collection={collection}
                  onClick={() => onSelectCollection(collection.id)}
                />
              ))}
              {collections.length === 0 && (
                <div className="col-span-full text-center py-12 text-zinc-500">
                  <p>No collections yet</p>
                  <p className="text-sm mt-1">Create your first collection to start organizing</p>
                </div>
              )}
            </div>
          </div>
        )}
      </div>
    );
  }

  // Breadcrumb navigation (when inside a collection)
  return (
    <div className="flex items-center gap-2 text-sm text-zinc-400 mb-4 px-6 pt-4">
      <button
        onClick={onBackToFolders}
        className="hover:text-white transition-colors"
      >
        Team Library
      </button>
      <ChevronRight size={14} />
      <span className="text-white">{currentCollection?.name || 'Collection'}</span>
      {currentCollection && (
        <span className="flex items-center gap-1 text-xs px-2 py-0.5 bg-zinc-800 rounded text-zinc-400">
          <span>{currentCollection.video_count || 0}</span>
          <Video size={12} className="text-indigo-400" />
        </span>
      )}
    </div>
  );
};
