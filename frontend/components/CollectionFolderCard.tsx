import React from 'react';
import { Folder, Video, Image, Music } from 'lucide-react';
import { Collection } from '../types';

interface CollectionFolderCardProps {
  collection: Collection;
  onClick: () => void;
}

export const CollectionFolderCard: React.FC<CollectionFolderCardProps> = ({
  collection,
  onClick,
}) => {
  // Format date like CapCut: YYYY-MM-DD HH:mm
  const formatDate = (dateStr: string) => {
    const date = new Date(dateStr);
    return date.toLocaleString('zh-CN', {
      year: 'numeric',
      month: '2-digit',
      day: '2-digit',
      hour: '2-digit',
      minute: '2-digit',
      hour12: false,
    }).replace(/\//g, '-');
  };

  return (
    <div
      className="group cursor-pointer bg-ink-900 rounded-xl overflow-hidden
                 hover:ring-2 hover:ring-ink-600 transition-all"
      onClick={onClick}
    >
      {/* Thumbnail area - square aspect ratio */}
      <div className="aspect-square relative bg-ink-800 overflow-hidden">
        {collection.thumbnail_url ? (
          <img
            src={collection.thumbnail_url}
            alt={collection.name}
            className="w-full h-full object-cover group-hover:scale-105 transition-transform duration-300"
          />
        ) : (
          <div className="w-full h-full flex items-center justify-center bg-gradient-to-br from-ink-800 to-ink-900">
            <Folder size={48} className="text-ink-600" />
          </div>
        )}
        {/* Media count badge with icon */}
        <div className="absolute bottom-2 right-2 flex items-center gap-1 px-2 py-0.5 bg-black/70 rounded text-xs text-white">
          <span>{collection.video_count || 0}</span>
          <Video size={12} className="text-indigo-400" />
        </div>
      </div>

      {/* Info area */}
      <div className="p-3">
        <h3 className="text-sm font-medium text-ink-200 truncate">
          {collection.name}
        </h3>
        <p className="text-xs text-ink-500 mt-1">
          {formatDate(collection.created_at)}
        </p>
      </div>
    </div>
  );
};
