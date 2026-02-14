import React, { useState } from 'react';
import { Outlet, useLocation, useNavigate } from 'react-router-dom';
import {
  Search, Library, LayoutDashboard, User,
  LayoutList, LayoutGrid, Smartphone, ListTodo,
  ScrollText, Activity, BarChart3,
} from 'lucide-react';
import { useTranslation } from 'react-i18next';
import { ViewState, PointPackage } from '../types';
import { useAuth } from '../contexts/AuthContext';
import { useTeamContext } from '../contexts/TeamContext';
import { useNavigation } from '../hooks/useNavigation';
import { useLibrary } from '../hooks/useLibrary';
import { useParser } from '../hooks/useParser';
import { Sidebar } from './Sidebar';
import { TopBar } from './TopBar';
import { ToastProvider } from './Toast';
import { UserProfileModal } from './UserProfileModal';
import { CreateTeamModal } from './CreateTeamModal';
import { PointsConfirmDialog } from './PointsConfirmDialog';
import { SettingsModal } from './SettingsModal';
import { CreateCollectionModal } from './CreateCollectionModal';
import { CreateProjectModal } from './CreateProjectModal';
import { PaymentModal } from './PaymentModal';

// ---------------------------------------------------------------------------
// Map URL pathname → ViewState for backward compat with Sidebar highlight
// ---------------------------------------------------------------------------

export function pathnameToView(pathname: string): ViewState {
  if (pathname.startsWith('/library')) return 'library';
  if (pathname.startsWith('/dashboard')) return 'dashboard';
  if (pathname.startsWith('/settings')) return 'settings';
  if (pathname.startsWith('/cleanup')) return 'cleanup';
  if (pathname.startsWith('/projects')) return 'mediatrack';
  if (pathname.startsWith('/points')) return 'points';
  if (pathname.startsWith('/billing')) return 'billing';
  if (pathname.startsWith('/members')) return 'members';
  if (pathname.startsWith('/resources')) return 'resources';
  if (pathname.startsWith('/todolist')) return 'todolist';
  return 'parser';
}

// ---------------------------------------------------------------------------
// AppLayout
// ---------------------------------------------------------------------------

