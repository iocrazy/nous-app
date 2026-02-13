import React, { useState } from 'react';
import { useTranslation } from 'react-i18next';
import {
  Search,
  Settings,
  FolderKanban,
  Layers,
  Users,
  Sparkles,
  ChevronDown,
  FolderOpen,
  ListTodo,
  ArrowLeft,
  Upload,
  MessageSquare,
  CheckCircle2,
  Activity,
  CreditCard,
} from 'lucide-react';
import { Team, Project, SidebarMode, ViewState } from '../types';
import { SmartCollection } from '../services/smartCollectionService';
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
// SidebarSubItem (indented child)
// ---------------------------------------------------------------------------

const SidebarSubItem: React.FC<{
  icon: React.ElementType;
  label: string;
  active: boolean;
  onClick: () => void;
}> = ({ icon: Icon, label, active, onClick }) => (
  <button
    onClick={onClick}
    className={`w-full flex items-center gap-3 pl-11 pr-3 py-2 rounded-xl text-sm transition-all duration-200 group ${
      active
        ? 'bg-indigo-500/10 text-indigo-400'
        : 'text-zinc-500 hover:text-zinc-300 hover:bg-zinc-800/50'
    }`}
  >
    <Icon size={16} className={active ? 'text-indigo-400' : 'text-zinc-600 group-hover:text-zinc-400'} />
    <span>{label}</span>
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
  // Props kept for App.tsx compatibility (unused in render after refactor)
  settingsTab: _settingsTab,
  teams,
  activeTeamId,
  currentTeam,
  permissions,
  userName,
  activeProject,
  isLibraryOpen: _isLibraryOpen,
  isSettingsOpen: _isSettingsOpen,
  activeSmartCollectionId: _activeSmartCollectionId,
  onViewChange,
  onSettingsTabChange: _onSettingsTabChange,
  onToggleLibrary: _onToggleLibrary,
  onToggleSettings: _onToggleSettings,
  onTeamChange,
  onCreateTeam,
  onProjectBack,
  onSmartCollectionSelect: _onSmartCollectionSelect,
  onProjectSelect: _onProjectSelect,
}) => {
  const { t } = useTranslation();
  const [isManagementOpen, setIsManagementOpen] = useState(view === 'members' || view === 'billing');

  // ===================================================================
  // PROJECT MODE (unchanged)
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
            <span>{t('sidebar.backToProjects')}</span>
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
            {t('sidebar.files')}
          </p>
          <SidebarItem
            icon={FolderOpen}
            label={t('sidebar.allFiles')}
            active={view === 'mediatrack'}
            onClick={() => onViewChange('mediatrack')}
          />
          {hasPermission(permissions, 'resource.upload') && (
            <SidebarItem
              icon={Upload}
              label={t('sidebar.upload')}
              active={false}
              onClick={() => onViewChange('mediatrack')}
            />
          )}

          <div className="my-2" />

          {/* REVIEW section */}
          <p className="px-3 text-[10px] font-semibold text-zinc-500 uppercase tracking-wider mb-1">
            {t('sidebar.review')}
          </p>
          <SidebarItem
            icon={MessageSquare}
            label={t('sidebar.comments')}
            active={false}
            onClick={() => onViewChange('mediatrack')}
          />
          <SidebarItem
            icon={CheckCircle2}
            label={t('sidebar.status')}
            active={false}
            onClick={() => onViewChange('mediatrack')}
          />

          <Divider />

          {/* Bottom section */}
          <SidebarItem
            icon={Users}
            label={t('sidebar.members')}
            active={view === 'members'}
            onClick={() => onViewChange('members')}
          />
          <SidebarItem
            icon={Activity}
            label={t('sidebar.activity')}
            active={false}
            onClick={() => onViewChange('mediatrack')}
          />
          {hasPermission(permissions, 'project.manage') && (
            <SidebarItem
              icon={Settings}
              label={t('sidebar.projectSettings')}
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

        <div className="mb-2">
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
          <SidebarItem
            icon={Layers}
            label={t('sidebar.resources')}
            active={view === 'resources'}
            onClick={() => onViewChange('resources')}
          />
          <SidebarItem
            icon={FolderKanban}
            label={t('sidebar.projects')}
            active={view === 'mediatrack'}
            onClick={() => onViewChange('mediatrack')}
          />
          <SidebarItem
            icon={ListTodo}
            label={t('sidebar.todolist')}
            active={view === 'todolist'}
            onClick={() => onViewChange('todolist')}
          />
          {hasPermission(permissions, 'member.view') && (
            <>
              <SidebarItem
                icon={Settings}
                label={t('sidebar.management')}
                active={view === 'members' || view === 'billing'}
                onClick={() => setIsManagementOpen(!isManagementOpen)}
                hasSubmenu
                isOpen={isManagementOpen}
              />
              {isManagementOpen && (
                <div className="space-y-0.5">
                  <SidebarSubItem
                    icon={Users}
                    label={t('sidebar.members')}
                    active={view === 'members'}
                    onClick={() => onViewChange('members')}
                  />
                  <SidebarSubItem
                    icon={CreditCard}
                    label={t('sidebar.billing')}
                    active={view === 'billing'}
                    onClick={() => onViewChange('billing')}
                  />
                </div>
              )}
            </>
          )}
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

      <div className="mb-2">
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
        <SidebarItem
          icon={Layers}
          label={t('sidebar.resources')}
          active={view === 'resources'}
          onClick={() => onViewChange('resources')}
        />
        <SidebarItem
          icon={FolderKanban}
          label={t('sidebar.projects')}
          active={view === 'mediatrack'}
          onClick={() => onViewChange('mediatrack')}
        />
        <SidebarItem
          icon={ListTodo}
          label={t('sidebar.todolist')}
          active={view === 'todolist'}
          onClick={() => onViewChange('todolist')}
        />
      </nav>

      <VersionFooter />
    </aside>
  );
};

export default Sidebar;
