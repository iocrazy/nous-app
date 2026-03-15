import React, { useState, useEffect } from 'react';
import { Outlet, useLocation, useNavigate, useParams } from 'react-router-dom';
import {
  Search, Library, User, FolderOpen,
  LayoutList, LayoutGrid, Smartphone,
} from 'lucide-react';
import { useTranslation } from 'react-i18next';
import { ViewState, PointPackage } from '../types';
import { useAuth } from '../contexts/AuthContext';
import { useTeamContext } from '../contexts/TeamContext';
import { useNavigation } from '../hooks/useNavigation';
import { useLibrary } from '../hooks/useLibrary';
import { Sidebar } from './Sidebar';
import { TopBar } from './TopBar';
import { ToastProvider } from './Toast';
import { UploadProvider } from '../contexts/UploadContext';
import { TaskManagerProvider } from '../contexts/TaskManagerContext';
import { UserProfileModal } from './UserProfileModal';
import { CreateTeamModal } from './CreateTeamModal';
import { SettingsModal } from './SettingsModal';
import { CreateCollectionModal } from './CreateCollectionModal';
import { CreateProjectModal } from './CreateProjectModal';
import { PaymentModal } from './PaymentModal';

// ---------------------------------------------------------------------------
// Map URL pathname → ViewState
// Supports both /team/:teamId/:view and legacy /:view patterns
// ---------------------------------------------------------------------------

export function pathnameToView(pathname: string): ViewState {
  // Strip /team/:teamId/ prefix if present
  const stripped = pathname.replace(/^\/team\/[^/]+/, '');
  if (stripped.startsWith('/library')) return 'library';
  if (stripped.startsWith('/dashboard')) return 'dashboard';
  if (stripped.startsWith('/settings')) return 'settings';
  if (stripped.startsWith('/cleanup')) return 'cleanup';
  if (stripped.startsWith('/projects')) return 'mediatrack';
  if (stripped.startsWith('/points')) return 'points';
  if (stripped.startsWith('/billing')) return 'billing';
  if (stripped.startsWith('/members')) return 'members';
  if (stripped.startsWith('/resources')) return 'resources';
  if (stripped.startsWith('/todolist')) return 'todolist';
  return 'parser';
}

// ---------------------------------------------------------------------------
// AppLayout
// ---------------------------------------------------------------------------