export function AppLayout() {
  const { t } = useTranslation();
  const location = useLocation();
  const navigate = useNavigate();

  const {
    currentUserId, userProfile, userSettings, aiSettings,
    isProfileModalOpen, setIsProfileModalOpen, setUserProfile, setAISettings,
    handleLogout, handleUpdateSettings,
  } = useAuth();

  const {
    teams, selectedTeamId, setSelectedTeamId, notifications,
    currentTeam, userPermissions, isCreateTeamModalOpen, setIsCreateTeamModalOpen,
    isSettingsModalOpen, setIsSettingsModalOpen, settingsModalInitialTab, setSettingsModalInitialTab,
    handleCreateTeam, handleTeamCreated, handleTeamUpdated,
    handleMarkNotificationRead, handleMarkAllNotificationsRead,
  } = useTeamContext();

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
  } = useNavigation({ isAuthenticated: true, selectedTeamId });

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

  // Parser state (for PointsConfirmDialog pendingAction)
  const [currentResult, setCurrentResult] = useState<import('../types').Video | null>(null);

  const {
    pendingAction, setPendingAction,
  } = useParser({
    loadLibraryData,
    setLibrary,
    currentResult,
    setCurrentResult,
    isAuthenticated: true,
  });

  const [selectedPaymentPackage, setSelectedPaymentPackage] = useState<PointPackage | null>(null);

  const handleAuthLogout = async () => {
    await handleLogout();
    navigate('/login');
  };

  // Main content padding
  const mainContentClass = `flex-1 md:ml-64 w-full ${
    view === 'library'
      ? 'p-0 md:p-8 md:pt-20 pb-20 md:pb-8'
      : 'p-4 md:p-8 md:pt-20 pb-24 md:pb-8'
  }`;

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
    navigate(viewToPath[targetView] || '/parser');
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
      navigate('/library');
      setSelectedLibraryItem(null);
      setIsMobileMenuOpen(false);
    }
  };

  const handleMobileDashboardClick = () => {
    if (view === 'dashboard') {
      setIsDashboardMenuOpen(!isDashboardMenuOpen);
    } else {
      navigate('/dashboard');
      setIsDashboardMenuOpen(false);
    }
    setIsMobileMenuOpen(false);
  };

  return (
    <ToastProvider>
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

      {/* Points Confirmation Dialog */}
      <PointsConfirmDialog
        isOpen={pendingAction !== null}
        actionType={pendingAction?.type ?? 'video_parse'}
        actionCount={pendingAction?.count ?? 1}
        onConfirm={() => {
          const cb = pendingAction?.callback;
          setPendingAction(null);
          cb?.();
        }}
        onClose={() => setPendingAction(null)}
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
      />

      {/* Create Collection Modal */}
      <CreateCollectionModal
        isOpen={isCreateCollectionModalOpen}
        onClose={() => setIsCreateCollectionModalOpen(false)}
        onSubmit={handleCreateCollection}
        teams={teams}
        defaultTeamId={activeLibraryTab === 'team-library' ? selectedTeamId : null}
      />

      {/* Mobile Nav */}
      <div className="md:hidden fixed bottom-0 left-0 right-0 bg-zinc-950/90 backdrop-blur-xl border-t border-zinc-800 flex justify-around p-4 z-40 pb-6">
        {/* Parser */}
        <button
          onClick={() => handleMobileNavClick('parser')}
          className={`flex flex-col items-center gap-1 transition-colors ${view === 'parser' ? 'text-indigo-400' : 'text-zinc-500 hover:text-zinc-300'}`}
        >
          <Search size={24}/>
        </button>

        {/* Library */}
        <div className="relative">
          <button
            onClick={handleMobileLibraryClick}
            className={`flex flex-col items-center gap-1 transition-colors relative ${view === 'library' ? 'text-indigo-400' : 'text-zinc-500 hover:text-zinc-300'}`}
          >
            <Library size={24}/>
            {view === 'library' && isMobileMenuOpen && (
               <span className="absolute -top-1 -right-1 w-2 h-2 bg-indigo-500 rounded-full animate-pulse"></span>
            )}
          </button>

          {isMobileMenuOpen && view === 'library' && !selectedLibraryItem && (
             <div className="absolute bottom-full left-1/2 -translate-x-1/2 mb-3 bg-zinc-800/95 backdrop-blur-md border border-zinc-700 p-1.5 rounded-xl shadow-2xl flex gap-1 z-50 animate-in slide-in-from-bottom-2 fade-in duration-200">
                <button
                  onClick={() => { setLibraryViewMode('list'); setIsMobileMenuOpen(false); }}
                  className={`p-2.5 rounded-lg transition-all ${libraryViewMode === 'list' ? 'bg-zinc-200 text-zinc-900 shadow-sm' : 'text-zinc-400 hover:text-zinc-200 hover:bg-zinc-700/50'}`}
                  title="Table View"
                >
                  <LayoutList size={20} />
                </button>
                <button
                  onClick={() => { setLibraryViewMode('grid'); setIsMobileMenuOpen(false); }}
                  className={`p-2.5 rounded-lg transition-all ${libraryViewMode === 'grid' ? 'bg-zinc-200 text-zinc-900 shadow-sm' : 'text-zinc-400 hover:text-zinc-200 hover:bg-zinc-700/50'}`}
                  title="Grid View"
                >
                  <LayoutGrid size={20} />
                </button>
                <button
                  onClick={() => { setLibraryViewMode('feed'); setIsMobileMenuOpen(false); }}
                  className={`p-2.5 rounded-lg transition-all ${libraryViewMode === 'feed' ? 'bg-zinc-200 text-zinc-900 shadow-sm' : 'text-zinc-400 hover:text-zinc-200 hover:bg-zinc-700/50'}`}
                  title="Feed View"
                >
                  <Smartphone size={20} />
                </button>
                <div className="absolute -bottom-1.5 left-1/2 -translate-x-1/2 w-3 h-3 bg-zinc-800 border-r border-b border-zinc-700 rotate-45 transform"></div>
             </div>
          )}
        </div>

        {/* Dashboard */}
        <div className="relative">
          <button
            onClick={handleMobileDashboardClick}
            className={`flex flex-col items-center gap-1 transition-colors relative ${view === 'dashboard' ? 'text-indigo-400' : 'text-zinc-500 hover:text-zinc-300'}`}
          >
            <LayoutDashboard size={24}/>
            {view === 'dashboard' && isDashboardMenuOpen && (
              <span className="absolute -top-1 -right-1 w-2 h-2 bg-indigo-500 rounded-full animate-pulse"></span>
            )}
          </button>

          {isDashboardMenuOpen && view === 'dashboard' && (
            <div className="absolute bottom-full left-1/2 -translate-x-1/2 mb-3 bg-zinc-800/95 backdrop-blur-md border border-zinc-700 p-1.5 rounded-xl shadow-2xl flex gap-1 z-50 animate-in slide-in-from-bottom-2 fade-in duration-200">
              <button
                onClick={() => { setDashboardSubView('overview'); setIsDashboardMenuOpen(false); }}
                className={`p-2.5 rounded-lg transition-all ${dashboardSubView === 'overview' ? 'bg-zinc-200 text-zinc-900 shadow-sm' : 'text-zinc-400 hover:text-zinc-200 hover:bg-zinc-700/50'}`}
                title="Overview"
              >
                <BarChart3 size={20} />
              </button>
              <button
                onClick={() => { setDashboardSubView('tasks'); setIsDashboardMenuOpen(false); }}
                className={`p-2.5 rounded-lg transition-all ${dashboardSubView === 'tasks' ? 'bg-zinc-200 text-zinc-900 shadow-sm' : 'text-zinc-400 hover:text-zinc-200 hover:bg-zinc-700/50'}`}
                title="Tasks"
              >
                <ListTodo size={20} />
              </button>
              <button
                onClick={() => { setDashboardSubView('logs'); setIsDashboardMenuOpen(false); }}
                className={`p-2.5 rounded-lg transition-all ${dashboardSubView === 'logs' ? 'bg-zinc-200 text-zinc-900 shadow-sm' : 'text-zinc-400 hover:text-zinc-200 hover:bg-zinc-700/50'}`}
                title="Logs"
              >
                <ScrollText size={20} />
              </button>
              {userProfile.role === 'admin' && (
              <button
                onClick={() => { setDashboardSubView('monitor'); setIsDashboardMenuOpen(false); }}
                className={`p-2.5 rounded-lg transition-all ${dashboardSubView === 'monitor' ? 'bg-zinc-200 text-zinc-900 shadow-sm' : 'text-zinc-400 hover:text-zinc-200 hover:bg-zinc-700/50'}`}
                title="Monitor"
              >
                <Activity size={20} />
              </button>
              )}
              <div className="absolute -bottom-1.5 left-1/2 -translate-x-1/2 w-3 h-3 bg-zinc-800 border-r border-b border-zinc-700 rotate-45 transform"></div>
            </div>
          )}
        </div>

        {/* User Profile */}
        <button
          onClick={() => setIsProfileModalOpen(true)}
          className="flex flex-col items-center gap-1 transition-colors text-zinc-500 hover:text-zinc-300"
        >
          {userProfile.avatarUrl ? (
             <div className="w-6 h-6 rounded-full overflow-hidden border border-zinc-600">
                <img src={userProfile.avatarUrl} alt="Profile" className="w-full h-full object-cover" />
             </div>
          ) : (
             <User size={24} />
          )}
        </button>
      </div>

      {/* Sidebar */}
      <Sidebar
        mode={sidebarMode}
        view={view}
        settingsTab={settingsTab}
        teams={teams}
        activeTeamId={selectedTeamId}
        currentTeam={currentTeam}
        permissions={userPermissions}
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
          setSelectedTeamId(teamId);
          setActiveCollectionId(null);
          setSelectedProject(null);
          setReviewFile(null);
          if (!teamId) navigate('/parser');
          else navigate('/resources');
        }}
        onCreateTeam={handleCreateTeam}
        onProjectBack={() => { setSelectedProject(null); setReviewFile(null); }}
        onSmartCollectionSelect={(collection) => {
          setIsSearchActive(false);
          setSearchResults([]);
          setSearchQueryText('');
          setActiveSmartCollectionId(collection?.id || null);
          navigate('/library');
          setActiveCollectionId(null);
          setActiveLibraryTab('my-library');
        }}
        onProjectSelect={setSelectedProject}
      />

      {/* Main Content */}
      <main className={mainContentClass}>
        {/* TopBar */}
        <TopBar
          user={userProfile ? { name: userProfile.name, email: userProfile.email, avatarUrl: userProfile.avatarUrl } : null}
          unreadCount={notifications.filter(n => !n.read).length}
          onSignOut={handleAuthLogout}
          onOpenSettings={(tab) => {
            setSettingsModalInitialTab(tab || 'personal');
            setIsSettingsModalOpen(true);
          }}
        />

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
          navigate('/projects/' + project.id);
        }}
      />
    </div>
    </ToastProvider>
  );
}
