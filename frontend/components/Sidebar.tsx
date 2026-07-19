import React, { useState } from 'react';
import { useNavigate, useLocation } from 'react-router-dom';
import { useTranslation } from 'react-i18next';
import {
  Lightbulb,
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
  Send,
} from 'lucide-react';
import { Team, Project, SidebarMode, ViewState } from '../types';
import { SmartCollection } from '../services/smartCollectionService';
import { WorkspaceSwitcher } from './WorkspaceSwitcher';
import { SidebarSection } from './sidebar/SidebarSection';
import { hasPermission } from '../utils/permissions';
import { VIEW_PATH_MAP, pathnameToView } from '../utils/routeConfig';
import { useTopicModuleStatus } from '../hooks/useTopicModuleEnabled';
import { useDistributionModuleStatus } from '../hooks/useDistributionModuleStatus';

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
        ? 'bg-[var(--accent-soft)] text-[var(--accent-text)]'
        : 'text-content-2 hover:text-content hover:bg-island-2'
    }`}
  >
    <div className={`flex items-center ${collapsed ? 'justify-center' : 'gap-3'}`}>
      <Icon size={collapsed ? 24 : 20} className={`flex-shrink-0 ${active ? 'text-[var(--accent-text)]' : 'text-content-3 group-hover:text-content-2'}`} />
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
        ? 'bg-[var(--accent-soft)] text-[var(--accent-text)]'
        : 'text-content-3 hover:text-content-2 hover:bg-island-2'
    }`}
  >
    <Icon size={16} className={active ? 'text-[var(--accent-text)]' : 'text-content-4 group-hover:text-content-2'} />
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

  /** Force the 54px icon rail (detail routes, spec D5) — overrides `collapsed`. */
  iconRail?: boolean;

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
// Floating collapse tab — mirrors the Details panel's affordance (bottom, outside
// the panel's outer edge). For the left sidebar that means sticking out to the
// right at -right-10 bottom-8, with a right-rounded shell. Always visible so users
// can collapse/expand without hunting.
//
// Details panel reference (DownloadInfoPanel.tsx:87):
//   -left-10 bottom-8 w-10 h-12 rounded-l-xl border-l border-y
// Main sidebar (mirrored):
//   -right-10 bottom-8 w-10 h-12 rounded-r-xl border-r border-y
// ---------------------------------------------------------------------------

const FloatingCollapseTab: React.FC<{ collapsed?: boolean; onToggleCollapse?: () => void }> = ({
  collapsed = false,
  onToggleCollapse,
}) => {
  if (!onToggleCollapse) return null;
  return (
    <button
      onClick={onToggleCollapse}
      className={`absolute -right-3 bottom-8 w-10 h-12 bg-island border-r border-y border-line rounded-r-xl flex items-center justify-center text-content-2 hover:text-content cursor-pointer hover:bg-ink-800 transition-colors z-10`}
      title={collapsed ? 'Expand sidebar' : 'Collapse sidebar'}
      aria-label={collapsed ? 'Expand sidebar' : 'Collapse sidebar'}
    >
      {collapsed ? <ChevronRight size={20} /> : <ChevronLeft size={20} />}
    </button>
  );
};

// ---------------------------------------------------------------------------
// Divider
// ---------------------------------------------------------------------------

const Divider: React.FC = () => <div className="my-3 border-t border-line" />;

// ---------------------------------------------------------------------------
// Main Sidebar export
// ---------------------------------------------------------------------------

