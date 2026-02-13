import React from 'react';
import { useTranslation } from 'react-i18next';
import {
  Search,
  Library,
  LayoutDashboard,
  Settings,
  FolderKanban,
  Layers,
  Users,
  CreditCard,
  Sparkles,
  ChevronDown,
  FolderOpen,
  Key,
  ScrollText,
  ListTodo,
  Tag,
  BookOpen,
  Trash2,
  ArrowLeft,
  Upload,
  MessageSquare,
  CheckCircle2,
  Activity,
} from 'lucide-react';
import { Team, Project, SidebarMode, ViewState } from '../types';
import { SmartCollection } from '../services/smartCollectionService';
import { SmartCollectionsSidebar } from './SmartCollectionsSidebar';
import { WorkspaceSwitcher } from './WorkspaceSwitcher';
import { hasPermission } from '../utils/permissions';

declare const __APP_VERSION__: string;

// ---------------------------------------------------------------------------
// SidebarItem
// ---------------------------------------------------------------------------

interface SidebarItemProps {
  icon: React.ElementType;
  label: string;
  active: boolean;
  onClick: () => void;
  hasSubmenu?: boolean;
  isOpen?: boolean;
}

const SidebarItem: React.FC<SidebarItemProps> = ({
  icon: Icon,
  label,
  active,
  onClick,
  hasSubmenu = false,
  isOpen = false,
}) => (
  <button
    onClick={onClick}
    className={`w-full flex items-center justify-between px-3 py-2.5 rounded-xl text-sm font-medium transition-all duration-200 group ${
      active
        ? 'bg-indigo-500/10 text-indigo-400'
        : 'text-zinc-400 hover:text-zinc-200 hover:bg-zinc-800/50'
    }`}
  >
    <div className="flex items-center gap-3">
      <Icon size={20} className={active ? 'text-indigo-400' : 'text-zinc-500 group-hover:text-zinc-300'} />
      <span>{label}</span>
    </div>
    {hasSubmenu && (
      <ChevronDown
        size={16}
        className={`transition-transform duration-200 ${isOpen ? 'rotate-180' : ''}`}
      />
    )}
  </button>
);

// ---------------------------------------------------------------------------
// Settings submenu item helper
// ---------------------------------------------------------------------------

interface SettingsSubItemProps {
  icon: React.ElementType;
  label: string;
  tabKey: string;
  activeView: ViewState;
  activeTab: string;
  onSelect: (tab: string) => void;
}

const SettingsSubItem: React.FC<SettingsSubItemProps> = ({
  icon: Icon,
  label,
  tabKey,
  activeView,
  activeTab,
  onSelect,
}) => (
  <button
    onClick={() => onSelect(tabKey)}
    className={`w-full text-left px-4 py-2 text-sm rounded-r-lg transition-colors ${
      activeView === 'settings' && activeTab === tabKey
        ? 'text-indigo-400 bg-indigo-500/5'
        : 'text-zinc-500 hover:text-zinc-300'
    }`}
  >
    <div className="flex items-center gap-2">
      <Icon size={14} />
      <span>{label}</span>
    </div>
  </button>
);

// ---------------------------------------------------------------------------
// Sidebar Props
// ---------------------------------------------------------------------------

interface SidebarProps {
  mode: SidebarMode;
  view: ViewState;
  settingsTab: string;

  // Team context
  teams: Team[];
  activeTeamId: string | null;
  currentTeam: Team | null;
  permissions: string[];
  userName?: string;

  // Project context (for project mode)
  activeProject: Project | null;

  // Library submenu state
  isLibraryOpen: boolean;
  isSettingsOpen: boolean;
  activeSmartCollectionId: number | null;

  // Callbacks
  onViewChange: (view: string) => void;
  onSettingsTabChange: (tab: string) => void;
  onToggleLibrary: () => void;
  onToggleSettings: () => void;
  onTeamChange: (teamId: string | null) => void;
  onCreateTeam: () => void;
  onProjectBack: () => void;
  onSmartCollectionSelect: (collection: SmartCollection | null) => void;
  onProjectSelect: (project: Project) => void;
}

// ---------------------------------------------------------------------------
// Settings submenu (shared across personal & team modes)
// ---------------------------------------------------------------------------

const SETTINGS_TABS = [
  { key: 'general', icon: FolderOpen, label: 'General' },
  { key: 'api', icon: Key, label: 'API Management' },
  { key: 'logs', icon: ScrollText, label: 'Logs' },
  { key: 'tasks', icon: ListTodo, label: 'Tasks' },
  { key: 'tags', icon: Tag, label: 'Tags' },
  { key: 'ai', icon: Sparkles, label: 'AI' },
  { key: 'docs', icon: BookOpen, label: 'API Docs' },
];

// ---------------------------------------------------------------------------
// Logo
// ---------------------------------------------------------------------------

