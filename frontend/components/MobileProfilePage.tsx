import React, { useState } from 'react';
import { Check, ChevronRight, RefreshCw, User } from 'lucide-react';
import { ViewState } from '../types';
import { VersionBadge } from './VersionBadge';
import { resetServiceWorkerAndReload } from '../utils/swReset';

// ---------------------------------------------------------------------------
// MobileProfilePage — Figma-style Settings page for mobile
// ---------------------------------------------------------------------------

const TEAM_COLORS = [
  'bg-pink-600', 'bg-amber-500', 'bg-indigo-600', 'bg-emerald-600',
  'bg-cyan-600', 'bg-rose-500', 'bg-violet-600', 'bg-orange-500',
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
  const [clearing, setClearing] = useState(false);

  if (!isOpen) return null;

  const otherTeams = teams.filter((t) => String(t.id) !== personalTeamId);

  const onClearCache = async () => {
    if (clearing) return;
    setClearing(true);
    // Hard escape for a stuck service worker: unregister SW + clear caches +
    // reload. Normal updates apply silently in the background; this is the
    // manual fallback. The reload navigates away, so no need to reset state.
    await resetServiceWorkerAndReload();
  };

  return (
    <div className="sm:hidden fixed inset-0 z-50 bg-zinc-950 flex flex-col">
      {/* Header */}
      <div className="flex items-center justify-between px-4 pt-[max(env(safe-area-inset-top),12px)] pb-3">
        <div className="w-16" />
        <h1 className="text-[17px] font-semibold text-zinc-100">Settings</h1>
        <button
          onClick={onClose}
          className="px-4 py-1.5 text-[15px] font-medium text-zinc-100 bg-zinc-800 rounded-full hover:bg-zinc-700 active:bg-zinc-600 transition-colors"
        >
          Done
        </button>
      </div>

      <div className="flex-1 overflow-y-auto">
        {/* ── Profile ── */}
        <button
          onClick={() => { onSettings(); onClose(); }}
          className="w-full flex items-center gap-3.5 px-5 py-4 active:bg-zinc-800/40 transition-colors"
        >
          {userProfile.avatarUrl ? (
            <img src={userProfile.avatarUrl} alt="" className="w-12 h-12 rounded-full object-cover flex-shrink-0" />
          ) : (
            <div className="w-12 h-12 rounded-full bg-emerald-700 flex items-center justify-center flex-shrink-0">
              <span className="text-lg font-bold text-white">
                {userProfile.name?.charAt(0)?.toLowerCase() || 'u'}
              </span>
            </div>
          )}
          <div className="flex-1 min-w-0 text-left">
            <p className="text-[16px] font-medium text-zinc-100 truncate">{userProfile.name || 'User'}</p>
            <p className="text-[13px] text-zinc-500 truncate">{userProfile.email}</p>
          </div>
          <ChevronRight size={20} className="text-zinc-600 flex-shrink-0" />
        </button>

        <div className="h-px bg-zinc-800/80 mx-5" />

        {/* ── Workspaces ── */}
        <div className="pt-5 pb-2 px-5">
          <h2 className="text-[17px] font-bold text-zinc-100">Workspaces</h2>
        </div>

        {personalTeamId && (
          <button
            onClick={() => onSwitchTeam(personalTeamId)}
            className="w-full flex items-center gap-4 px-5 py-3.5 active:bg-zinc-800/40 transition-colors"
          >
            <div className="w-10 h-10 rounded-full bg-indigo-600 flex items-center justify-center flex-shrink-0 shadow-lg shadow-indigo-500/20">
              <span className="text-[15px] font-bold text-white">
                {userProfile.name?.charAt(0)?.toUpperCase() || 'P'}
              </span>
            </div>
            <span className="text-[15px] flex-1 text-left text-zinc-200">Personal</span>
            {selectedTeamId === personalTeamId && (
              <Check size={20} strokeWidth={2.5} className="text-zinc-400 flex-shrink-0" />
            )}
          </button>
        )}

        {otherTeams.map((team, idx) => {
          const isActive = selectedTeamId === String(team.id);
          return (
            <button
              key={team.id}
              onClick={() => onSwitchTeam(String(team.id))}
              className="w-full flex items-center gap-4 px-5 py-3.5 active:bg-zinc-800/40 transition-colors"
            >
              <div className={`w-10 h-10 rounded-full ${getTeamColor(idx)} flex items-center justify-center flex-shrink-0 shadow-lg shadow-black/20`}>
                <span className="text-[15px] font-bold text-white">
                  {team.name.charAt(0).toUpperCase()}
                </span>
              </div>
              <span className="text-[15px] flex-1 text-left text-zinc-200">{team.name}</span>
              {isActive && (
                <Check size={20} strokeWidth={2.5} className="text-zinc-400 flex-shrink-0" />
              )}
            </button>
          );
        })}

        <div className="h-px bg-zinc-800/80 mx-5 mt-2" />

        {/* ── General ── */}
        <div className="pt-5 pb-2 px-5">
          <h2 className="text-[17px] font-bold text-zinc-100">General</h2>
        </div>

        <button
          onClick={() => { onSettings(); onClose(); }}
          className="w-full flex items-center justify-between px-5 py-3.5 active:bg-zinc-800/40 transition-colors"
        >
          <span className="text-[15px] text-zinc-300">Notifications</span>
          <ChevronRight size={18} className="text-zinc-600" />
        </button>
        <button
          onClick={() => { onSettings(); onClose(); }}
          className="w-full flex items-center justify-between px-5 py-3.5 active:bg-zinc-800/40 transition-colors"
        >
          <span className="text-[15px] text-zinc-300">Preferences</span>
          <ChevronRight size={18} className="text-zinc-600" />
        </button>
        <button
          onClick={onClearCache}
          disabled={clearing}
          className="w-full flex items-center justify-between px-5 py-3.5 active:bg-zinc-800/40 disabled:opacity-60 transition-colors"
        >
          <span className="text-[15px] text-zinc-300">
            {clearing ? 'Clearing…' : 'Clear Cache & Reload'}
          </span>
          <RefreshCw
            size={18}
            className={`text-zinc-600 ${clearing ? 'animate-spin' : ''}`}
          />
        </button>

        <div className="h-px bg-zinc-800/80 mx-5 mt-2" />

        {/* ── Account ── */}
        <div className="pt-5 pb-2 px-5">
          <h2 className="text-[17px] font-bold text-zinc-100">Account</h2>
        </div>

        <button
          onClick={onLogout}
          className="w-full text-left px-5 py-3.5 active:bg-zinc-800/40 transition-colors"
        >
          <span className="text-[15px] text-zinc-300">Log Out</span>
        </button>

        {/* Version — so the user can tell if the app is up to date */}
        <div className="px-5 pt-6 text-center text-[12px] text-zinc-600">
          <VersionBadge />
        </div>

        {/* Bottom spacer */}
        <div className="pb-[calc(env(safe-area-inset-bottom,16px)+24px)]" />
      </div>
    </div>
  );
}
