import React, { useState, useEffect } from 'react';
import { Outlet, useLocation, useNavigate, useParams } from 'react-router-dom';
import {
  Search, Library, User, FolderOpen, Download, Check,
  LayoutList, LayoutGrid, Smartphone,
} from 'lucide-react';
import { useTranslation } from 'react-i18next';
import { ViewState, PointPackage } from '../types';
import { VIEW_PATH_MAP, pathnameToView } from '../utils/routeConfig';
import { useAuth } from '../contexts/AuthContext';
import { useTeamContext } from '../contexts/TeamContext';
import { useNavigation } from '../hooks/useNavigation';
import { LibraryProvider, useLibraryContext } from '../contexts/LibraryContext';
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
// AppLayout
// ---------------------------------------------------------------------------

export function AppLayout() {
  return (
    <LibraryProvider>
      <AppLayoutInner />
    </LibraryProvider>
  );
}

function AppLayoutInner() {
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

  const isPersonalWorkspace = !selectedTeamId || selectedTeamId === personalTeamId;

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
    loadLibraryData, handleCreateCollection,
  } = useLibraryContext();

  const [selectedPaymentPackage, setSelectedPaymentPackage] = useState<PointPackage | null>(null);
  const [isResourcesMenuOpen, setIsResourcesMenuOpen] = useState(false);

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
    view === 'resources'
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
    navigate(teamPath(VIEW_PATH_MAP[targetView]));
    setIsMobileMenuOpen(false);
    setIsDashboardMenuOpen(false);
  };

  const handleMobileLibraryClick = () => {
    setIsDashboardMenuOpen(false);
    const pid = personalTeamId || selectedTeamId;
    navigate(`/team/${pid}/resources/downloads`);
    setIsMobileMenuOpen(false);
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

      {/* Mobile Tab Bar — 4 tabs: Parser, Downloads, Resources, Me */}
      <div className="sm:hidden fixed bottom-0 left-0 right-0 z-40">
        {/* Backdrop to dismiss popups */}
        {(isMobileMenuOpen || isResourcesMenuOpen) && (
          <div
            className="fixed inset-0 z-30"
            onClick={() => { setIsMobileMenuOpen(false); setIsResourcesMenuOpen(false); }}
          />
        )}

        {/* Tab buttons */}
        <div className="bg-zinc-950/95 backdrop-blur-xl border-t border-zinc-800/60 flex justify-around px-2 pt-2 pb-[calc(env(safe-area-inset-bottom,8px)+4px)] relative z-40">
          {/* Parser */}
          <button
            onClick={() => handleMobileNavClick('parser')}
            className={`flex flex-col items-center gap-0.5 min-w-0 px-3 py-1 transition-colors ${view === 'parser' ? 'text-indigo-400' : 'text-zinc-500'}`}
          >
            <Search size={22} />
            <span className="text-[10px] leading-tight">Parser</span>
          </button>

          {/* Downloads — only visible in personal workspace */}
          {isPersonalWorkspace && (
            <button
              onClick={handleMobileLibraryClick}
              className="flex flex-col items-center gap-0.5 min-w-0 px-3 py-1 transition-colors text-zinc-500"
            >
              <Download size={22} />
              <span className="text-[10px] leading-tight">Downloads</span>
            </button>
          )}

          {/* Resources — with team picker popup */}
          <div className="relative">
            {isResourcesMenuOpen && (
              <div className="absolute bottom-full left-1/2 -translate-x-1/2 mb-3 w-52 bg-zinc-900/98 backdrop-blur-xl border border-zinc-700/60 rounded-2xl shadow-2xl z-50 animate-in slide-in-from-bottom-2 fade-in duration-200 overflow-hidden">
                <div className="px-4 pt-3 pb-2 border-b border-zinc-800/60">
                  <span className="text-[10px] font-semibold text-zinc-500 uppercase tracking-wider">Workspace</span>
                </div>
                <div className="py-1 max-h-60 overflow-y-auto">
                  {personalTeamId && (
                    <button
                      onClick={() => { navigate(`/team/${personalTeamId}/resources`); setIsResourcesMenuOpen(false); }}
                      className={`w-full text-left px-3 py-2.5 transition-colors flex items-center gap-2.5 ${
                        selectedTeamId === personalTeamId ? 'bg-indigo-500/10' : 'hover:bg-zinc-800/60'
                      }`}
                    >
                      <div className={`w-7 h-7 rounded-lg flex items-center justify-center text-xs font-bold ${
                        selectedTeamId === personalTeamId ? 'bg-indigo-500/30 text-indigo-300' : 'bg-zinc-700 text-zinc-400'
                      }`}>
                        {userProfile?.name?.charAt(0)?.toUpperCase() || 'P'}
                      </div>
                      <span className={`text-sm flex-1 ${selectedTeamId === personalTeamId ? 'text-indigo-300 font-medium' : 'text-zinc-300'}`}>Personal</span>
                      {selectedTeamId === personalTeamId && <Check size={14} className="text-indigo-400" />}
                    </button>
                  )}
                  {teams.filter(t => String(t.id) !== personalTeamId).map(team => {
                    const isActive = selectedTeamId === String(team.id);
                    return (
                      <button
                        key={team.id}
                        onClick={() => { navigate(`/team/${team.id}/resources`); setIsResourcesMenuOpen(false); }}
                        className={`w-full text-left px-3 py-2.5 transition-colors flex items-center gap-2.5 ${
                          isActive ? 'bg-indigo-500/10' : 'hover:bg-zinc-800/60'
                        }`}
                      >
                        <div className={`w-7 h-7 rounded-lg flex items-center justify-center text-xs font-bold ${
                          isActive ? 'bg-indigo-500/30 text-indigo-300' : 'bg-zinc-700 text-zinc-400'
                        }`}>
                          {team.name.charAt(0).toUpperCase()}
                        </div>
                        <span className={`text-sm flex-1 ${isActive ? 'text-indigo-300 font-medium' : 'text-zinc-300'}`}>{team.name}</span>
                        {isActive && <Check size={14} className="text-indigo-400" />}
                      </button>
                    );
                  })}
                </div>
              </div>
            )}
            <button
              onClick={() => {
                setIsMobileMenuOpen(false);
                if (view === 'resources') {
                  setIsResourcesMenuOpen(!isResourcesMenuOpen);
                } else {
                  navigate(teamPath('/resources'));
                  setIsResourcesMenuOpen(false);
                }
              }}
              className={`flex flex-col items-center gap-0.5 min-w-0 px-3 py-1 transition-colors ${view === 'resources' ? 'text-indigo-400' : 'text-zinc-500'}`}
            >
              <FolderOpen size={22} />
              <span className="text-[10px] leading-tight">Resources</span>
            </button>
          </div>

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
          navigate(teamPath('/resources/downloads'));
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
