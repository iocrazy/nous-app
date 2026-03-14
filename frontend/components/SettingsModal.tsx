import React, { useState, useEffect, useMemo } from 'react';
import {
  X, User, FolderOpen, Key, ScrollText, ListTodo, Tag, Sparkles, FileText, Users,
} from 'lucide-react';
import { PersonalSettings } from './PersonalSettings';
import { SettingsView } from './SettingsView';
import { TeamSettings } from './TeamSettings';
import { UserSettings, AISettings as AISettingsType, Team } from '../types';

type SettingsTab = 'personal' | 'team' | 'general' | 'api' | 'logs' | 'tasks' | 'tags' | 'ai' | 'docs';

interface SettingsModalProps {
  isOpen: boolean;
  onClose: () => void;
  initialTab?: SettingsTab;
  user: {
    id: string;
    name: string;
    email: string;
    avatarUrl?: string;
    bio?: string;
  };
  onUserUpdated?: () => void;
  // App settings props (forwarded to SettingsView)
  settings: UserSettings;
  onUpdateSettings: (s: UserSettings) => void;
  aiSettings?: AISettingsType;
  onSaveAISettings?: (settings: AISettingsType) => void;
  // Team settings props (optional — shown only when in team mode)
  currentTeam?: Team | null;
  isTeamOwner?: boolean;
  onTeamDeleted?: (teamId: string) => void;
  onTeamLeft?: (teamId: string) => void;
  onTeamUpdated?: (team: Team) => void;
}

// Nav item type
interface NavItem {
  id: SettingsTab;
  label: string;
  icon: React.ElementType;
}

interface NavSection {
  label: string;
  items: NavItem[];
}

// Static nav sections (team section added dynamically)
const ACCOUNT_SECTION: NavSection = {
  label: 'ACCOUNT',
  items: [
    { id: 'personal', label: 'Personal Settings', icon: User },
  ],
};

const TEAM_SECTION: NavSection = {
  label: 'TEAM',
  items: [
    { id: 'team', label: 'Team Settings', icon: Users },
  ],
};

const APP_SETTINGS_SECTION: NavSection = {
  label: 'APP SETTINGS',
  items: [
    { id: 'general', label: 'General', icon: FolderOpen },
    { id: 'api', label: 'API Management', icon: Key },
    { id: 'logs', label: 'Logs', icon: ScrollText },
    { id: 'tasks', label: 'Tasks', icon: ListTodo },
    { id: 'tags', label: 'Tags', icon: Tag },
    { id: 'ai', label: 'AI', icon: Sparkles },
    { id: 'docs', label: 'API Docs', icon: FileText },
  ],
};

const TAB_LABELS: Record<SettingsTab, string> = {
  personal: 'Personal Settings',
  team: 'Team Settings',
  general: 'General',
  api: 'API Management',
  logs: 'Logs',
  tasks: 'Tasks',
  tags: 'Tags',
  ai: 'AI',
  docs: 'API Docs',
};

