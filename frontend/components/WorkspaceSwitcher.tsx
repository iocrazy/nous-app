import React, { useState, useRef, useEffect } from 'react';
import { ChevronDown, Check, Plus } from 'lucide-react';
import { useTranslation } from 'react-i18next';
import { Team } from '../types';

interface WorkspaceSwitcherProps {
  teams: Team[];
  activeTeamId: string | null;
  currentTeam: Team | null;
  userName?: string;
  onTeamChange: (teamId: string | null) => void;
  onCreateTeam: () => void;
}

// Deterministic color palette for team avatars
const TEAM_COLORS = [
  'bg-indigo-600', 'bg-violet-600', 'bg-pink-600', 'bg-rose-600',
  'bg-orange-600', 'bg-amber-600', 'bg-emerald-600', 'bg-teal-600',
  'bg-cyan-600', 'bg-blue-600',
];

function getTeamColor(name: string): string {
  const code = name.charCodeAt(0) || 0;
  return TEAM_COLORS[code % TEAM_COLORS.length];
}

export const WorkspaceSwitcher: React.FC<WorkspaceSwitcherProps> = ({
  teams,
  activeTeamId,
  currentTeam,
  userName,
  onTeamChange,
  onCreateTeam,
}) => {
  const { t } = useTranslation();
  const [isOpen, setIsOpen] = useState(false);
  const containerRef = useRef<HTMLDivElement>(null);

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
  const displayName = isPersonal
    ? (userName ? `${userName}'s workspace` : (t('sidebar.personal') || 'Personal'))
    : (currentTeam?.name || 'Team');
  const activeColor = isPersonal ? 'bg-zinc-600' : getTeamColor(currentTeam?.name || 'T');
  const activeInitial = isPersonal
    ? (userName?.charAt(0)?.toUpperCase() || 'P')
    : (currentTeam?.name.charAt(0).toUpperCase() || 'T');

  return (
    <div ref={containerRef} className="relative">
      {/* Trigger — single-line: icon + name + chevron + badge */}
      <button
        onClick={() => setIsOpen(!isOpen)}
        className="flex items-center w-full hover:bg-zinc-800/50 rounded-lg px-3 py-2.5 transition-colors group text-left min-w-0"
      >
        <div className={`w-5 h-5 rounded ${activeColor} flex items-center justify-center flex-shrink-0 mr-2.5`}>
          <span className="text-white text-[10px] font-bold leading-none">{activeInitial}</span>
        </div>
        <span className="text-sm font-semibold text-zinc-200 truncate min-w-0">
          {displayName}
        </span>
        <ChevronDown
          size={12}
          className={`text-zinc-500 flex-shrink-0 ml-1 transition-transform duration-200 ${isOpen ? 'rotate-180' : ''}`}
        />
        <span className="text-[10px] text-zinc-500 flex-shrink-0 ml-2">
          {t('plans.free')}
        </span>
      </button>

      {/* Dropdown */}
      {isOpen && (
        <div className="absolute left-0 right-0 top-full mt-1 bg-zinc-900 border border-zinc-700/60 rounded-xl shadow-2xl z-50 animate-in fade-in slide-in-from-top-2 duration-150 overflow-hidden">
          <div className="py-1">
            {/* Personal workspace */}
            <button
              onClick={() => handleSelect(null)}
              className={`flex items-center gap-2.5 w-full px-3 py-2 transition-colors ${
                isPersonal ? 'bg-zinc-800/60' : 'hover:bg-zinc-800/40'
              }`}
            >
              <div className="w-5 h-5 rounded bg-zinc-600 flex items-center justify-center flex-shrink-0">
                <span className="text-white text-[10px] font-bold leading-none">
                  {userName?.charAt(0)?.toUpperCase() || 'P'}
                </span>
              </div>
              <span className="text-sm text-zinc-200 truncate flex-1 text-left">
                {userName ? `${userName}'s workspace` : (t('sidebar.personal') || 'Personal')}
              </span>
              {isPersonal && (
                <Check size={14} className="text-indigo-400 flex-shrink-0" />
              )}
            </button>

            {/* Team list */}
            {teams.map((team) => {
              const color = getTeamColor(team.name);
              const isActive = activeTeamId === team.id;
              return (
                <button
                  key={team.id}
                  onClick={() => handleSelect(team.id)}
                  className={`flex items-center gap-2.5 w-full px-3 py-2 transition-colors ${
                    isActive ? 'bg-zinc-800/60' : 'hover:bg-zinc-800/40'
                  }`}
                >
                  <div className={`w-5 h-5 rounded ${color} flex items-center justify-center flex-shrink-0`}>
                    <span className="text-white text-[10px] font-bold leading-none">
                      {team.name.charAt(0).toUpperCase()}
                    </span>
                  </div>
                  <span className="text-sm text-zinc-200 truncate flex-1 text-left">
                    {team.name}
                  </span>
                  <span className="text-[10px] px-1.5 py-0.5 rounded bg-zinc-700/80 text-zinc-400 flex-shrink-0">
                    {t('plans.free')}
                  </span>
                  {isActive && (
                    <Check size={14} className="text-indigo-400 flex-shrink-0" />
                  )}
                </button>
              );
            })}

            {/* Divider + Create */}
            <div className="my-1 mx-3 border-t border-zinc-800" />
            <button
              onClick={handleCreateTeam}
              className="flex items-center gap-2.5 w-full px-3 py-2 hover:bg-zinc-800/40 transition-colors"
            >
              <div className="w-5 h-5 rounded flex items-center justify-center flex-shrink-0">
                <Plus size={14} className="text-zinc-400" />
              </div>
              <span className="text-sm text-zinc-400">
                {t('user.createTeam') || 'Create new'}
              </span>
            </button>
          </div>
        </div>
      )}
    </div>
  );
};