export const Sidebar: React.FC<SidebarProps> = ({
  mode,
  view: _view,
  // Props kept for App.tsx compatibility (unused in render after refactor)
  settingsTab: _settingsTab,
  collapsed: collapsedProp = false,
  onToggleCollapse,
  iconRail = false,
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
  // Topic Inspiration display switch (admin) — hide the nav item only when the
  // module is not VISIBLE; a paused pipeline (enabled=false) keeps the entry.
  const { visible: topicModuleVisible } = useTopicModuleStatus();
  // Distribution display switch (admin, DB-backed). Opt-in / fail-closed: the
  // nav entry only appears once an admin flips `distribution.module.visible`.
  const { visible: distributionModuleVisible } = useDistributionModuleStatus();

  // iconRail (detail routes, D5) pins the narrow rail → render icon-only.
  const collapsed = iconRail || collapsedProp;

  // The card chrome (bg/border/radius/shadow) is drawn by the shell wrapper, so
  // the aside is a plain fill (relative, full height).
  // Nav-island width follows spec §2: list pages = 200px, detail rail = 54px.
  const railW = iconRail ? 'w-[54px]' : collapsed ? 'w-20' : 'w-[200px]';
  const railPad = iconRail ? 'px-1.5 py-3' : collapsed ? 'p-3' : 'p-4';
  // `island-nav` opts the full-width island nav (200px, list pages) into the
  // tighter mock row density (index.css). Not added for the 54px icon rail
  // (collapsed) → that keeps its own spacing.
  const asideClass = `group hidden sm:flex flex-col ${railW} ${railPad} relative h-full transition-all duration-300${!collapsed ? ' island-nav' : ''}`;

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
      <aside className={asideClass}>
        {/* Back to projects + team name */}
        {collapsed ? (
          <div className="mb-6 flex justify-center">
            <button onClick={onProjectBack} className={`p-2 rounded-lg text-content-2 hover:text-content hover:bg-island-2 transition-colors`} title={t('sidebar.backToProjects')}>
              <ArrowLeft size={24} />
            </button>
          </div>
        ) : (
          <div className="mb-6">
            <button
              onClick={onProjectBack}
              className={`flex items-center gap-2 text-sm text-content-2 hover:text-content transition-colors mb-2`}
            >
              <ArrowLeft size={16} />
              <span>{t('sidebar.backToProjects')}</span>
            </button>
            {currentTeam && (
              <p className={`px-1 text-xs text-content-3 truncate`}>{currentTeam.name}</p>
            )}
          </div>
        )}

        {/* Project name */}
        {activeProject && !collapsed && (
          <div className="px-2 mb-6">
            <p className={`text-base font-bold text-content truncate`}>{activeProject.name}</p>
          </div>
        )}

        <nav className="flex-1 space-y-1">
          {/* FILES section */}
          {!collapsed && (
            <p className={`px-3 text-[10px] font-semibold text-content-3 uppercase tracking-wider mb-1`}>
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
            <p className={`px-3 text-[10px] font-semibold text-content-3 uppercase tracking-wider mb-1`}>
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

        {!iconRail && <FloatingCollapseTab collapsed={collapsed} onToggleCollapse={onToggleCollapse} />}
      </aside>
    );
  }

  // ===================================================================
  // TEAM MODE
  // ===================================================================
  if (mode === 'team') {
    return (
      <aside className={asideClass}>
        {/* Brand wordmark lives in the global topbar (spec §2), so the nav island
            never repeats it — list nav starts with the account switcher, detail
            rail is icons only (matches the mockups). */}

        {!iconRail && (
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
        )}

        <nav className="flex-1 flex flex-col gap-4">
          <SidebarSection label="" hideLabel>
            {isViewEnabled('resources') && (
              <SidebarItem icon={Layers} label={t('sidebar.resources')} active={currentView === 'resources'} onClick={() => handleNav('resources')} collapsed={collapsed} />
            )}
            {isViewEnabled('mediatrack') && (
              <SidebarItem icon={FolderKanban} label={t('sidebar.projects')} active={currentView === 'mediatrack'} onClick={() => handleNav('mediatrack')} collapsed={collapsed} />
            )}
            <SidebarItem icon={ListTodo} label={t('sidebar.todolist')} active={currentView === 'todolist'} onClick={() => handleNav('todolist')} collapsed={collapsed} />
            <SidebarItem icon={Share2} label={t('sidebar.shared')} active={currentView === 'shared'} onClick={() => handleNav('shared')} collapsed={collapsed} />
            <SidebarItem icon={MessageSquare} label={t('chat.title')} active={currentView === 'chat'} onClick={() => handleNav('chat')} collapsed={collapsed} />
            <SidebarItem
              icon={Sparkles}
              label={t('sidebar.aiLibrary', 'AI Library')}
              active={currentView === 'ailibrary'}
              onClick={() => handleNav('ailibrary')}
              collapsed={collapsed}
            />
            {distributionModuleVisible && (
              <SidebarItem
                icon={Send}
                label={t('sidebar.distribution', 'Distribution')}
                active={currentView === 'distribution'}
                onClick={() => handleNav('distribution')}
                collapsed={collapsed}
              />
            )}
          </SidebarSection>

          {hasPermission(permissions, 'member.view') && (
            <SidebarSection label="" hideLabel>
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
            </SidebarSection>
          )}
        </nav>

        {!iconRail && <FloatingCollapseTab collapsed={collapsed} onToggleCollapse={onToggleCollapse} />}
      </aside>
    );
  }

  // ===================================================================
  // PERSONAL MODE (default)
  // ===================================================================
  return (
    <aside className={asideClass}>
      {/* Brand wordmark lives in the global topbar (spec §2), so the nav island
          never repeats it. */}

      {!iconRail && (
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
      )}

      <nav className="flex-1 flex flex-col gap-4">
        <SidebarSection label="" hideLabel>
          {isViewEnabled('parser') && topicModuleVisible && (
            <SidebarItem icon={Lightbulb} label={t('nav.topicInspiration')} active={currentView === 'parser'} onClick={() => handleNav('parser')} collapsed={collapsed} />
          )}
          {isViewEnabled('resources') && (
            <SidebarItem icon={Layers} label={t('sidebar.resources')} active={currentView === 'resources'} onClick={() => handleNav('resources')} collapsed={collapsed} />
          )}
          {isViewEnabled('mediatrack') && (
            <SidebarItem icon={FolderKanban} label={t('sidebar.projects')} active={currentView === 'mediatrack'} onClick={() => handleNav('mediatrack')} collapsed={collapsed} />
          )}
          <SidebarItem icon={ListTodo} label={t('sidebar.todolist')} active={currentView === 'todolist'} onClick={() => handleNav('todolist')} collapsed={collapsed} />
          <SidebarItem icon={Share2} label={t('sidebar.shared')} active={currentView === 'shared'} onClick={() => handleNav('shared')} collapsed={collapsed} />
          <SidebarItem icon={Coins} label={t('sidebar.points')} active={currentView === 'points'} onClick={() => handleNav('points')} collapsed={collapsed} />
          <SidebarItem
            icon={Sparkles}
            label={t('sidebar.aiLibrary', 'AI Library')}
            active={currentView === 'ailibrary'}
            onClick={() => handleNav('ailibrary')}
            collapsed={collapsed}
          />
          {distributionModuleVisible && (
            <SidebarItem
              icon={Send}
              label={t('sidebar.distribution', 'Distribution')}
              active={currentView === 'distribution'}
              onClick={() => handleNav('distribution')}
              collapsed={collapsed}
            />
          )}
        </SidebarSection>
      </nav>

      {!iconRail && <FloatingCollapseTab collapsed={collapsed} onToggleCollapse={onToggleCollapse} />}
    </aside>
  );
};

export default Sidebar;