export const SettingsModal: React.FC<SettingsModalProps> = ({
  isOpen,
  onClose,
  initialTab = 'personal',
  user,
  onUserUpdated,
  settings,
  onUpdateSettings,
  aiSettings,
  onSaveAISettings,
  currentTeam,
  isTeamOwner = false,
  onTeamDeleted,
  onTeamLeft,
  onTeamUpdated,
}) => {
  const [activeTab, setActiveTab] = useState<SettingsTab>(initialTab);

  // Build nav sections dynamically — show TEAM section only when in team mode
  const navSections = useMemo<NavSection[]>(() => {
    const sections = [ACCOUNT_SECTION];
    if (currentTeam) {
      sections.push(TEAM_SECTION);
    }
    sections.push(APP_SETTINGS_SECTION);
    return sections;
  }, [currentTeam]);

  useEffect(() => {
    if (isOpen) {
      // If initialTab is 'team' but no team context, fall back to 'personal'
      setActiveTab(!currentTeam && initialTab === 'team' ? 'personal' : initialTab);
    }
  }, [isOpen, initialTab, currentTeam]);

  useEffect(() => {
    const handleEsc = (e: KeyboardEvent) => {
      if (e.key === 'Escape') onClose();
    };
    if (isOpen) {
      document.addEventListener('keydown', handleEsc);
      document.body.style.overflow = 'hidden';
    }
    return () => {
      document.removeEventListener('keydown', handleEsc);
      document.body.style.overflow = '';
    };
  }, [isOpen, onClose]);

  if (!isOpen) return null;

  // Is it an app-settings tab (rendered by SettingsView)?
  const isAppSettingsTab = activeTab !== 'personal' && activeTab !== 'team';

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center">
      {/* Backdrop */}
      <div className="absolute inset-0 bg-black/60 backdrop-blur-sm" onClick={onClose} />

      {/* Modal — wider, fixed height */}
      <div className="relative bg-zinc-900 border border-zinc-800 md:rounded-2xl shadow-2xl w-full md:max-w-6xl md:mx-4 h-full md:h-[82vh] flex flex-col md:flex-row overflow-hidden animate-in fade-in zoom-in-95 duration-200">

        {/* Mobile Header */}
        <div className="md:hidden flex items-center justify-between px-4 py-3 border-b border-zinc-800 bg-zinc-950/50">
          <div className="flex items-center gap-3">
            <div className="w-8 h-8 rounded-full bg-gradient-to-br from-indigo-500 to-purple-600 flex items-center justify-center overflow-hidden">
              {user.avatarUrl ? (
                <img src={user.avatarUrl} alt="Avatar" className="w-full h-full object-cover" />
              ) : (
                <span className="text-xs font-bold text-white">
                  {user.name.charAt(0).toUpperCase()}
                </span>
              )}
            </div>
            <span className="text-sm font-medium text-white">{user.name}</span>
          </div>
          <button
            onClick={onClose}
            className="p-2 text-zinc-400 hover:text-white hover:bg-zinc-800 rounded-lg transition-colors"
          >
            <X size={20} />
          </button>
        </div>

        {/* Mobile Tab Bar (scrollable) */}
        <div className="md:hidden flex overflow-x-auto border-b border-zinc-800 bg-zinc-950/30">
          {navSections.flatMap(s => s.items).map((item) => (
            <button
              key={item.id}
              onClick={() => setActiveTab(item.id)}
              className={`flex-shrink-0 flex items-center gap-2 px-4 py-3 text-sm font-medium transition-colors relative ${
                activeTab === item.id
                  ? 'text-white'
                  : 'text-zinc-500 hover:text-zinc-300'
              }`}
            >
              <item.icon size={14} />
              <span className="whitespace-nowrap">{item.label}</span>
              {activeTab === item.id && (
                <div className="absolute bottom-0 left-4 right-4 h-0.5 bg-indigo-500 rounded-full" />
              )}
            </button>
          ))}
        </div>

        {/* Desktop Sidebar */}
        <div className="hidden md:flex w-56 bg-zinc-950/50 border-r border-zinc-800 flex-col flex-shrink-0">
          {/* User header */}
          <div className="p-4 border-b border-zinc-800">
            <div className="flex items-center gap-3">
              <div className="w-10 h-10 rounded-full bg-gradient-to-br from-indigo-500 to-purple-600 flex items-center justify-center overflow-hidden flex-shrink-0">
                {user.avatarUrl ? (
                  <img src={user.avatarUrl} alt="Avatar" className="w-full h-full object-cover" />
                ) : (
                  <span className="text-sm font-bold text-white">
                    {user.name.charAt(0).toUpperCase()}
                  </span>
                )}
              </div>
              <div className="flex-1 min-w-0">
                <p className="text-sm font-medium text-white truncate">{user.name}</p>
                <p className="text-xs text-zinc-500 truncate">{user.email}</p>
              </div>
            </div>
          </div>

          {/* Navigation sections */}
          <nav className="flex-1 p-3 space-y-4 overflow-y-auto">
            {navSections.map((section) => (
              <div key={section.label}>
                <p className="px-3 text-[10px] font-semibold text-zinc-600 uppercase tracking-wider mb-2">
                  {section.label}
                </p>
                <div className="space-y-0.5">
                  {section.items.map((item) => (
                    <button
                      key={item.id}
                      onClick={() => setActiveTab(item.id)}
                      className={`w-full flex items-center gap-2.5 px-3 py-2 rounded-lg text-sm transition-colors cursor-pointer select-none ${
                        activeTab === item.id
                          ? 'bg-zinc-800 text-white'
                          : 'text-zinc-400 hover:text-white hover:bg-zinc-800/50'
                      }`}
                    >
                      <item.icon size={16} />
                      {item.label}
                    </button>
                  ))}
                </div>
              </div>
            ))}
          </nav>
        </div>

        {/* Content */}
        <div className="flex-1 flex flex-col min-w-0 relative">
          {/* Desktop Header */}
          <div className="hidden md:flex items-center justify-between px-6 py-4 border-b border-zinc-800">
            <h2 className="text-lg font-semibold text-white">
              {TAB_LABELS[activeTab]}
            </h2>
            <button
              onClick={onClose}
              className="p-2 text-zinc-400 hover:text-white hover:bg-zinc-800 rounded-lg transition-colors"
            >
              <X size={20} />
            </button>
          </div>

          {/* Content Area */}
          <div className="flex-1 overflow-y-auto p-4 md:p-6">
            {activeTab === 'personal' && (
              <div className="max-w-xl mx-auto md:mx-0">
                <PersonalSettings user={user} onUserUpdated={onUserUpdated} />
              </div>
            )}

            {activeTab === 'team' && currentTeam && (
              <TeamSettings
                teamId={currentTeam.id}
                teamName={currentTeam.name}
                isOwner={isTeamOwner}
                currentUserId={user.id}
                currentUserName={user.name}
                currentUserEmail={user.email}
                onOpenInviteModal={() => {}}
                onTeamDeleted={() => {
                  onTeamDeleted?.(currentTeam.id);
                  onClose();
                }}
                onTeamLeft={() => {
                  onTeamLeft?.(currentTeam.id);
                  onClose();
                }}
                onTeamUpdated={onTeamUpdated}
              />
            )}

            {isAppSettingsTab && (
              <SettingsView
                settings={settings}
                onUpdateSettings={onUpdateSettings}
                activeTab={activeTab as any}
                aiSettings={aiSettings}
                onSaveAISettings={onSaveAISettings}
                embedded
              />
            )}
          </div>
        </div>
      </div>
    </div>
  );
};
