import React from 'react';
import { X, Check, Settings, LogOut, User } from 'lucide-react';
import { ViewState } from '../types';

// ---------------------------------------------------------------------------
// MobileProfilePage — full-screen profile + workspace switcher for mobile
// ---------------------------------------------------------------------------

export interface MobileProfilePageProps {
  isOpen: boolean;
  onClose: () => void;
  userProfile: { name: string; email: string; avatarUrl: string };
  teams: Array<{ id: string | number; name: string }>;
  personalTeamId: string | null;
  selectedTeamId: string | null;
  currentView: ViewState;
  onSwitchTeam: (teamId: string) => void;
  onSettings: () => void;
  onLogout: () => void;
}

export function MobileProfilePage({
  isOpen,
  onClose,
  userProfile,
  teams,
  personalTeamId,
  selectedTeamId,
  onSwitchTeam,
  onSettings,
  onLogout,
}: MobileProfilePageProps) {
  if (!isOpen) return null;

  const otherTeams = teams.filter((t) => String(t.id) !== personalTeamId);

  return (
    <div className="sm:hidden fixed inset-0 z-50 bg-zinc-950 flex flex-col overflow-y-auto">
      {/* Header */}
      <div className="flex items-center justify-between px-5 pt-12 pb-4 border-b border-zinc-800/60">
        <h1 className="text-base font-semibold text-zinc-100">Profile</h1>
        <button
          onClick={onClose}
          className="w-8 h-8 flex items-center justify-center rounded-full text-zinc-400 hover:text-zinc-100 hover:bg-zinc-800/60 transition-colors"
          aria-label="Close"
        >
          <X size={20} />
        </button>
      </div>

      {/* Profile Section */}
      <div className="px-5 py-6 border-b border-zinc-800/60">
        <div className="flex items-center gap-4">
          {userProfile.avatarUrl ? (
            <div className="w-16 h-16 rounded-full overflow-hidden border-2 border-zinc-700 flex-shrink-0">
              <img
                src={userProfile.avatarUrl}
                alt="Avatar"
                className="w-full h-full object-cover"
              />
            </div>
          ) : (
            <div className="w-16 h-16 rounded-full bg-zinc-800 border-2 border-zinc-700 flex items-center justify-center flex-shrink-0">
              <User size={28} className="text-zinc-400" />
            </div>
          )}
          <div className="min-w-0">
            <p className="text-base font-semibold text-zinc-100 truncate">
              {userProfile.name || 'User'}
            </p>
            <p className="text-sm text-zinc-400 truncate">{userProfile.email}</p>
          </div>
        </div>
      </div>

      {/* Workspace Switcher */}
      <div className="px-5 py-4 border-b border-zinc-800/60">
        <p className="text-[11px] font-semibold text-zinc-500 uppercase tracking-wider mb-3">
          Workspace
        </p>
        <div className="space-y-1">
          {/* Personal workspace */}
          {personalTeamId && (
            <button
              onClick={() => onSwitchTeam(personalTeamId)}
              className={`w-full flex items-center gap-3 px-3 py-3 rounded-xl transition-colors ${
                selectedTeamId === personalTeamId
                  ? 'bg-indigo-500/10'
                  : 'hover:bg-zinc-800/60 active:bg-zinc-800/80'
              }`}
            >
              <div
                className={`w-9 h-9 rounded-xl flex items-center justify-center text-sm font-bold flex-shrink-0 ${
                  selectedTeamId === personalTeamId
                    ? 'bg-indigo-500/30 text-indigo-300'
                    : 'bg-zinc-800 text-zinc-400'
                }`}
              >
                {userProfile.name?.charAt(0)?.toUpperCase() || 'P'}
              </div>
              <span
                className={`text-sm flex-1 text-left ${
                  selectedTeamId === personalTeamId
                    ? 'text-indigo-300 font-medium'
                    : 'text-zinc-300'
                }`}
              >
                Personal
              </span>
              {selectedTeamId === personalTeamId && (
                <Check size={16} className="text-indigo-400 flex-shrink-0" />
              )}
            </button>
          )}

          {/* Team workspaces */}
          {otherTeams.map((team) => {
            const isActive = selectedTeamId === String(team.id);
            return (
              <button
                key={team.id}
                onClick={() => onSwitchTeam(String(team.id))}
                className={`w-full flex items-center gap-3 px-3 py-3 rounded-xl transition-colors ${
                  isActive
                    ? 'bg-indigo-500/10'
                    : 'hover:bg-zinc-800/60 active:bg-zinc-800/80'
                }`}
              >
                <div
                  className={`w-9 h-9 rounded-xl flex items-center justify-center text-sm font-bold flex-shrink-0 ${
                    isActive
                      ? 'bg-indigo-500/30 text-indigo-300'
                      : 'bg-zinc-800 text-zinc-400'
                  }`}
                >
                  {team.name.charAt(0).toUpperCase()}
                </div>
                <span
                  className={`text-sm flex-1 text-left ${
                    isActive ? 'text-indigo-300 font-medium' : 'text-zinc-300'
                  }`}
                >
                  {team.name}
                </span>
                {isActive && (
                  <Check size={16} className="text-indigo-400 flex-shrink-0" />
                )}
              </button>
            );
          })}
        </div>
      </div>

      {/* Actions */}
      <div className="px-5 py-4 space-y-1">
        <button
          onClick={() => { onSettings(); onClose(); }}
          className="w-full flex items-center gap-3 px-3 py-3 rounded-xl text-zinc-300 hover:text-zinc-100 hover:bg-zinc-800/60 active:bg-zinc-800/80 transition-colors"
        >
          <Settings size={18} className="flex-shrink-0" />
          <span className="text-sm">Settings</span>
        </button>
        <button
          onClick={onLogout}
          className="w-full flex items-center gap-3 px-3 py-3 rounded-xl text-red-400 hover:text-red-300 hover:bg-red-500/10 active:bg-red-500/15 transition-colors"
        >
          <LogOut size={18} className="flex-shrink-0" />
          <span className="text-sm">Log Out</span>
        </button>
      </div>

      {/* Bottom safe-area spacer */}
      <div className="pb-[env(safe-area-inset-bottom,16px)]" />
    </div>
  );
}
