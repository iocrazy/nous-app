import React, { useState, useRef, useEffect } from 'react';
import { ChevronDown, Check, Plus, User } from 'lucide-react';
import { useTranslation } from 'react-i18next';
import { Team } from '../types';

interface TeamSwitcherProps {
  teams: Team[];
  activeTeamId: string | null;    // null = personal mode
  currentTeam: Team | null;       // resolved team object
  onTeamChange: (teamId: string | null) => void;  // null = switch to personal
  onCreateTeam: () => void;
}

export const TeamSwitcher: React.FC<TeamSwitcherProps> = ({
  teams,
  activeTeamId,
  currentTeam,
  onTeamChange,
  onCreateTeam,
}) => {
  const { t } = useTranslation();
  const [isOpen, setIsOpen] = useState(false);
  const containerRef = useRef<HTMLDivElement>(null);

  // Close dropdown when clicking outside
  useEffect(() => {
    const handleClickOutside = (e: MouseEvent) => {
      if (containerRef.current && !containerRef.current.contains(e.target as Node)) {
        setIsOpen(false);
      }
    };
    const handleEsc = (e: KeyboardEvent) => {
      if (e.key === 'Escape') setIsOpen(false);
    };

    if (isOpen) {
      document.addEventListener('mousedown', handleClickOutside);
      document.addEventListener('keydown', handleEsc);
    }
    return () => {
      document.removeEventListener('mousedown', handleClickOutside);
      document.removeEventListener('keydown', handleEsc);
    };
  }, [isOpen]);

  const handleSelect = (teamId: string | null) => {
    onTeamChange(teamId);
    setIsOpen(false);
  };

  const handleCreateTeam = () => {
    onCreateTeam();
    setIsOpen(false);
  };

  const isPersonal = activeTeamId === null;

  return (
    <div ref={containerRef} className="relative">
      {/* Trigger Button */}
      <button
        onClick={() => setIsOpen(!isOpen)}
        className="flex items-center gap-2.5 w-full bg-zinc-800/50 hover:bg-zinc-800 border border-zinc-700/50 rounded-xl px-3 py-2.5 transition-colors"
      >
        {isPersonal ? (
          <div className="w-7 h-7 rounded-lg bg-zinc-700 flex items-center justify-center flex-shrink-0">
            <User size={16} className="text-zinc-300" />
          </div>
        ) : (
          <div className="w-7 h-7 rounded-lg bg-indigo-600/20 flex items-center justify-center flex-shrink-0">
            <span className="text-indigo-400 text-sm font-bold">
              {currentTeam?.name.charAt(0).toUpperCase() || 'T'}
            </span>
          </div>
        )}
        <span className="text-sm font-medium text-zinc-200 truncate flex-1 text-left">
          {isPersonal
            ? (t('sidebar.personal') || 'Personal')
            : (currentTeam?.name || 'Team')}
        </span>
        <ChevronDown
          size={16}
          className={`text-zinc-500 flex-shrink-0 transition-transform duration-200 ${isOpen ? 'rotate-180' : ''}`}
        />
      </button>

      {/* Dropdown */}
      {isOpen && (
        <div className="absolute left-0 right-0 top-full mt-1.5 bg-zinc-900 border border-zinc-700/50 rounded-xl shadow-2xl z-50 animate-in fade-in slide-in-from-top-2 duration-200 overflow-hidden">
          <div className="py-1.5">
            {/* Personal option */}
            <button
              onClick={() => handleSelect(null)}
              className="flex items-center gap-2.5 w-full px-3 py-2 hover:bg-zinc-800/70 transition-colors"
            >
              <div className="w-7 h-7 rounded-lg bg-zinc-700 flex items-center justify-center flex-shrink-0">
                <User size={16} className="text-zinc-300" />
              </div>
              <span className="text-sm text-zinc-300 truncate flex-1 text-left">
                {t('sidebar.personal') || 'Personal'}
              </span>
              {isPersonal && (
                <Check size={16} className="text-indigo-400 flex-shrink-0" />
              )}
            </button>

            {/* Divider (if teams exist) */}
            {teams.length > 0 && (
              <div className="my-1.5 mx-3 border-t border-zinc-800" />
            )}

            {/* Team list */}
            {teams.map((team) => (
              <button
                key={team.id}
                onClick={() => handleSelect(team.id)}
                className="flex items-center gap-2.5 w-full px-3 py-2 hover:bg-zinc-800/70 transition-colors"
              >
                <div className="w-7 h-7 rounded-lg bg-indigo-600/20 flex items-center justify-center flex-shrink-0">
                  <span className="text-indigo-400 text-sm font-bold">
                    {team.name.charAt(0).toUpperCase()}
                  </span>
                </div>
                <span className="text-sm text-zinc-300 truncate flex-1 text-left">
                  {team.name}
                </span>
                {activeTeamId === team.id && (
                  <Check size={16} className="text-indigo-400 flex-shrink-0" />
                )}
              </button>
            ))}

            {/* Divider */}
            <div className="my-1.5 mx-3 border-t border-zinc-800" />

            {/* Create Team */}
            <button
              onClick={handleCreateTeam}
              className="flex items-center gap-2.5 w-full px-3 py-2 hover:bg-zinc-800/70 transition-colors"
            >
              <div className="w-7 h-7 rounded-lg flex items-center justify-center flex-shrink-0">
                <Plus size={16} className="text-indigo-400" />
              </div>
              <span className="text-sm text-indigo-400 font-medium">
                {t('user.createTeam') || 'Create Team'}
              </span>
            </button>
          </div>
        </div>
      )}
    </div>
  );
};