const Logo: React.FC = () => (
  <div className="flex items-center gap-3 mb-10 px-2">
    <div className="w-8 h-8 bg-gradient-to-br from-indigo-500 to-purple-600 rounded-lg flex items-center justify-center shadow-lg shadow-indigo-500/20">
      <Sparkles className="w-5 h-5 text-white" />
    </div>
    <span className="text-xl font-bold tracking-tight">MediaHub</span>
  </div>
);

// ---------------------------------------------------------------------------
// Version footer
// ---------------------------------------------------------------------------

const VersionFooter: React.FC = () => (
  <div className="pt-4 pb-2 px-2 border-t border-zinc-800">
    <span className="text-xs text-zinc-600">v{__APP_VERSION__}</span>
  </div>
);

// ---------------------------------------------------------------------------
// Divider
// ---------------------------------------------------------------------------

const Divider: React.FC = () => <div className="my-3 border-t border-zinc-800" />;

// ---------------------------------------------------------------------------
// Main Sidebar export
// ---------------------------------------------------------------------------

export const Sidebar: React.FC<SidebarProps> = ({
  mode,
  view,
  settingsTab,
  teams,
  activeTeamId,
  currentTeam,
  permissions,
  userName,
  activeProject,
  isLibraryOpen,
  isSettingsOpen,
  activeSmartCollectionId,
  onViewChange,
  onSettingsTabChange,
  onToggleLibrary,
  onToggleSettings,
  onTeamChange,
  onCreateTeam,
  onProjectBack,
  onSmartCollectionSelect,
  onProjectSelect,
}) => {
  const { t } = useTranslation();

  // ------- Settings submenu (reused in personal & team modes) -------
  const renderSettingsMenu = () => (
    <div className="space-y-1">
      <SidebarItem
        icon={Settings}
        label={t('nav.settings')}
        active={view === 'settings'}
        onClick={() => {
          onToggleSettings();
          if (!isSettingsOpen) {
            onViewChange('settings');
            if (settingsTab === 'api') onSettingsTabChange('general');
          }
        }}
        hasSubmenu
        isOpen={isSettingsOpen}
      />

      {isSettingsOpen && (
        <div className="ml-9 border-l border-zinc-800 space-y-1 animate-in slide-in-from-left-2 duration-200">
          {SETTINGS_TABS.map((tab) => (
            <SettingsSubItem
              key={tab.key}
              icon={tab.icon}
              label={tab.label}
              tabKey={tab.key}
              activeView={view}
              activeTab={settingsTab}
              onSelect={(key) => {
                onViewChange('settings');
                onSettingsTabChange(key);
              }}
            />
          ))}
        </div>
      )}
    </div>
  );

  // ===================================================================
  // PROJECT MODE
  // ===================================================================
  if (mode === 'project') {
    return (
      <aside className="hidden md:flex flex-col w-64 border-r border-zinc-800 bg-zinc-950 p-6 fixed h-full z-10">
        {/* Back to projects + team name */}
        <div className="mb-6">
          <button
            onClick={onProjectBack}
            className="flex items-center gap-2 text-sm text-zinc-400 hover:text-zinc-200 transition-colors mb-2"
          >
            <ArrowLeft size={16} />
            <span>Projects</span>
          </button>
          {currentTeam && (
            <p className="px-1 text-xs text-zinc-500 truncate">{currentTeam.name}</p>
          )}
        </div>

        {/* Project name */}
        {activeProject && (
          <div className="px-2 mb-6">
            <p className="text-base font-bold text-zinc-100 truncate">{activeProject.name}</p>
          </div>
        )}

        <nav className="flex-1 space-y-1">
          {/* FILES section */}
          <p className="px-3 text-[10px] font-semibold text-zinc-500 uppercase tracking-wider mb-1">
            Files
          </p>
          <SidebarItem
            icon={FolderOpen}
            label="All Files"
            active={view === 'mediatrack'}
            onClick={() => onViewChange('mediatrack')}
          />
          {hasPermission(permissions, 'resource.upload') && (
            <SidebarItem
              icon={Upload}
              label="Upload"
              active={false}
              onClick={() => onViewChange('mediatrack')}
            />
          )}

          <div className="my-2" />

          {/* REVIEW section */}
          <p className="px-3 text-[10px] font-semibold text-zinc-500 uppercase tracking-wider mb-1">
            Review
          </p>
          <SidebarItem
            icon={MessageSquare}
            label="Comments"
            active={false}
            onClick={() => onViewChange('mediatrack')}
          />
          <SidebarItem
            icon={CheckCircle2}
            label="Status"
            active={false}
            onClick={() => onViewChange('mediatrack')}
          />

          <Divider />

          {/* Bottom section */}
          <SidebarItem
            icon={Users}
            label="Members"
            active={view === 'members'}
            onClick={() => onViewChange('members')}
          />
          <SidebarItem
            icon={Activity}
            label="Activity"
            active={false}
            onClick={() => onViewChange('mediatrack')}
          />
          {hasPermission(permissions, 'project.manage') && (
            <SidebarItem
              icon={Settings}
              label="Settings"
              active={view === 'settings'}
              onClick={() => onViewChange('settings')}
            />
          )}
        </nav>

        <VersionFooter />
      </aside>
    );
  }

  // ===================================================================
  // TEAM MODE
  // ===================================================================
  if (mode === 'team') {
    return (
      <aside className="hidden md:flex flex-col w-64 border-r border-zinc-800 bg-zinc-950 p-6 fixed h-full z-10">
        <Logo />

        <div className="mb-4 px-2">
          <WorkspaceSwitcher
            teams={teams}
            activeTeamId={activeTeamId}
            currentTeam={currentTeam}
            userName={userName}
            onTeamChange={onTeamChange}
            onCreateTeam={onCreateTeam}
          />
        </div>

        <nav className="flex-1 space-y-2">
          {/* Team-specific views */}
          {hasPermission(permissions, 'project.view') && (
            <SidebarItem
              icon={FolderKanban}
              label={t('mediatrack.projects', 'Projects')}
              active={view === 'mediatrack'}
              onClick={() => onViewChange('mediatrack')}
            />
          )}
          {hasPermission(permissions, 'resource.view') && (
            <SidebarItem
              icon={Layers}
              label="Resources"
              active={view === 'resources'}
              onClick={() => onViewChange('resources')}
            />
          )}
          {hasPermission(permissions, 'member.view') && (
            <SidebarItem
              icon={Users}
              label="Members"
              active={view === 'members'}
              onClick={() => onViewChange('members')}
            />
          )}
          {hasPermission(permissions, 'billing.view') && (
            <SidebarItem
              icon={CreditCard}
              label="Billing"
              active={view === 'billing'}
              onClick={() => onViewChange('billing')}
            />
          )}

          <Divider />

          {/* Personal views accessible in team context */}
          <SidebarItem
            icon={Search}
            label={t('nav.linkParser')}
            active={view === 'parser'}
            onClick={() => onViewChange('parser')}
          />
          <SidebarItem
            icon={Library}
            label="My Library"
            active={view === 'library'}
            onClick={() => onViewChange('library')}
          />

          <Divider />

          {/* Settings with submenu */}
          {renderSettingsMenu()}
        </nav>

        <VersionFooter />
      </aside>
    );
  }

  // ===================================================================
  // PERSONAL MODE (default)
  // ===================================================================
  return (
    <aside className="hidden md:flex flex-col w-64 border-r border-zinc-800 bg-zinc-950 p-6 fixed h-full z-10">
      <Logo />

      <div className="mb-4 px-2">
        <WorkspaceSwitcher
          teams={teams}
          activeTeamId={activeTeamId}
          currentTeam={null}
          userName={userName}
          onTeamChange={onTeamChange}
          onCreateTeam={onCreateTeam}
        />
      </div>

      <nav className="flex-1 space-y-2">
        <SidebarItem
          icon={Search}
          label={t('nav.linkParser')}
          active={view === 'parser'}
          onClick={() => onViewChange('parser')}
        />

        {/* Library with submenu */}
        <div className="space-y-1">
          <SidebarItem
            icon={Library}
            label="Library"
            active={view === 'library'}
            onClick={() => {
              onViewChange('library');
              if (!isLibraryOpen) {
                onToggleLibrary();
              }
            }}
            hasSubmenu
            isOpen={isLibraryOpen}
          />

          {/* Library Sub-menu - Smart Collections */}
          {isLibraryOpen && (
            <div className="ml-9 border-l border-zinc-800 space-y-1 animate-in slide-in-from-left-2 duration-200">
              <SmartCollectionsSidebar
                activeCollectionId={activeSmartCollectionId}
                onSelectCollection={onSmartCollectionSelect}
                isInline
              />

              {/* Storage Cleanup */}
              <button
                onClick={() => onViewChange('cleanup')}
                className={`w-full text-left px-4 py-2 text-sm rounded-r-lg transition-colors ${
                  view === 'cleanup'
                    ? 'text-indigo-400 bg-indigo-500/5'
                    : 'text-zinc-500 hover:text-zinc-300'
                }`}
              >
                <div className="flex items-center gap-2">
                  <Trash2 size={14} />
                  <span>Storage Cleanup</span>
                </div>
              </button>
            </div>
          )}
        </div>

        <SidebarItem
          icon={LayoutDashboard}
          label={t('nav.dashboard')}
          active={view === 'dashboard'}
          onClick={() => onViewChange('dashboard')}
        />

        {/* Settings with submenu */}
        {renderSettingsMenu()}
      </nav>

      <VersionFooter />
    </aside>
  );
};

export default Sidebar;
