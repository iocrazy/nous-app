import React from 'react';
import { Users } from 'lucide-react';
import { Team } from '../types';

export type LibraryTab = 'my-library' | 'team-library';

interface LibraryTabsProps {
  activeTab: LibraryTab;
  onTabChange: (tab: LibraryTab) => void;
  currentTeam: Team | null;
  onTeamClick?: () => void;
}

export const LibraryTabs: React.FC<LibraryTabsProps> = ({
  activeTab,
  onTabChange,
  currentTeam,
  onTeamClick,
}) => {
  return (
    <div className="flex items-center gap-1 border-b border-zinc-800 mb-4">
      <button
        className={`px-4 py-2.5 text-sm font-medium transition-all border-b-2 -mb-[1px] ${
          activeTab === 'my-library'
            ? 'text-white border-indigo-500'
            : 'text-zinc-400 border-transparent hover:text-zinc-200 hover:border-zinc-600'
        }`}
        onClick={() => onTabChange('my-library')}
      >
        My Library
      </button>

      <button
        className={`px-4 py-2.5 text-sm font-medium transition-all border-b-2 -mb-[1px] flex items-center gap-2 ${
          activeTab === 'team-library'
            ? 'text-white border-indigo-500'
            : 'text-zinc-400 border-transparent hover:text-zinc-200 hover:border-zinc-600'
        }`}
        onClick={() => onTabChange('team-library')}
      >
        <Users size={14} />
        <span>Team Library</span>
        {currentTeam && (
          <span
            className="text-xs px-2 py-0.5 bg-zinc-700 rounded text-zinc-300 hover:bg-zinc-600 cursor-pointer"
            onClick={(e) => {
              e.stopPropagation();
              onTeamClick?.();
            }}
          >
            {currentTeam.name}
          </span>
        )}
        {!currentTeam && (
          <span className="text-xs px-2 py-0.5 bg-zinc-800/50 rounded-full text-zinc-500">
            Select Team
          </span>
        )}
      </button>
    </div>
  );
};
