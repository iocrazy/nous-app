import React from 'react';
import { Check, ChevronRight, LogOut, User } from 'lucide-react';
import { ViewState } from '../types';

// ---------------------------------------------------------------------------
// MobileProfilePage — Figma-style Settings page for mobile
// Sections: Profile / Workspaces / General / Account
// ---------------------------------------------------------------------------

// Color palette for team avatars (deterministic by index)
const TEAM_COLORS = [
  'bg-pink-600', 'bg-amber-600', 'bg-indigo-600', 'bg-emerald-600',
  'bg-cyan-600', 'bg-rose-600', 'bg-violet-600', 'bg-orange-600',
];

function getTeamColor(index: number): string {
  return TEAM_COLORS[index % TEAM_COLORS.length];
}

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
    <div className="sm:hidden fixed inset-0 z-50 bg-zinc-950 flex flex-col">
      {/* Header — Figma style: centered title + Done button */}
      <div className="flex items-center justify-between px-5 pt-[env(safe-area-inset-top,12px)] pb-3">
        <div className="w-14" /> {/* spacer for centering */}
        <h1 className="text-base font-semibold text-zinc-100">Settings</h1>
        <button
          onClick={onClose}
          className="px-3 py-1.5 text-sm font-medium text-zinc-300 hover:text-white rounded-lg hover:bg-zinc-800/60 transition-colors"
        >
          Done
        </button>
      </div>

      <div className="flex-1 overflow-y-auto">
        {/* Profile row */}
        <button
          onClick={() => { onSettings(); onClose(); }}
          className="w-full flex items-center gap-3.5 px-5 py-4 hover:bg-zinc-900/60 active:bg-zinc-800/60 transition-colors"
        >
          {userProfile.avatarUrl ? (
            <div className="w-11 h-11 rounded-full overflow-hidden flex-shrink-0">
              <img src={userProfile.avatarUrl} alt="" className="w-full h-full object-cover" />
            </div>
          ) : (
            <div className="w-11 h-11 rounded-full bg-emerald-700 flex items-center justify-center flex-shrink-0">
              <span className="text-base font-bold text-white">
                {userProfile.name?.charAt(0)?.toLowerCase() || 'u'}
              </span>
            </div>
          )}
          <div className="flex-1 min-w-0 text-left">
            <p className="text-[15px] font-medium text-zinc-100 truncate">{userProfile.name || 'User'}</p>
            <p className="text-[13px] text-zinc-500 truncate">{userProfile.email}</p>
          </div>
          <ChevronRight size={18} className="text-zinc-600 flex-shrink-0" />
        </button>

        {/* Workspaces section */}
        <div className="mt-4 px-5">
          <h2 className="text-[15px] font-bold text-zinc-100 mb-3">Workspaces</h2>
        </div>
        <div className="space-y-0.5">
          {/* Personal workspace */}
          {personalTeamId && (
            <button
              onClick={() => onSwitchTeam(personalTeamId)}
              className={`w-full flex items-center gap-3.5 px-5 py-3 transition-colors ${
                selectedTeamId === personalTeamId ? 'bg-zinc-800/40' : 'hover:bg-zinc-900/60 active:bg-zinc-800/60'
              }`}
            >
              <div className="w-10 h-10 rounded-full bg-indigo-600 flex items-center justify-center flex-shrink-0">
                <span className="text-sm font-bold text-white">
                  {userProfile.name?.charAt(0)?.toUpperCase() || 'P'}
                </span>
              </div>
              <span className={`text-[15px] flex-1 text-left ${
                selectedTeamId === personalTeamId ? 'text-zinc-100 font-medium' : 'text-zinc-300'
              }`}>
                Personal
              </span>
              {selectedTeamId === personalTeamId && (
                <Check size={20} className="text-zinc-400 flex-shrink-0" />
              )}
            </button>
          )}

          {/* Team workspaces */}
          {otherTeams.map((team, idx) => {
            const isActive = selectedTeamId === String(team.id);
            return (
              <button
                key={team.id}
                onClick={() => onSwitchTeam(String(team.id))}
                className={`w-full flex items-center gap-3.5 px-5 py-3 transition-colors ${
                  isActive ? 'bg-zinc-800/40' : 'hover:bg-zinc-900/60 active:bg-zinc-800/60'
                }`}
              >
                <div className={`w-10 h-10 rounded-full ${getTeamColor(idx)} flex items-center justify-center flex-shrink-0`}>
                  <span className="text-sm font-bold text-white">
                    {team.name.charAt(0).toUpperCase()}
                  </span>
                </div>
                <span className={`text-[15px] flex-1 text-left ${
                  isActive ? 'text-zinc-100 font-medium' : 'text-zinc-300'
                }`}>
                  {team.name}
                </span>
                {isActive && (
                  <Check size={20} className="text-zinc-400 flex-shrink-0" />
                )}
              </button>
            );
          })}
        </div>

        {/* General section */}
        <div className="mt-6 px-5">
          <h2 className="text-[15px] font-bold text-zinc-100 mb-1">General</h2>
        </div>
        <div>
          <button
            onClick={() => { onSettings(); onClose(); }}
            className="w-full flex items-center justify-between px-5 py-3.5 hover:bg-zinc-900/60 active:bg-zinc-800/60 transition-colors"
          >
            <span className="text-[15px] text-zinc-300">Settings</span>
            <ChevronRight size={18} className="text-zinc-600" />
          </button>
        </div>

        {/* Account section */}
        <div className="mt-6 px-5">
          <h2 className="text-[15px] font-bold text-zinc-100 mb-1">Account</h2>
        </div>
        <div>
          <button
            onClick={onLogout}
            className="w-full text-left px-5 py-3.5 text-[15px] text-red-400 hover:bg-red-500/5 active:bg-red-500/10 transition-colors"
          >
            Log Out
          </button>
        </div>

        {/* Bottom safe-area spacer */}
        <div className="pb-[calc(env(safe-area-inset-bottom,16px)+20px)]" />
      </div>
    </div>
  );
}
