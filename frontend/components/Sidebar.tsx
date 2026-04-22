import React, { useState } from 'react';
import { useNavigate, useLocation } from 'react-router-dom';
import { useTranslation } from 'react-i18next';
import {
  Search,
  Settings,
  FolderKanban,
  Layers,
  Users,
  Sparkles,
  ChevronDown,
  ChevronLeft,
  ChevronRight,
  FolderOpen,
  ListTodo,
  ArrowLeft,
  Upload,
  MessageSquare,
  CheckCircle2,
  Activity,
  CreditCard,
  Coins,
  Share2,
} from 'lucide-react';
import { Team, Project, SidebarMode, ViewState } from '../types';
import { SmartCollection } from '../services/smartCollectionService';
import { WorkspaceSwitcher } from './WorkspaceSwitcher';
import { hasPermission } from '../utils/permissions';
import { VIEW_PATH_MAP, pathnameToView } from '../utils/routeConfig';

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
  collapsed?: boolean;
}

const SidebarItem: React.FC<SidebarItemProps> = ({
  icon: Icon,
  label,
  active,
  onClick,
  hasSubmenu = false,
  isOpen = false,
  collapsed = false,
}) => (
  <button
    onClick={onClick}
    title={collapsed ? label : undefined}
    className={`w-full flex items-center ${collapsed ? 'justify-center px-0 py-2.5' : 'justify-between px-3 py-2.5'} rounded-xl text-sm font-medium transition-all duration-200 group ${
      active
        ? 'bg-indigo-500/10 text-indigo-400'
        : 'text-zinc-400 hover:text-zinc-200 hover:bg-zinc-800/50'
    }`}
  >
    <div className={`flex items-center ${collapsed ? 'justify-center' : 'gap-3'}`}>
      <Icon size={collapsed ? 24 : 20} className={`flex-shrink-0 ${active ? 'text-indigo-400' : 'text-zinc-500 group-hover:text-zinc-300'}`} />
      {!collapsed && <span>{label}</span>}
    </div>
    {hasSubmenu && !collapsed && (
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
  view?: ViewState;
  settingsTab: string;

  // Collapse state
  collapsed?: boolean;
  onToggleCollapse?: () => void;

  // Team context
  teams: Team[];
  activeTeamId: string | null;
  personalTeamId: string | null;
  currentTeam: Team | null;
  permissions: string[];
  isViewEnabled?: (viewKey: string) => boolean;
  userName?: string;
  userRole?: string;

  // Project context (for project mode)
  activeProject: Project | null;

  // Library submenu state
  isLibraryOpen: boolean;
  isSettingsOpen: boolean;
  activeSmartCollectionId: number | null;

  // Callbacks
  onViewChange?: (view: string) => void;
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

const Logo: React.FC<{ collapsed?: boolean }> = ({ collapsed = false }) => (
  <div className={`flex items-center ${collapsed ? 'justify-center mb-6' : 'gap-3 mb-10 px-2'}`}>
    <div className={`${collapsed ? 'w-10 h-10' : 'w-8 h-8'} bg-gradient-to-br from-indigo-500 to-purple-600 rounded-lg flex items-center justify-center shadow-lg shadow-indigo-500/20 transition-all duration-200`}>
      <Sparkles className={`${collapsed ? 'w-6 h-6' : 'w-5 h-5'} text-white`} />
    </div>
    {!collapsed && <span className="text-xl font-bold tracking-tight">MediaHub</span>}
  </div>
);

// ---------------------------------------------------------------------------
// Edge collapse button — right-edge tab, mirrors ResourcesSidebar / ProjectNavSidebar.
// - Expanded: hover-reveal (opacity-0 group-hover:opacity-100) — keeps the sidebar clean.
// - Collapsed: always visible (opacity-100) — without this the button is unreachable
//   because hovering the narrow collapsed rail doesn't read as "there's a control here".
// ---------------------------------------------------------------------------

const EdgeCollapseButton: React.FC<{ collapsed?: boolean; onToggleCollapse?: () => void }> = ({
  collapsed = false,
  onToggleCollapse,
}) => {
  if (!onToggleCollapse) return null;
  return (
    <button
      onClick={onToggleCollapse}
      className={`absolute top-1/2 -translate-y-1/2 right-0 z-10 w-4 h-10 flex items-center justify-center rounded-l-md bg-zinc-800/80 text-zinc-500 hover:text-zinc-200 hover:bg-zinc-700 transition-colors ${
        collapsed ? 'opacity-100' : 'opacity-0 group-hover:opacity-100'
      }`}
      title={collapsed ? 'Expand sidebar' : 'Collapse sidebar'}
    >
      {collapsed ? <ChevronRight size={12} /> : <ChevronLeft size={12} />}
    </button>
  );
};

// ---------------------------------------------------------------------------
// Divider
// ---------------------------------------------------------------------------

const Divider: React.FC = () => <div className="my-3 border-t border-zinc-800" />;

// ---------------------------------------------------------------------------
// Main Sidebar export
// ---------------------------------------------------------------------------

export const Sidebar: React.FC<SidebarProps> = ({
  mode,
  view: _view,
  // Props kept for App.tsx compatibility (unused in render after refactor)
  settingsTab: _settingsTab,
  collapsed = false,
  onToggleCollapse,
  teams,
  activeTeamId,
  personalTeamId,
  currentTeam,
  permissions,
  isViewEnabled = () => true,
  userName,
  userRole,
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
  const location = useLocation();
  const navigate = useNavigate();
  const currentView = pathnameToView(location.pathname);

  // Navigate via URL and notify parent for side effects
  const handleNav = (viewKey: string) => {
    const basePath = VIEW_PATH_MAP[viewKey] || '/parser';
    const path = activeTeamId ? `/team/${activeTeamId}${basePath}` : basePath;
    navigate(path);
    onViewChange?.(viewKey);
  };

  const [isManagementOpen, setIsManagementOpen] = useState(currentView === 'members' || currentView === 'billing');

  // ===================================================================
  // PROJECT MODE (unchanged)
  // ===================================================================
  if (mode === 'project') {
    return (
      <aside className={`group hidden sm:flex flex-col ${collapsed ? 'w-20' : 'w-64'} border-r border-zinc-800 bg-zinc-950 ${collapsed ? 'p-3' : 'p-6'} fixed top-0 left-0 h-full z-10 transition-all duration-300`}>
        {/* Back to projects + team name */}
        {collapsed ? (
          <div className="mb-6 flex justify-center">
            <button onClick={onProjectBack} className="p-2 rounded-lg text-zinc-400 hover:text-zinc-200 hover:bg-zinc-800/50 transition-colors" title={t('sidebar.backToProjects')}>
              <ArrowLeft size={24} />
            </button>
          </div>
        ) : (
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
        )}

        {/* Project name */}
        {activeProject && !collapsed && (
          <div className="px-2 mb-6">
            <p className="text-base font-bold text-zinc-100 truncate">{activeProject.name}</p>
          </div>
        )}

        <nav className="flex-1 space-y-1">
          {/* FILES section */}
          {!collapsed && (
            <p className="px-3 text-[10px] font-semibold text-zinc-500 uppercase tracking-wider mb-1">
              {t('sidebar.files')}
            </p>
          )}
          <SidebarItem icon={FolderOpen} label={t('sidebar.allFiles')} active={currentView === 'mediatrack'} onClick={() => handleNav('mediatrack')} collapsed={collapsed} />
          {hasPermission(permissions, 'resource.upload') && (
            <SidebarItem icon={Upload} label={t('sidebar.upload')} active={false} onClick={() => handleNav('mediatrack')} collapsed={collapsed} />
          )}

          <div className="my-2" />

          {/* REVIEW section */}
          {!collapsed && (
            <p className="px-3 text-[10px] font-semibold text-zinc-500 uppercase tracking-wider mb-1">
              {t('sidebar.review')}
            </p>
          )}
          <SidebarItem icon={MessageSquare} label={t('sidebar.comments')} active={false} onClick={() => handleNav('mediatrack')} collapsed={collapsed} />
          <SidebarItem icon={CheckCircle2} label={t('sidebar.status')} active={false} onClick={() => handleNav('mediatrack')} collapsed={collapsed} />

          <Divider />

          {/* Bottom section */}
          <SidebarItem icon={Users} label={t('sidebar.members')} active={currentView === 'members'} onClick={() => handleNav('members')} collapsed={collapsed} />
          <SidebarItem icon={Activity} label={t('sidebar.activity')} active={false} onClick={() => handleNav('mediatrack')} collapsed={collapsed} />
          {hasPermission(permissions, 'project.manage') && (
            <SidebarItem icon={Settings} label={t('sidebar.projectSettings')} active={currentView === 'settings'} onClick={() => handleNav('settings')} collapsed={collapsed} />
          )}
        </nav>

        <EdgeCollapseButton collapsed={collapsed} onToggleCollapse={onToggleCollapse} />
      </aside>
    );
  }

  // ===================================================================
  // TEAM MODE
  // ===================================================================
  if (mode === 'team') {
    return (
      <aside className={`group hidden sm:flex flex-col ${collapsed ? 'w-20' : 'w-64'} border-r border-zinc-800 bg-zinc-950 ${collapsed ? 'p-3' : 'p-6'} fixed top-0 left-0 h-full z-10 transition-all duration-300`}>
        <Logo collapsed={collapsed} />

        <div className="mb-2">
          <WorkspaceSwitcher
            teams={teams}
            activeTeamId={activeTeamId}
            personalTeamId={personalTeamId}
            currentTeam={currentTeam}
            userName={userName}
            onTeamChange={onTeamChange}
            onCreateTeam={onCreateTeam}
            collapsed={collapsed}
          />
        </div>

        <nav className="flex-1 space-y-3">
          {isViewEnabled('resources') && (
            <SidebarItem icon={Layers} label={t('sidebar.resources')} active={currentView === 'resources'} onClick={() => handleNav('resources')} collapsed={collapsed} />
          )}
          {isViewEnabled('mediatrack') && (
            <SidebarItem icon={FolderKanban} label={t('sidebar.projects')} active={currentView === 'mediatrack'} onClick={() => handleNav('mediatrack')} collapsed={collapsed} />
          )}
          <SidebarItem icon={ListTodo} label={t('sidebar.todolist')} active={currentView === 'todolist'} onClick={() => handleNav('todolist')} collapsed={collapsed} />
          <SidebarItem icon={Share2} label={t('sidebar.shared')} active={currentView === 'shared'} onClick={() => handleNav('shared')} collapsed={collapsed} />
          {hasPermission(permissions, 'member.view') && (
            <>
              <SidebarItem
                icon={Settings}
                label={t('sidebar.management')}
                active={currentView === 'members' || currentView === 'billing'}
                onClick={() => collapsed ? handleNav('members') : setIsManagementOpen(!isManagementOpen)}
                hasSubmenu={!collapsed}
                isOpen={isManagementOpen}
                collapsed={collapsed}
              />
              {isManagementOpen && !collapsed && (
                <div className="space-y-0.5">
                  <SidebarSubItem icon={Users} label={t('sidebar.members')} active={currentView === 'members'} onClick={() => handleNav('members')} />
                  <SidebarSubItem icon={CreditCard} label={t('sidebar.billing')} active={currentView === 'billing'} onClick={() => handleNav('billing')} />
                </div>
              )}
            </>
          )}

        </nav>

        <EdgeCollapseButton collapsed={collapsed} onToggleCollapse={onToggleCollapse} />
      </aside>
    );
  }

  // ===================================================================
  // PERSONAL MODE (default)
  // ===================================================================
  return (
    <aside className={`group hidden sm:flex flex-col ${collapsed ? 'w-20' : 'w-64'} border-r border-zinc-800 bg-zinc-950 ${collapsed ? 'p-3' : 'p-6'} fixed top-0 left-0 h-full z-10 transition-all duration-300`}>
      <Logo collapsed={collapsed} />

      <div className="mb-2">
        <WorkspaceSwitcher
          teams={teams}
          activeTeamId={activeTeamId}
          personalTeamId={personalTeamId}
          currentTeam={currentTeam}
          userName={userName}
          onTeamChange={onTeamChange}
          onCreateTeam={onCreateTeam}
          collapsed={collapsed}
        />
      </div>

      <nav className="flex-1 space-y-3">
        {isViewEnabled('parser') && (
          <SidebarItem icon={Search} label={t('nav.linkParser')} active={currentView === 'parser'} onClick={() => handleNav('parser')} collapsed={collapsed} />
        )}
        {isViewEnabled('resources') && (
          <SidebarItem icon={Layers} label={t('sidebar.resources')} active={currentView === 'resources'} onClick={() => handleNav('resources')} collapsed={collapsed} />
        )}
        {isViewEnabled('mediatrack') && (
          <SidebarItem icon={FolderKanban} label={t('sidebar.projects')} active={currentView === 'mediatrack'} onClick={() => handleNav('mediatrack')} collapsed={collapsed} />
        )}
        <SidebarItem icon={ListTodo} label={t('sidebar.todolist')} active={currentView === 'todolist'} onClick={() => handleNav('todolist')} collapsed={collapsed} />
        <SidebarItem icon={Share2} label={t('sidebar.shared')} active={currentView === 'shared'} onClick={() => handleNav('shared')} collapsed={collapsed} />

        <Divider />

        <SidebarItem icon={Coins} label={t('sidebar.points')} active={currentView === 'points'} onClick={() => handleNav('points')} collapsed={collapsed} />

      </nav>

      <EdgeCollapseButton collapsed={collapsed} onToggleCollapse={onToggleCollapse} />
    </aside>
  );
};

export default Sidebar;
