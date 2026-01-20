import React from 'react';
import { Plus } from 'lucide-react';

interface NewCollectionCardProps {
  onClick: () => void;
}

export const NewCollectionCard: React.FC<NewCollectionCardProps> = ({ onClick }) => {
  return (
    <div
      className="cursor-pointer bg-zinc-900/50 rounded-xl border border-dashed border-zinc-700
                 hover:border-indigo-500 hover:bg-zinc-800/30 transition-all overflow-hidden"
      onClick={onClick}
    >
      {/* Thumbnail area - matches CollectionFolderCard */}
      <div className="aspect-square flex flex-col items-center justify-center">
        <div className="w-16 h-16 bg-zinc-800/50 rounded-full flex items-center justify-center mb-3">
          <Plus size={32} className="text-zinc-500" />
        </div>
        <span className="text-sm text-zinc-500">New Collection</span>
      </div>

      {/* Info area placeholder to match height */}
      <div className="p-3 invisible">
        <div className="h-4"></div>
        <div className="h-3 mt-1"></div>
      </div>
    </div>
  );
};
