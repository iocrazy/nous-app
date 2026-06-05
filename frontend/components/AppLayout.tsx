import React, { useState, useEffect } from 'react';
import { Outlet, useLocation, useNavigate, useParams } from 'react-router-dom';
import {
  Search, Library as LibraryIcon, User, FolderOpen, Download, Check,
  LayoutList, LayoutGrid, Smartphone, Trash2, Share2, Zap, BookOpen,
} from 'lucide-react';
import { useTranslation } from 'react-i18next';
import { ViewState, PointPackage, Library, SmartCollection } from '../types';
import { VIEW_PATH_MAP, pathnameToView } from '../utils/routeConfig';
import { fetchLibraries } from '../services/libraryService';
import { fetchSmartFolders } from '../services/resourceService';
import { useAuth } from '../contexts/AuthContext';
import { useTeamContext } from '../contexts/TeamContext';
import { useNavigation } from '../hooks/useNavigation';
import { LibraryProvider, useLibraryContext } from '../contexts/LibraryContext';
import { Sidebar } from './Sidebar';
import { TopBar } from './TopBar';
import { ToastProvider } from './Toast';
import { ConfirmProvider } from './ConfirmDialog';
import { UploadProvider } from '../contexts/UploadContext';
import { TaskManagerProvider } from '../contexts/TaskManagerContext';
import { UserProfileModal } from './UserProfileModal';
import { MobileProfilePage } from './MobileProfilePage';
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
    teams, teamsLoading, personalTeamId, selectedTeamId, setSelectedTeamId, notifications,
    currentTeam, userPermissions, isModuleEnabled, isViewEnabled,
    isCreateTeamModalOpen, setIsCreateTeamModalOpen,
    isSettingsModalOpen, setIsSettingsModalOpen, settingsModalInitialTab, setSettingsModalInitialTab,
    handleCreateTeam, handleTeamCreated, handleTeamUpdated, handleTeamDeleted, handleTeamLeft,
    handleMarkNotificationRead, handleMarkAllNotificationsRead,
  } = useTeamContext();

  // Sync teamId from URL to context.
  // Once teams have loaded, a URL team id that isn't one of the user's teams
  // (e.g. a stale bookmark from before the snowflake id migration) must NOT be
  // adopted — doing so leaves currentTeam=null and the whole app renders blank.
  // Redirect such URLs to a valid team (personal, or the first available).
  //
  // IMPORTANT: the personal team is NOT in `teams` — fetchMyTeams excludes it
  // (.neq kind personal). A valid id is therefore (teams ∪ personalTeamId),
  // otherwise navigating to the personal workspace would be wrongly rejected
  // and loop-redirect forever.
  useEffect(() => {
    if (!urlTeamId) return;
    const isValidTeam = teams.some(t => t.id === urlTeamId) || urlTeamId === personalTeamId;
    if (!teamsLoading && (teams.length > 0 || personalTeamId) && !isValidTeam) {
      const fallback = personalTeamId || (teams.length > 0 ? teams[0].id : null);
      if (!fallback) return; // nothing valid to redirect to yet
      console.warn(
        `[AppLayout] URL team ${urlTeamId} is not one of the user's teams; ` +
          `redirecting to ${fallback}`,
      );
      const rest = location.pathname.replace(/^\/team\/[^/]+/, '');
      navigate(`/team/${fallback}${rest}`, { replace: true });
      return;
    }
    if (urlTeamId !== selectedTeamId) {
      setSelectedTeamId(urlTeamId);
    }
  }, [urlTeamId, selectedTeamId, setSelectedTeamId, teams, teamsLoading, personalTeamId, location.pathname, navigate]);

  const view = pathnameToView(location.pathname);
  const isDownloadsRoute = location.pathname.includes('/resources/downloads');
  const isDetailPage = location.pathname.includes('/resources/file/') || location.pathname.includes('/player/');

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
  const [isMobileProfileOpen, setIsMobileProfileOpen] = useState(false);
  const [isDownloadsMenuOpen, setIsDownloadsMenuOpen] = useState(false);
  const [mobileLibraries, setMobileLibraries] = useState<Library[]>([]);
  const [mobileSmartFolders, setMobileSmartFolders] = useState<SmartCollection[]>([]);

  // Mobile workspace switch — navigates to new team, respecting current view
  const handleWorkspaceSwitch = (newTeamId: string) => {
    // Eagerly update team context so data loads immediately
    setSelectedTeamId(newTeamId);
    if (view === 'resources' && location.pathname.includes('/downloads')) {
      navigate(`/team/${newTeamId}/resources`);
    } else {
      navigate(`/team/${newTeamId}/${VIEW_PATH_MAP[view].slice(1)}`);
    }
    setIsMobileProfileOpen(false);
  };

  // Load libraries + smart folders for mobile Resources popup.
  // After Spec 1 PR-C, scope_id is the personal-team snowflake (not the
  // user UUID), so personal mode passes selectedTeamId (which equals
  // personalTeamId in this branch) rather than currentUserId.
  useEffect(() => {
    if (!isResourcesMenuOpen || !selectedTeamId) return;
    fetchLibraries(selectedTeamId).then(setMobileLibraries).catch(() => setMobileLibraries([]));
    fetchSmartFolders(selectedTeamId).then(setMobileSmartFolders).catch(() => setMobileSmartFolders([]));
  }, [isResourcesMenuOpen, selectedTeamId, personalTeamId]);

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
  // Resources view: desktop uses h-screen + overflow-hidden for Shell layout;
  // mobile uses natural scroll (no h-screen) so infinite scroll works
  const mainContentClass = `flex-1 ${sidebarCollapsed ? 'sm:ml-20' : 'sm:ml-64'} w-full transition-[margin] duration-300 ${
    view === 'resources'
      ? `${isDetailPage ? 'pt-0 pb-0' : 'pt-[calc(env(safe-area-inset-top,0px)+48px)] pb-28'} sm:h-screen sm:p-8 sm:pt-20 sm:pb-8 sm:overflow-hidden`
      : `${isDetailPage ? 'pt-0 pb-0' : 'px-4 pt-[calc(env(safe-area-inset-top,0px)+48px)] pb-28'} sm:px-8 sm:pt-20 sm:pb-8`
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
    <ConfirmProvider>
    <TaskManagerProvider>
    <UploadProvider>
    <div className="flex min-h-[100dvh] bg-black text-zinc-100 font-sans selection:bg-indigo-500/30 overflow-x-hidden">

      {/* User Profile Modal */}
      <UserProfileModal
        isOpen={isProfileModalOpen}
        onClose={() => setIsProfileModalOpen(false)}
        user={userProfile}
        onSave={(updated) => setUserProfile(updated)}
        onLogout={handleAuthLogout}
      />

      {/* Mobile Profile Page — full-screen overlay, Me tab */}
      <MobileProfilePage
        isOpen={isMobileProfileOpen}
        onClose={() => setIsMobileProfileOpen(false)}
        userProfile={userProfile}
        teams={teams}
        personalTeamId={personalTeamId}
        selectedTeamId={selectedTeamId}
        currentView={view}
        onSwitchTeam={handleWorkspaceSwitch}
        onSettings={() => {
          setSettingsModalInitialTab('personal');
          setIsSettingsModalOpen(true);
        }}
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

      {/* Mobile Tab Bar — hidden on detail pages for immersive experience */}
      <div className={`sm:hidden fixed bottom-0 left-0 right-0 z-40 ${isDetailPage ? 'hidden' : ''}`}>
        {/* Backdrop to dismiss popups */}
        {(isMobileMenuOpen || isResourcesMenuOpen || isDownloadsMenuOpen) && (
          <div
            className="fixed inset-0 z-30"
            onClick={() => { setIsMobileMenuOpen(false); setIsResourcesMenuOpen(false); setIsDownloadsMenuOpen(false); }}
          />
        )}

        {/* Figma-style floating pill tab bar */}
        <div className="flex justify-center pb-[calc(env(safe-area-inset-bottom,6px)+6px)] relative z-40">
          <div className="bg-zinc-900/95 backdrop-blur-xl border border-zinc-800/60 rounded-full flex items-center px-2 py-1.5 gap-0.5 shadow-2xl">
            {/* Parser */}
            <button
              onClick={() => handleMobileNavClick('parser')}
              className={`flex flex-col items-center gap-0.5 px-4 py-1.5 rounded-full transition-colors ${
                view === 'parser' ? 'bg-zinc-800 text-indigo-400' : 'text-zinc-500'
              }`}
            >
              <Search size={20} />
              <span className="text-[9px] leading-tight font-medium">Parser</span>
            </button>

            {/* Downloads — personal workspace only */}
            {isPersonalWorkspace && (
              <div className="relative">
                {isDownloadsMenuOpen && (
                  <div className="absolute bottom-full left-1/2 -translate-x-1/2 mb-3 bg-zinc-900/98 backdrop-blur-xl border border-zinc-700/60 rounded-2xl shadow-2xl z-50 animate-in slide-in-from-bottom-2 fade-in duration-200 overflow-hidden">
                    <div className="flex p-1.5 gap-1">
                      <button onClick={() => { setLibraryViewMode('grid'); setIsDownloadsMenuOpen(false); }} className={`p-2 rounded-lg transition-colors ${libraryViewMode === 'grid' ? 'bg-zinc-800 text-white' : 'text-zinc-500 hover:text-zinc-300'}`}><LayoutGrid size={18} /></button>
                      <button onClick={() => { setLibraryViewMode('list'); setIsDownloadsMenuOpen(false); }} className={`p-2 rounded-lg transition-colors ${libraryViewMode === 'list' ? 'bg-zinc-800 text-white' : 'text-zinc-500 hover:text-zinc-300'}`}><LayoutList size={18} /></button>
                      <button onClick={() => { setLibraryViewMode('feed'); setIsDownloadsMenuOpen(false); }} className={`p-2 rounded-lg transition-colors ${libraryViewMode === 'feed' ? 'bg-zinc-800 text-white' : 'text-zinc-500 hover:text-zinc-300'}`}><Smartphone size={18} /></button>
                    </div>
                  </div>
                )}
                <button
                  onClick={() => {
                    if (isDownloadsRoute) {
                      setIsDownloadsMenuOpen(!isDownloadsMenuOpen);
                    } else {
                      handleMobileLibraryClick();
                      setIsDownloadsMenuOpen(false);
                    }
                  }}
                  className={`flex flex-col items-center gap-0.5 px-4 py-1.5 rounded-full transition-colors ${
                    isDownloadsRoute ? 'bg-zinc-800 text-indigo-400' : 'text-zinc-500'
                  }`}
                >
                  <Download size={20} />
                  <span className="text-[9px] leading-tight font-medium">Downloads</span>
                </button>
              </div>
            )}

            {/* Resources — full sidebar navigation (replaces desktop sidebar on mobile) */}
            <div className="relative">
              {isResourcesMenuOpen && (
                <div className="absolute bottom-full right-0 mb-3 w-56 bg-zinc-900/98 backdrop-blur-xl border border-zinc-700/60 rounded-2xl shadow-2xl z-50 animate-in slide-in-from-bottom-2 fade-in duration-200 overflow-hidden max-h-[60vh] overflow-y-auto">
                  {/* Main views */}
                  <div className="py-1.5">
                    <button
                      onClick={(e) => { e.stopPropagation(); navigate(teamPath('/resources')); setIsResourcesMenuOpen(false); }}
                      className={`w-full text-left px-3.5 py-2 transition-colors flex items-center gap-2.5 ${
                        view === 'resources' && !isDownloadsRoute && !location.pathname.includes('/shared') && !location.pathname.includes('/recycle') && !location.pathname.includes('/library/') && !location.pathname.includes('/smart/') ? 'bg-indigo-500/10 text-indigo-300' : 'text-zinc-300 hover:bg-zinc-800/60'
                      }`}
                    >
                      <FolderOpen size={16} className="shrink-0" />
                      <span className="text-sm">My Uploads</span>
                    </button>
                    <button
                      onClick={(e) => { e.stopPropagation(); navigate(teamPath('/resources/shared')); setIsResourcesMenuOpen(false); }}
                      className={`w-full text-left px-3.5 py-2 transition-colors flex items-center gap-2.5 ${
                        location.pathname.includes('/shared') ? 'bg-indigo-500/10 text-indigo-300' : 'text-zinc-300 hover:bg-zinc-800/60'
                      }`}
                    >
                      <Share2 size={16} className="shrink-0" />
                      <span className="text-sm">Shared</span>
                    </button>
                    <button
                      onClick={(e) => { e.stopPropagation(); navigate(teamPath('/resources/recycle')); setIsResourcesMenuOpen(false); }}
                      className={`w-full text-left px-3.5 py-2 transition-colors flex items-center gap-2.5 ${
                        location.pathname.includes('/recycle') ? 'bg-indigo-500/10 text-indigo-300' : 'text-zinc-300 hover:bg-zinc-800/60'
                      }`}
                    >
                      <Trash2 size={16} className="shrink-0" />
                      <span className="text-sm">Recycle Bin</span>
                    </button>
                  </div>

                  {/* Libraries (team workspace) */}
                  {mobileLibraries.length > 0 && (
                    <>
                      <div className="h-px bg-zinc-800/60 mx-3" />
                      <div className="py-1.5">
                        <div className="px-3.5 py-1.5">
                          <span className="text-[10px] font-semibold text-zinc-500 uppercase tracking-wider">Libraries</span>
                        </div>
                        {mobileLibraries.map(lib => (
                          <button
                            key={lib.id}
                            onClick={(e) => { e.stopPropagation(); navigate(teamPath(`/resources/library/${lib.id}`)); setIsResourcesMenuOpen(false); }}
                            className={`w-full text-left px-3.5 py-2 transition-colors flex items-center gap-2.5 ${
                              location.pathname.includes(`/library/${lib.id}`) ? 'bg-indigo-500/10 text-indigo-300' : 'text-zinc-300 hover:bg-zinc-800/60'
                            }`}
                          >
                            <BookOpen size={16} className="shrink-0" />
                            <span className="text-sm truncate">{lib.name}</span>
                          </button>
                        ))}
                      </div>
                    </>
                  )}

                  {/* Smart Folders */}
                  {mobileSmartFolders.length > 0 && (
                    <>
                      <div className="h-px bg-zinc-800/60 mx-3" />
                      <div className="py-1.5">
                        <div className="px-3.5 py-1.5">
                          <span className="text-[10px] font-semibold text-zinc-500 uppercase tracking-wider">Smart Folders</span>
                        </div>
                        {mobileSmartFolders.map(sf => (
                          <button
                            key={sf.id}
                            onClick={(e) => { e.stopPropagation(); navigate(teamPath(`/resources/smart/${sf.id}`)); setIsResourcesMenuOpen(false); }}
                            className={`w-full text-left px-3.5 py-2 transition-colors flex items-center gap-2.5 ${
                              location.pathname.includes(`/smart/${sf.id}`) ? 'bg-indigo-500/10 text-indigo-300' : 'text-zinc-300 hover:bg-zinc-800/60'
                            }`}
                          >
                            <Zap size={16} className="shrink-0" />
                            <span className="text-sm truncate">{sf.name}</span>
                          </button>
                        ))}
                      </div>
                    </>
                  )}
                </div>
              )}
              <button
                onClick={() => {
                  setIsMobileMenuOpen(false);
                  if (view === 'resources' && !isDownloadsRoute) {
                    setIsResourcesMenuOpen(!isResourcesMenuOpen);
                  } else {
                    navigate(teamPath('/resources'));
                    setIsResourcesMenuOpen(false);
                  }
                }}
                className={`flex flex-col items-center gap-0.5 px-4 py-1.5 rounded-full transition-colors ${
                  view === 'resources' && !isDownloadsRoute ? 'bg-zinc-800 text-indigo-400' : 'text-zinc-500'
                }`}
              >
                <FolderOpen size={20} />
                <span className="text-[9px] leading-tight font-medium">Resources</span>
              </button>
            </div>
          </div>
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
        userRole={userProfile?.role}
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

      {/* Mobile workspace avatar — hidden on detail pages */}
      <button
        className={`sm:hidden fixed top-[calc(env(safe-area-inset-top,0px)+10px)] left-3 z-[31] w-9 h-9 rounded-full transition-all active:scale-95 ${isDetailPage ? 'hidden' : ''}`}
        onClick={() => setIsMobileProfileOpen(true)}
      >
        <div className={`w-full h-full rounded-full flex items-center justify-center text-sm font-bold shadow-lg ring-2 ring-offset-2 ring-offset-zinc-950 ${
          selectedTeamId === personalTeamId
            ? 'bg-indigo-600 text-white ring-indigo-500/50'
            : 'bg-emerald-600 text-white ring-emerald-500/50'
        }`}>
          {selectedTeamId === personalTeamId
            ? (userProfile?.name?.charAt(0)?.toUpperCase() || 'P')
            : (currentTeam?.name?.charAt(0)?.toUpperCase() || 'T')}
        </div>
      </button>

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
    </ConfirmProvider>
    </ToastProvider>
  );
}