export function AppLayout() {
  const { t } = useTranslation();
  const location = useLocation();
  const navigate = useNavigate();
  const { teamId: urlTeamId } = useParams();

  const {
    currentUserId, userProfile, userSettings, aiSettings,
    isProfileModalOpen, setIsProfileModalOpen, setUserProfile, setAISettings,
    handleLogout, handleUpdateSettings,
  } = useAuth();

  const {
    teams, personalTeamId, selectedTeamId, setSelectedTeamId, notifications,
    currentTeam, userPermissions, isModuleEnabled, isViewEnabled,
    isCreateTeamModalOpen, setIsCreateTeamModalOpen,
    isSettingsModalOpen, setIsSettingsModalOpen, settingsModalInitialTab, setSettingsModalInitialTab,
    handleCreateTeam, handleTeamCreated, handleTeamUpdated, handleTeamDeleted, handleTeamLeft,
    handleMarkNotificationRead, handleMarkAllNotificationsRead,
  } = useTeamContext();

  // Sync teamId from URL to context
  useEffect(() => {
    if (urlTeamId && urlTeamId !== selectedTeamId) {
      setSelectedTeamId(urlTeamId);
    }
  }, [urlTeamId, selectedTeamId, setSelectedTeamId]);

  const view = pathnameToView(location.pathname);

  const {
    settingsTab, setSettingsTab,
    selectedProject, setSelectedProject,
    reviewFile, setReviewFile,
    isCreateProjectModalOpen, setIsCreateProjectModalOpen,
    isLibraryOpen, isSettingsOpen,
    isMobileMenuOpen, setIsMobileMenuOpen,
    dashboardSubView, setDashboardSubView,
    isDashboardMenuOpen, setIsDashboardMenuOpen,
    sidebarMode,
    toggleLibraryMenu, toggleSettingsMenu,
  } = useNavigation({ isAuthenticated: true, selectedTeamId, personalTeamId });

  const {
    library, setLibrary,
    collections, activeLibraryTab, setActiveLibraryTab,
    activeCollectionId, setActiveCollectionId,
    searchResults, setSearchResults, isSearchActive, setIsSearchActive,
    searchQueryText, setSearchQueryText,
    activeSmartCollectionId, setActiveSmartCollectionId,
    isCreateCollectionModalOpen, setIsCreateCollectionModalOpen,
    libraryViewMode, setLibraryViewMode,
    selectedLibraryItem, setSelectedLibraryItem,
    loadLibraryData, handleCreateCollection,
  } = useLibrary({
    isAuthenticated: true,
    selectedTeamId,
  });

  const [selectedPaymentPackage, setSelectedPaymentPackage] = useState<PointPackage | null>(null);

  // Sidebar collapse state with localStorage persistence
  const [sidebarCollapsed, setSidebarCollapsed] = useState(() => {
    try { return localStorage.getItem('sidebar-collapsed') === 'true'; } catch { return false; }
  });
  const handleToggleSidebar = () => {
    setSidebarCollapsed(prev => {
      const next = !prev;
      try { localStorage.setItem('sidebar-collapsed', String(next)); } catch { /* ignore */ }
      return next;
    });
  };

  const handleAuthLogout = async () => {
    await handleLogout();
    navigate('/login');
  };

  // Main content padding — mobile: clear TopBar (pt-14) + Tab Bar (pb-20)
  const mainContentClass = `flex-1 ${sidebarCollapsed ? 'sm:ml-20' : 'sm:ml-64'} w-full transition-[margin] duration-300 ${
    view === 'library'
      ? 'pt-14 pb-20 sm:p-8 sm:pt-20 sm:pb-8'
      : view === 'resources'
        ? 'pt-14 pb-20 sm:p-8 sm:pt-20 sm:pb-8'
        : 'px-4 pt-14 pb-20 sm:px-8 sm:pt-20 sm:pb-8'
  }`;

  // Helper: build team-scoped path
  const teamPath = (path: string) => {
    const tid = urlTeamId || selectedTeamId || personalTeamId;
    return tid ? `/team/${tid}${path}` : path;
  };

  // Mobile nav handlers
  const handleMobileNavClick = (targetView: ViewState) => {
    const viewToPath: Record<string, string> = {
      parser: '/parser',
      library: '/library',
      dashboard: '/dashboard',
      resources: '/resources',
      mediatrack: '/projects',
      settings: '/settings',
      cleanup: '/cleanup',
      points: '/points',
      billing: '/billing',
      members: '/members',
      todolist: '/todolist',
    };
    navigate(teamPath(viewToPath[targetView] || '/parser'));
    setIsMobileMenuOpen(false);
    setIsDashboardMenuOpen(false);
  };

  const handleMobileLibraryClick = () => {
    setIsDashboardMenuOpen(false);
    if (view === 'library') {
      if (selectedLibraryItem) {
        setSelectedLibraryItem(null);
      } else {
        setIsMobileMenuOpen(!isMobileMenuOpen);
      }
    } else {
      navigate(teamPath('/library'));
      setSelectedLibraryItem(null);
      setIsMobileMenuOpen(false);
    }
  };

  const handleMobileDashboardClick = () => {
    if (view === 'dashboard') {
      setIsDashboardMenuOpen(!isDashboardMenuOpen);
    } else {
      navigate(teamPath('/dashboard'));
      setIsDashboardMenuOpen(false);
    }
    setIsMobileMenuOpen(false);
  };

  return (
    <ToastProvider>
    <TaskManagerProvider>
    <UploadProvider>
    <div className="flex min-h-screen bg-black text-zinc-100 font-sans selection:bg-indigo-500/30 overflow-x-hidden">

      {/* User Profile Modal */}
      <UserProfileModal
        isOpen={isProfileModalOpen}
        onClose={() => setIsProfileModalOpen(false)}
        user={userProfile}
        onSave={(updated) => setUserProfile(updated)}
        onLogout={handleAuthLogout}
      />

      {/* Create Team Modal */}
      <CreateTeamModal
        isOpen={isCreateTeamModalOpen}
        onClose={() => setIsCreateTeamModalOpen(false)}
        onTeamCreated={handleTeamCreated}
      />

      {/* Settings Modal */}
      <SettingsModal
        isOpen={isSettingsModalOpen}
        onClose={() => setIsSettingsModalOpen(false)}
        initialTab={settingsModalInitialTab as any}
        user={{
          id: currentUserId || '',
          name: userProfile.name,
          email: userProfile.email,
          avatarUrl: userProfile.avatarUrl,
          bio: '',
        }}
        onUserUpdated={() => {}}
        settings={userSettings}
        onUpdateSettings={handleUpdateSettings}
        aiSettings={aiSettings}
        onSaveAISettings={setAISettings}
        currentTeam={currentTeam}
        isTeamOwner={!!currentTeam && currentTeam.owner_id === currentUserId}
        onTeamDeleted={handleTeamDeleted}
        onTeamLeft={handleTeamLeft}
        onTeamUpdated={handleTeamUpdated}
      />

      {/* Create Collection Modal */}
      <CreateCollectionModal
        isOpen={isCreateCollectionModalOpen}
        onClose={() => setIsCreateCollectionModalOpen(false)}
        onSubmit={handleCreateCollection}
        teams={teams}
        defaultTeamId={activeLibraryTab === 'team-library' ? selectedTeamId : null}
      />

      {/* Mobile Tab Bar — 4 tabs: Parser, Library, Resources, Me */}
      <div className="sm:hidden fixed bottom-0 left-0 right-0 z-40">
        {/* Backdrop to dismiss popups */}
        {isMobileMenuOpen && (
          <div
            className="fixed inset-0 z-30"
            onClick={() => setIsMobileMenuOpen(false)}
          />
        )}

        {/* Library view mode popup — positioned above tab bar */}
        {isMobileMenuOpen && view === 'library' && !selectedLibraryItem && (
          <div className="absolute bottom-full left-1/2 -translate-x-1/2 mb-2 bg-zinc-800/95 backdrop-blur-md border border-zinc-700 p-1.5 rounded-xl shadow-2xl flex gap-1 z-50 animate-in slide-in-from-bottom-2 fade-in duration-200">
            <button
              onClick={() => { setLibraryViewMode('list'); setIsMobileMenuOpen(false); }}
              className={`p-2.5 rounded-lg transition-all ${libraryViewMode === 'list' ? 'bg-zinc-200 text-zinc-900 shadow-sm' : 'text-zinc-400 hover:text-zinc-200 hover:bg-zinc-700/50'}`}
            >
              <LayoutList size={20} />
            </button>
            <button
              onClick={() => { setLibraryViewMode('grid'); setIsMobileMenuOpen(false); }}
              className={`p-2.5 rounded-lg transition-all ${libraryViewMode === 'grid' ? 'bg-zinc-200 text-zinc-900 shadow-sm' : 'text-zinc-400 hover:text-zinc-200 hover:bg-zinc-700/50'}`}
            >
              <LayoutGrid size={20} />
            </button>
            <button
              onClick={() => { setLibraryViewMode('feed'); setIsMobileMenuOpen(false); }}
              className={`p-2.5 rounded-lg transition-all ${libraryViewMode === 'feed' ? 'bg-zinc-200 text-zinc-900 shadow-sm' : 'text-zinc-400 hover:text-zinc-200 hover:bg-zinc-700/50'}`}
            >
              <Smartphone size={20} />
            </button>
          </div>
        )}

        {/* Tab buttons */}
        <div className="bg-zinc-950/95 backdrop-blur-xl border-t border-zinc-800/60 flex justify-around px-2 pt-1.5 pb-[env(safe-area-inset-bottom,6px)] relative z-40">
          {/* Parser */}
          <button
            onClick={() => handleMobileNavClick('parser')}
            className={`flex flex-col items-center gap-0.5 min-w-0 px-3 py-1 transition-colors ${view === 'parser' ? 'text-indigo-400' : 'text-zinc-500'}`}
          >
            <Search size={22} />
            <span className="text-[10px] leading-tight">Parser</span>
          </button>

          {/* Library */}
          <button
            onClick={handleMobileLibraryClick}
            className={`flex flex-col items-center gap-0.5 min-w-0 px-3 py-1 transition-colors ${view === 'library' ? 'text-indigo-400' : 'text-zinc-500'}`}
          >
            <Library size={22} />
            <span className="text-[10px] leading-tight">Library</span>
          </button>

          {/* Resources */}
          <button
            onClick={() => handleMobileNavClick('resources')}
            className={`flex flex-col items-center gap-0.5 min-w-0 px-3 py-1 transition-colors ${view === 'resources' ? 'text-indigo-400' : 'text-zinc-500'}`}
          >
            <FolderOpen size={22} />
            <span className="text-[10px] leading-tight">Resources</span>
          </button>

          {/* Profile */}
          <button
            onClick={() => setIsProfileModalOpen(true)}
            className="flex flex-col items-center gap-0.5 min-w-0 px-3 py-1 transition-colors text-zinc-500"
          >
            {userProfile.avatarUrl ? (
               <div className="w-[22px] h-[22px] rounded-full overflow-hidden border border-zinc-600">
                  <img src={userProfile.avatarUrl} alt="Profile" className="w-full h-full object-cover" />
               </div>
            ) : (
               <User size={22} />
            )}
            <span className="text-[10px] leading-tight">Me</span>
          </button>
        </div>
      </div>

      {/* Sidebar */}
      <Sidebar
        mode={sidebarMode}
        view={view}
        settingsTab={settingsTab}
        collapsed={sidebarCollapsed}
        onToggleCollapse={handleToggleSidebar}
        teams={teams}
        activeTeamId={selectedTeamId}
        personalTeamId={personalTeamId}
        currentTeam={currentTeam}
        permissions={userPermissions}
        isViewEnabled={isViewEnabled}
        userName={userProfile?.name}
        activeProject={selectedProject}
        isLibraryOpen={isLibraryOpen}
        isSettingsOpen={isSettingsOpen}
        activeSmartCollectionId={activeSmartCollectionId}
        onViewChange={(v) => {
          // Side effects only — Sidebar handles navigation directly via useNavigate
          if (v === 'mediatrack') { setSelectedProject(null); setReviewFile(null); }
        }}
        onSettingsTabChange={(tab) => setSettingsTab(tab as any)}
        onToggleLibrary={toggleLibraryMenu}
        onToggleSettings={toggleSettingsMenu}
        onTeamChange={(teamId) => {
          // WorkspaceSwitcher navigates via URL; just reset local state
          setActiveCollectionId(null);
          setSelectedProject(null);
          setReviewFile(null);
        }}
        onCreateTeam={handleCreateTeam}
        onProjectBack={() => { setSelectedProject(null); setReviewFile(null); }}
        onSmartCollectionSelect={(collection) => {
          setIsSearchActive(false);
          setSearchResults([]);
          setSearchQueryText('');
          setActiveSmartCollectionId(collection?.id || null);
          navigate(teamPath('/library'));
          setActiveCollectionId(null);
          setActiveLibraryTab('my-library');
        }}
        onProjectSelect={setSelectedProject}
      />

      {/* TopBar — fixed, sibling to Sidebar for clean positioning */}
      <TopBar
        user={userProfile ? { name: userProfile.name, email: userProfile.email, avatarUrl: userProfile.avatarUrl } : null}
        unreadCount={notifications.filter(n => !n.read).length}
        onSignOut={handleAuthLogout}
        onOpenSettings={(tab) => {
          setSettingsModalInitialTab(tab || 'personal');
          setIsSettingsModalOpen(true);
        }}
        sidebarCollapsed={sidebarCollapsed}
      />

      {/* Main Content */}
      <main className={mainContentClass}>
        {/* Routed content */}
        <Outlet />
      </main>

      {/* Payment Modal */}
      {selectedPaymentPackage && selectedTeamId && (
        <PaymentModal
          package={selectedPaymentPackage}
          teamId={selectedTeamId}
          onClose={() => setSelectedPaymentPackage(null)}
          onSuccess={() => {
            setSelectedPaymentPackage(null);
          }}
        />
      )}

      {/* Create Project Modal */}
      <CreateProjectModal
        isOpen={isCreateProjectModalOpen}
        onClose={() => setIsCreateProjectModalOpen(false)}
        onProjectCreated={(project) => {
          setIsCreateProjectModalOpen(false);
          setSelectedProject(project);
          navigate(teamPath('/projects/' + project.id));
        }}
      />
    </div>
    </UploadProvider>
    </TaskManagerProvider>
    </ToastProvider>
  );
}
