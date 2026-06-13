import React, { useEffect, useRef } from 'react';
import { User, Settings, LogOut, Users, Plus, ChevronRight, Check } from 'lucide-react';
import { useTranslation } from 'react-i18next';
import { LanguageSwitcher } from './LanguageSwitcher';

interface Team {
  id: string;
  name: string;
  isOwner: boolean;
}

interface UserDropdownProps {
  isOpen: boolean;
  onClose: () => void;
  user: {
    name: string;
    email: string;
    avatarUrl?: string;
  };
  teams: Team[];
  activeTeamId: string | null;
  onTeamSelect: (teamId: string | null) => void;
  onCreateTeam: () => void;
  onTeamSettings: (teamId: string) => void;
  onProfile: () => void;
  onAccount: () => void;
  onLogout: () => void;
}

export const UserDropdown: React.FC<UserDropdownProps> = ({
  isOpen,
  onClose,
  user,
  teams,
  activeTeamId,
  onTeamSelect,
  onCreateTeam,
  onTeamSettings,
  onProfile,
  onAccount,
  onLogout,
}) => {
  const { t } = useTranslation();
  const dropdownRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    const handleClickOutside = (e: MouseEvent) => {
      if (dropdownRef.current && !dropdownRef.current.contains(e.target as Node)) {
        onClose();
      }
    };
    const handleEsc = (e: KeyboardEvent) => {
      if (e.key === 'Escape') onClose();
    };

    if (isOpen) {
      document.addEventListener('mousedown', handleClickOutside);
      document.addEventListener('keydown', handleEsc);
    }
    return () => {
      document.removeEventListener('mousedown', handleClickOutside);
      document.removeEventListener('keydown', handleEsc);
    };
  }, [isOpen, onClose]);

  if (!isOpen) return null;

  // Sort teams: active team first, then rest alphabetically
  const sortedTeams = [...teams].sort((a, b) => {
    if (a.id === activeTeamId) return -1;
    if (b.id === activeTeamId) return 1;
    return a.name.localeCompare(b.name);
  });

  return (
    <>
      {/* Backdrop */}
      <div className="fixed inset-0 bg-black/20 z-40" />

      {/* Dropdown Panel */}
      <div
        ref={dropdownRef}
        className="fixed top-0 right-0 h-full w-72 bg-ink-900 border-l border-ink-800 shadow-2xl z-50 animate-in slide-in-from-right duration-300"
      >
        {/* User Info */}
        <div className="p-5 border-b border-ink-800">
          <div className="flex items-center gap-3">
            <div className="w-12 h-12 rounded-full bg-gradient-to-br from-indigo-500 to-purple-600 flex items-center justify-center overflow-hidden">
              {user.avatarUrl ? (
                <img src={user.avatarUrl} alt="Avatar" className="w-full h-full object-cover" />
              ) : (
                <User size={24} className="text-white" />
              )}
            </div>
            <div className="flex-1 min-w-0">
              <p className="font-semibold text-white truncate">{user.name}</p>
              <p className="text-sm text-ink-500 truncate">{user.email}</p>
            </div>
          </div>
        </div>

        {/* Teams Section */}
        <div className="p-3 border-b border-ink-800">
          <p className="px-2 text-xs font-semibold text-ink-500 uppercase tracking-wider mb-2">
            {t('user.joinedTeams')}
          </p>
          <div className="space-y-1">
            {sortedTeams.map(team => (
              <div
                key={team.id}
                className="flex items-center justify-between px-2 py-2 rounded-lg hover:bg-ink-800/50 transition-colors group"
              >
                <button
                  onClick={() => onTeamSelect(team.id)}
                  className="flex items-center gap-2 flex-1 min-w-0"
                >
                  <div className="w-7 h-7 rounded-lg bg-indigo-600/20 flex items-center justify-center text-indigo-400 text-sm font-bold">
                    {team.name.charAt(0).toUpperCase()}
                  </div>
                  <span className="text-sm text-ink-300 truncate">{team.name}</span>
                </button>
                <div className="flex items-center gap-1">
                  {team.isOwner && (
                    <button
                      onClick={() => onTeamSettings(team.id)}
                      className="p-1 rounded text-ink-500 hover:text-white hover:bg-ink-700 opacity-0 group-hover:opacity-100 transition-all"
                    >
                      <Settings size={14} />
                    </button>
                  )}
                  {activeTeamId === team.id && (
                    <Check size={16} className="text-indigo-400" />
                  )}
                </div>
              </div>
            ))}
            <button
              onClick={onCreateTeam}
              className="flex items-center gap-2 w-full px-2 py-2 rounded-lg text-indigo-400 hover:bg-indigo-500/10 transition-colors"
            >
              <Plus size={18} />
              <span className="text-sm font-medium">{t('user.createTeam')}</span>
            </button>
          </div>
        </div>

        {/* Menu Items */}
        <div className="p-3 space-y-1">
          <button
            onClick={onProfile}
            className="flex items-center gap-3 w-full px-3 py-2.5 rounded-lg text-ink-300 hover:bg-ink-800/50 transition-colors"
          >
            <User size={18} className="text-ink-500" />
            <span className="text-sm">{t('user.profile')}</span>
          </button>
          <button
            onClick={onAccount}
            className="flex items-center gap-3 w-full px-3 py-2.5 rounded-lg text-ink-300 hover:bg-ink-800/50 transition-colors"
          >
            <Settings size={18} className="text-ink-500" />
            <span className="text-sm">{t('user.account')}</span>
          </button>

          {/* Language Switcher (Mobile) */}
          <div className="md:hidden px-3 py-2.5">
            <LanguageSwitcher variant="inline" />
          </div>
        </div>

        {/* Logout */}
        <div className="absolute bottom-0 left-0 right-0 p-3 border-t border-ink-800">
          <button
            onClick={onLogout}
            className="flex items-center gap-3 w-full px-3 py-2.5 rounded-lg text-red-400 hover:bg-red-500/10 transition-colors"
          >
            <LogOut size={18} />
            <span className="text-sm font-medium">{t('user.signOut')}</span>
          </button>
        </div>
      </div>
    </>
  );
};
