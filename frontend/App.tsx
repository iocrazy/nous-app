
import React, { useState, useEffect } from 'react';
import {
  LayoutDashboard, Search, Library, Settings,
  Link as LinkIcon, AlertCircle, Loader2, User,
  LayoutGrid, LayoutList, Folder, Smartphone, X,
  CloudOff, RefreshCw, Activity, CheckCircle2,
  ListVideo, HardDrive, ArrowLeft, Check, Music, Video as VideoIcon, Image as ImageIcon,
  Layers, Download, ScrollText, ListTodo, BarChart3,
} from 'lucide-react';
import { useTranslation } from 'react-i18next';
import { Video, ViewState, PointPackage } from './types';
import { MediaCard } from './components/MediaCard';
import { CompactMediaCard } from './components/CompactMediaCard';
import { StatsChart } from './components/StatsChart';
import { LibraryTable } from './components/LibraryTable';
import { LibraryFeed } from './components/LibraryFeed';
import { SettingsView } from './components/SettingsView';
import { UserProfileModal } from './components/UserProfileModal';
import { LandingPage } from './components/LandingPage';
import { AuthOverlay } from './components/AuthOverlay';
import { ParseModeCard } from './components/ParseModeCard';
import { ParserTagSelector } from './components/ParserTagSelector';
import { CreateTeamModal } from './components/CreateTeamModal';
import { PointsConfirmDialog } from './components/PointsConfirmDialog';
import { CreateCollectionModal } from './components/CreateCollectionModal';
import { TasksPanel } from './components/TasksPanel';
import { LogsPanel } from './components/LogsPanel';
import { SystemMonitorPanel } from './components/SystemMonitorPanel';
import { SemanticSearchBar } from './components/SemanticSearchBar';
import { CleanupSuggestionsView } from './components/CleanupSuggestionsView';
import { PointsCenter } from './components/PointsCenter';
import { PaymentModal } from './components/PaymentModal';
import { Sidebar } from './components/Sidebar';
import { SettingsModal } from './components/SettingsModal';
import { VideoDetailPanel } from './components/VideoDetailPanel';
import { getQueueDisplay, getStorageDisplay } from './services/systemService';
import { ToastProvider } from './components/Toast';
import { ProjectsListView } from './components/ProjectsListView';
import { ProjectFilesView } from './components/ProjectFilesView';
import { CreateProjectModal } from './components/CreateProjectModal';
import { VideoReviewPage } from './components/VideoReviewPage';
import { BillingView } from './components/BillingView';
import { MembersView } from './components/MembersView';
import { ResourcesView } from './components/ResourcesView';
import { TopBar } from './components/TopBar';
import { TaskMonitor } from './components/TaskMonitor';
import { AuthProvider, useAuth } from './hooks/useAuth';
import { useTeams } from './hooks/useTeams';
import { useLibrary } from './hooks/useLibrary';
import { useParser } from './hooks/useParser';
import { useNavigation } from './hooks/useNavigation';


function AppInner() {
  const { t } = useTranslation();
  const {
    isAuthenticated, currentUserId, showAuthModal, userProfile, userSettings, aiSettings,
    isProfileModalOpen, setShowAuthModal, setIsProfileModalOpen, setUserProfile, setAISettings,
    handleLogin, handleLogout, handleUpdateSettings,
  } = useAuth();

  const {
    teams, setTeams, selectedTeamId, setSelectedTeamId, notifications, setNotifications,
    currentTeam, userPermissions, isCreateTeamModalOpen, setIsCreateTeamModalOpen,
    isSettingsModalOpen, setIsSettingsModalOpen, settingsModalInitialTab, setSettingsModalInitialTab,
    handleCreateTeam, handleTeamCreated, handleTeamSettings, handleTeamUpdated,
    handleTeamDeleted, handleTeamLeft, handleMarkNotificationRead, handleMarkAllNotificationsRead,
  } = useTeams(isAuthenticated, currentUserId);

  // Parser result state (needed for cross-hook realtime update)
  const [currentResult, setCurrentResult] = useState<Video | null>(null);

  const {
    library, setLibrary, isLoadingLibrary, libraryError, selectedLibraryItem, setSelectedLibraryItem,
    filteredLibrary, hasMoreData, isLoadingMore, loadMoreRef,
    libraryViewMode, setLibraryViewMode, activeLibraryTab, setActiveLibraryTab, isTeamLibraryActive,
    searchQuery, setSearchQuery, searchResults, setSearchResults, isSearchActive, setIsSearchActive,
    searchQueryText, setSearchQueryText,
    collections, setCollections, activeCollectionId, setActiveCollectionId, collectionVideoIds,
    isCreateCollectionModalOpen, setIsCreateCollectionModalOpen, selectedVideoCollectionIds,
    activeSmartCollectionId, setActiveSmartCollectionId, sharedVideoIds,
    loadLibraryData, handleLibraryTabChange, handleCreateCollection, handleToggleVideoCollection,
    handleUpdateLibraryItem, handleDeleteLibraryItem,
  } = useLibrary({
    isAuthenticated,
    selectedTeamId,
    onVideoRealtimeUpdate: (video) => {
      setCurrentResult(prev => prev?.platform_id === video.platform_id ? video : prev);
    },
  });

  const {
    view, setView, settingsTab, setSettingsTab,
    selectedProject, setSelectedProject, reviewFile, setReviewFile,
    isCreateProjectModalOpen, setIsCreateProjectModalOpen,
    isLibraryOpen, isSettingsOpen,
    isMobileMenuOpen, setIsMobileMenuOpen, isMobileSearchOpen, setIsMobileSearchOpen,
    dashboardSubView, setDashboardSubView, isDashboardMenuOpen, setIsDashboardMenuOpen,
    dashboardStats, sidebarMode,
    toggleLibraryMenu, toggleSettingsMenu, hasAutoSelected,
  } = useNavigation({ isAuthenticated, selectedTeamId });

  const {
    urlInput, setUrlInput, parserMode, setParserMode, batchInput, setBatchInput,
    downloadOptions, setDownloadOptions, selectedTagIds, setSelectedTagIds,
    isParsing, taskStatus, taskProgress, socketLogs, systemStatus,
    batchResults, error, pendingAction, setPendingAction,
    downloadTaskId, downloadStatus, downloadPercent, downloadSpeed,
    handleParse, handleSaveToLibrary, handleBatchSave,
  } = useParser({
    loadLibraryData,
    setLibrary,
    currentResult,
    setCurrentResult,
    isAuthenticated,
  });

  const [selectedPaymentPackage, setSelectedPaymentPackage] = useState<PointPackage | null>(null);

  // Auto-select first team only on initial load when no preference was saved
  useEffect(() => {
    if (teams.length > 0 && !selectedTeamId && !hasAutoSelected.current) {
      hasAutoSelected.current = true;
      setSelectedTeamId(teams[0].id);
      setView('resources');
    }
  }, [teams]);

  // Auth logout resets non-auth state
  const handleAuthLogout = async () => {
    await handleLogout();
  };

  const handleMobileLibraryClick = () => {
    setIsDashboardMenuOpen(false);
    if (view === 'library') {
      if (selectedLibraryItem) {
        setSelectedLibraryItem(null); // Go back to list if in detail view
      } else {
        setIsMobileMenuOpen(!isMobileMenuOpen); // Toggle menu if in list view
      }
    } else {
      setView('library');
      setSelectedLibraryItem(null); // Ensure we start at list
      setIsMobileMenuOpen(false); // Reset menu when entering from another view
    }
  };

  const handleMobileDashboardClick = () => {
    if (view === 'dashboard') {
      setIsDashboardMenuOpen(!isDashboardMenuOpen);
    } else {
      setView('dashboard');
      setIsDashboardMenuOpen(false);
    }
    setIsMobileMenuOpen(false);
  };

  const handleMobileNavClick = (targetView: ViewState) => {
    setView(targetView);
    setIsMobileMenuOpen(false);
    setIsDashboardMenuOpen(false);
  };

  // Filter and sort library - 默认按添加时间降序（最新在前）
  // Calculate main content classes based on view to handle mobile padding
  // md:pt-14 accounts for the fixed TopBar (h-14 = 56px)
  const mainContentClass = `flex-1 md:ml-64 w-full ${
    view === 'library'
      ? 'p-0 md:p-8 md:pt-20 pb-20 md:pb-8'
      : 'p-4 md:p-8 md:pt-20 pb-24 md:pb-8'
  }`;

  // --- RENDER LOGIC ---

  // 1. If not authenticated, show Landing Page.
  //    Also handle showing the Auth Overlay if triggered.
  if (!isAuthenticated) {
    return (
      <>
        <LandingPage
          onLoginClick={() => setShowAuthModal(true)}
          onGetStarted={() => setShowAuthModal(true)}
        />
        {showAuthModal && (
          <AuthOverlay 
            onLogin={handleLogin} 
            onClose={() => setShowAuthModal(false)} 
          />
        )}
      </>
    );
  }

  // 2. If authenticated, show Main Dashboard (existing logic)
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
        onUserUpdated={() => {
          // Refresh user profile
        }}
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

        {/* 1. Parser */}
        <button
          onClick={() => handleMobileNavClick('parser')}
          className={`flex flex-col items-center gap-1 transition-colors ${view === 'parser' ? 'text-indigo-400' : 'text-zinc-500 hover:text-zinc-300'}`}
        >
          <Search size={24}/>
        </button>

        {/* 2. Library with popup menu */}
        <div className="relative">
          <button
            onClick={handleMobileLibraryClick}
            className={`flex flex-col items-center gap-1 transition-colors relative ${view === 'library' ? 'text-indigo-400' : 'text-zinc-500 hover:text-zinc-300'}`}
          >
            <Library size={24}/>
            {/* Active Indicator dot */}
            {view === 'library' && isMobileMenuOpen && (
               <span className="absolute -top-1 -right-1 w-2 h-2 bg-indigo-500 rounded-full animate-pulse"></span>
            )}
          </button>

          {/* Mobile Popup Menu for View Selection - positioned above Library button */}
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
                {/* Little triangle arrow pointing down */}
                <div className="absolute -bottom-1.5 left-1/2 -translate-x-1/2 w-3 h-3 bg-zinc-800 border-r border-b border-zinc-700 rotate-45 transform"></div>
             </div>
          )}
        </div>
        
        {/* 3. Dashboard with popup menu */}
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

          {/* Dashboard Sub-view Popup Menu */}
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
              {/* Little triangle arrow pointing down */}
              <div className="absolute -bottom-1.5 left-1/2 -translate-x-1/2 w-3 h-3 bg-zinc-800 border-r border-b border-zinc-700 rotate-45 transform"></div>
            </div>
          )}
        </div>

        {/* 4. User Profile Button (MOVED TO RIGHT) */}
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
          setView(v as ViewState);
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
          if (!teamId) setView('parser');
          else setView('resources');
        }}
        onCreateTeam={handleCreateTeam}
        onProjectBack={() => { setSelectedProject(null); setReviewFile(null); }}
        onSmartCollectionSelect={(collection) => {
          setIsSearchActive(false);
          setSearchResults([]);
          setSearchQueryText('');
          setActiveSmartCollectionId(collection?.id || null);
          setView('library');
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
          onNavigate={(v, tab) => {
            setView(v as ViewState);
            if (tab) setSettingsTab(tab as any);
          }}
          onSignOut={handleAuthLogout}
          onOpenSettings={(tab) => {
            setSettingsModalInitialTab(tab || 'personal');
            setIsSettingsModalOpen(true);
          }}
        />

        {/* VIEW: PARSER */}
        {view === 'parser' && (
          <div className="max-w-3xl mx-auto space-y-8 animate-in fade-in slide-in-from-bottom-4 duration-500">
            <div className="text-center space-y-2 mb-6">
              <h1 className="text-3xl md:text-4xl font-bold bg-clip-text text-transparent bg-gradient-to-r from-indigo-400 to-purple-400">
                {t('parser.title')}
              </h1>
              <p className="text-zinc-400">
                {t('parser.subtitle')}
              </p>
            </div>

            {/* Parsing Mode Toggle */}
            <div className="flex justify-center">
              <div className="bg-zinc-900 p-1 rounded-xl border border-zinc-800 inline-flex">
                <button
                  onClick={() => setParserMode('single')}
                  className={`px-4 py-2 rounded-lg text-sm font-medium transition-all ${
                    parserMode === 'single'
                    ? 'bg-zinc-800 text-white shadow-sm'
                    : 'text-zinc-500 hover:text-zinc-300'
                  }`}
                >
                  <div className="flex items-center gap-2">
                    <LinkIcon size={14} />
                    <span>{t('parser.singleLink')}</span>
                  </div>
                </button>
                <button
                  onClick={() => setParserMode('batch')}
                  className={`px-4 py-2 rounded-lg text-sm font-medium transition-all ${
                    parserMode === 'batch'
                    ? 'bg-zinc-800 text-white shadow-sm'
                    : 'text-zinc-500 hover:text-zinc-300'
                  }`}
                >
                  <div className="flex items-center gap-2">
                    <Layers size={14} />
                    <span>{t('parser.batchDownload')}</span>
                  </div>
                </button>
              </div>
            </div>

            <div className="relative group">
              <div className="absolute -inset-0.5 bg-gradient-to-r from-indigo-500 to-purple-600 rounded-xl opacity-30 group-hover:opacity-60 transition duration-500 blur"></div>
              <div className="relative bg-zinc-900 rounded-xl p-2 border border-zinc-800 shadow-xl">
                
                {parserMode === 'single' ? (
                  <div className="flex items-center">
                    <LinkIcon className="ml-3 text-zinc-500 w-5 h-5 flex-shrink-0" />
                    <input
                      type="text"
                      placeholder={t('parser.placeholder')}
                      className="flex-1 bg-transparent border-none outline-none text-zinc-200 placeholder-zinc-600 px-4 py-3"
                      value={urlInput}
                      onChange={(e) => setUrlInput(e.target.value)}
                      onKeyDown={(e) => e.key === 'Enter' && handleParse()}
                    />
                    <button
                      onClick={handleParse}
                      disabled={isParsing || !urlInput}
                      className="bg-indigo-600 hover:bg-indigo-500 disabled:opacity-50 disabled:cursor-not-allowed text-white px-6 py-2.5 rounded-lg font-medium transition-all shadow-lg shadow-indigo-500/20 flex items-center gap-2 flex-shrink-0"
                    >
                      {isParsing && taskProgress < 100 ? <Loader2 className="animate-spin w-4 h-4" /> : t('parser.analyze')}
                    </button>
                  </div>
                ) : (
                  <div className="flex flex-col">
                    <textarea 
                      placeholder={t('parser.batchPlaceholder')}
                      rows={5}
                      className="w-full bg-transparent border-none outline-none text-zinc-200 placeholder-zinc-600 px-4 py-3 resize-none font-mono text-sm"
                      value={batchInput}
                      onChange={(e) => setBatchInput(e.target.value)}
                    />
                    <div className="flex justify-between items-center px-2 py-2 border-t border-zinc-800">
                      <span className="text-xs text-zinc-500 ml-2">
                         {batchInput.split(/\r?\n/).filter(l => l.trim().length > 0).length} links detected
                      </span>
                      <button
                        onClick={handleParse}
                        disabled={isParsing || !batchInput}
                        className="bg-indigo-600 hover:bg-indigo-500 disabled:opacity-50 disabled:cursor-not-allowed text-white px-6 py-2 rounded-lg font-medium transition-all shadow-lg shadow-indigo-500/20 flex items-center gap-2"
                      >
                        {isParsing && taskProgress < 100 ? <Loader2 className="animate-spin w-4 h-4" /> : t('parser.processBatch')}
                      </button>
                    </div>
                  </div>
                )}
              </div>
            </div>

            {/* Parser Configuration Options */}
            <div className="grid grid-cols-1 md:grid-cols-3 gap-3">
              {/* Option: Video/Image */}
              <button
                onClick={() => setDownloadOptions(prev => ({ ...prev, video: !prev.video }))}
                className={`flex items-center gap-3 p-3.5 rounded-xl border transition-all text-left ${
                  downloadOptions.video
                    ? 'bg-indigo-500/10 border-indigo-500/40 text-indigo-300'
                    : 'bg-zinc-900/50 border-zinc-800 text-zinc-500 hover:border-zinc-700'
                }`}
              >
                <div className={`w-5 h-5 rounded flex items-center justify-center border flex-shrink-0 transition-colors ${
                  downloadOptions.video ? 'bg-indigo-500 border-indigo-500' : 'border-zinc-600 bg-zinc-900'
                }`}>
                  {downloadOptions.video && <Check size={14} className="text-white" />}
                </div>
                <div className="flex items-center gap-2">
                  <VideoIcon size={16} />
                  <span className="text-sm font-medium">{t('parser.downloadVideo')}</span>
                </div>
              </button>

              {/* Option: Audio */}
              <button
                onClick={() => setDownloadOptions(prev => ({ ...prev, audio: !prev.audio }))}
                className={`flex items-center gap-3 p-3.5 rounded-xl border transition-all text-left ${
                  downloadOptions.audio
                    ? 'bg-indigo-500/10 border-indigo-500/40 text-indigo-300'
                    : 'bg-zinc-900/50 border-zinc-800 text-zinc-500 hover:border-zinc-700'
                }`}
              >
                <div className={`w-5 h-5 rounded flex items-center justify-center border flex-shrink-0 transition-colors ${
                  downloadOptions.audio ? 'bg-indigo-500 border-indigo-500' : 'border-zinc-600 bg-zinc-900'
                }`}>
                  {downloadOptions.audio && <Check size={14} className="text-white" />}
                </div>
                <div className="flex items-center gap-2">
                  <Music size={16} />
                  <span className="text-sm font-medium">{t('parser.downloadAudio')}</span>
                </div>
              </button>

              {/* Option: Cover */}
              <button
                onClick={() => setDownloadOptions(prev => ({ ...prev, cover: !prev.cover }))}
                className={`flex items-center gap-3 p-3.5 rounded-xl border transition-all text-left ${
                  downloadOptions.cover
                    ? 'bg-indigo-500/10 border-indigo-500/40 text-indigo-300'
                    : 'bg-zinc-900/50 border-zinc-800 text-zinc-500 hover:border-zinc-700'
                }`}
              >
                <div className={`w-5 h-5 rounded flex items-center justify-center border flex-shrink-0 transition-colors ${
                  downloadOptions.cover ? 'bg-indigo-500 border-indigo-500' : 'border-zinc-600 bg-zinc-900'
                }`}>
                  {downloadOptions.cover && <Check size={14} className="text-white" />}
                </div>
                <div className="flex items-center gap-2">
                  <ImageIcon size={16} />
                  <span className="text-sm font-medium">{t('parser.downloadCover')}</span>
                </div>
              </button>
            </div>

            {/* Tag Selector */}
            <ParserTagSelector
              selectedTagIds={selectedTagIds}
              onTagsChange={setSelectedTagIds}
            />

            {error && (
              <div className="bg-red-950/20 border border-red-900/50 text-red-200 p-4 rounded-xl flex items-center gap-3">
                <AlertCircle className="w-5 h-5 flex-shrink-0" />
                <p>{error}</p>
              </div>
            )}

            {/* Task Monitor - Shows when parsing or downloading */}
            {(isParsing || taskProgress > 0) && taskProgress < 100 && (
              <TaskMonitor
                logs={socketLogs}
                progress={taskProgress}
                status={taskStatus}
                systemStatus={systemStatus}
              />
            )}

            {/* Result Card with Progressive Download Progress */}
            {parserMode === 'single' && currentResult && (
              <div className="mt-8 animate-in fade-in zoom-in-95 duration-300">
                 <div className="flex items-center justify-between mb-4 px-1">
                    <h3 className="text-lg font-semibold text-zinc-300">Analysis Result</h3>
                    <div className="flex items-center gap-2">
                       {downloadTaskId && downloadStatus !== 'completed' && downloadStatus !== 'failed' ? (
                         <span className="text-xs text-indigo-400 flex items-center gap-1">
                            <Loader2 size={12} className="animate-spin" />
                            {t('download.downloading')} {downloadPercent}%
                            {downloadSpeed && <span className="text-zinc-500 ml-1">({downloadSpeed})</span>}
                         </span>
                       ) : downloadStatus === 'completed' || taskProgress === 100 ? (
                         <span className="text-xs text-green-400 flex items-center gap-1">
                            <CheckCircle2 size={12} />
                            {t('download.completed')}
                         </span>
                       ) : downloadStatus === 'failed' ? (
                         <span className="text-xs text-red-400 flex items-center gap-1">
                            <AlertCircle size={12} />
                            {t('download.failed')}
                         </span>
                       ) : null}
                    </div>
                 </div>

                 {/* MediaCard with integrated download progress in left section */}
                 <MediaCard
                   data={currentResult}
                   onSave={(item) => handleSaveToLibrary(item)}
                   onUpdate={handleUpdateLibraryItem}
                   downloadStatus={downloadTaskId ? downloadStatus : undefined}
                   downloadPercent={downloadPercent}
                   downloadSpeed={downloadSpeed}
                   progressStyle={userSettings.progressStyle || 'neon'}
                 />
              </div>
            )}

            {/* Batch Results Grid */}
            {parserMode === 'batch' && batchResults.length > 0 && (
               <div className="mt-8 animate-in fade-in slide-in-from-bottom-2 duration-300">
                  <div className="flex items-center justify-between mb-4 px-1">
                      <div>
                        <h3 className="text-lg font-semibold text-zinc-300">Batch Results</h3>
                        <p className="text-xs text-zinc-500">Successfully processed {batchResults.length} items.</p>
                      </div>
                      <button 
                        onClick={handleBatchSave}
                        className="bg-zinc-100 hover:bg-white text-zinc-900 px-4 py-2 rounded-lg text-sm font-semibold transition-colors flex items-center gap-2 shadow-lg"
                      >
                         <Download size={16} />
                         Save All ({batchResults.length})
                      </button>
                  </div>
                  
                  <div className="columns-2 md:columns-3 gap-3 mx-auto space-y-3">
                      {batchResults.map((item, idx) => (
                        <CompactMediaCard
                          key={`${item.platform_id}-${idx}`}
                          data={item}
                          onClick={() => {
                             // Switch to library detail view mock-up if needed,
                             // for now just log since we are in parser mode
                             console.log("Clicked batch item:", item.title);
                          }}
                          isShared={sharedVideoIds.includes(item.platform_id)}
                        />
                      ))}
                  </div>
               </div>
            )}

            {!currentResult && batchResults.length === 0 && !isParsing && taskProgress === 0 && (
              <div className="grid grid-cols-3 gap-3 mt-8">
                 {/* Queue Status */}
                 <div className="p-3 md:p-5 rounded-xl bg-zinc-900/50 border border-zinc-800/50 hover:border-zinc-700 transition-colors text-center flex flex-col items-center justify-center min-h-0">
                    <div className={`w-8 h-8 md:w-10 md:h-10 rounded-lg flex items-center justify-center mb-2 ${
                      systemStatus?.queue.status === 'offline' ? 'bg-red-900/30 text-red-400' :
                      systemStatus?.queue.active ? 'bg-indigo-900/30 text-indigo-400' : 'bg-zinc-800/50 text-zinc-500'
                    }`}>
                      <ListVideo size={16} className="md:hidden" />
                      <ListVideo size={20} className="hidden md:block" />
                    </div>
                    <h4 className="font-semibold text-zinc-200 text-xs md:text-base mb-0.5">Queue</h4>
                    <p className={`text-xs md:text-sm font-mono ${
                      systemStatus?.queue.status === 'offline' ? 'text-red-400' :
                      systemStatus?.queue.active ? 'text-indigo-400' : 'text-zinc-500'
                    }`}>
                      {systemStatus ? getQueueDisplay(systemStatus.queue) : '...'}
                    </p>
                    {systemStatus?.queue.pending ? (
                      <p className="text-[10px] md:text-xs text-zinc-600 mt-0.5">{systemStatus.queue.pending} pending</p>
                    ) : null}
                 </div>
                 {/* Parse Mode */}
                 <ParseModeCard />
                 {/* Storage Status */}
                 <div className="p-3 md:p-5 rounded-xl bg-zinc-900/50 border border-zinc-800/50 hover:border-zinc-700 transition-colors text-center flex flex-col items-center justify-center min-h-0">
                    <div className={`w-8 h-8 md:w-10 md:h-10 rounded-lg flex items-center justify-center mb-2 ${
                      systemStatus?.storage.status === 'ok' ? 'bg-purple-900/30 text-purple-400' :
                      systemStatus?.storage.status === 'warning' ? 'bg-yellow-900/30 text-yellow-400' :
                      systemStatus?.storage.status === 'critical' ? 'bg-red-900/30 text-red-400' : 'bg-zinc-800/50 text-zinc-500'
                    }`}>
                      <HardDrive size={16} className="md:hidden" />
                      <HardDrive size={20} className="hidden md:block" />
                    </div>
                    <h4 className="font-semibold text-zinc-200 text-xs md:text-base mb-0.5">Storage</h4>
                    <p className={`text-xs md:text-sm font-mono ${
                      systemStatus?.storage.status === 'ok' ? 'text-purple-400' :
                      systemStatus?.storage.status === 'warning' ? 'text-yellow-400' :
                      systemStatus?.storage.status === 'critical' ? 'text-red-400' : 'text-zinc-500'
                    }`}>
                      {systemStatus ? getStorageDisplay(systemStatus.storage) : '...'}
                    </p>
                    {systemStatus?.storage.percent_used ? (
                      <p className="text-[10px] md:text-xs text-zinc-600 mt-0.5">{systemStatus.storage.percent_used}% used</p>
                    ) : null}
                 </div>
              </div>
            )}
          </div>
        )}
        
        {/* VIEW: LIBRARY */}
        {view === 'library' && (
          <div className="animate-in fade-in slide-in-from-bottom-4 duration-500 h-full flex flex-col">
            
            {selectedLibraryItem ? (
              // DETAIL VIEW - VideoDetailPanel with tabs
              <div className="flex flex-col h-full p-4 md:p-0">
                 <div className="flex items-center gap-3 mb-4 shrink-0">
                    <button
                      onClick={() => setSelectedLibraryItem(null)}
                      className="p-2 -ml-2 rounded-full hover:bg-zinc-800 text-zinc-400 hover:text-white transition-colors"
                    >
                       <ArrowLeft size={24} />
                    </button>
                    <h2 className="text-xl font-bold text-white">Media Details</h2>
                 </div>
                 <div className="flex-1 min-h-0">
                    <VideoDetailPanel
                      video={selectedLibraryItem}
                      onClose={() => setSelectedLibraryItem(null)}
                      onUpdate={handleUpdateLibraryItem}
                      onDelete={handleDeleteLibraryItem}
                      collections={collections}
                      videoCollectionIds={selectedVideoCollectionIds}
                      onToggleCollection={handleToggleVideoCollection}
                      onCreateCollection={handleCreateCollection}
                    />
                 </div>
              </div>
            ) : (
              // LIST/GRID VIEW
              <>
                {/* Desktop Header */}
                {(
                <header className="hidden md:flex flex-col md:flex-row md:items-center justify-between gap-4 mb-6">
                  <div>
                    <div className="flex items-center gap-3">
                      {activeCollectionId && (
                        <button
                          onClick={() => setActiveCollectionId(null)}
                          className="p-2 -ml-2 rounded-lg text-zinc-400 hover:text-white hover:bg-zinc-800 transition-colors"
                          title={t('common.back')}
                        >
                          <ArrowLeft size={20} />
                        </button>
                      )}
                      {activeCollectionId && (
                        <div className="p-2 bg-indigo-500/20 rounded-lg">
                          <Folder size={20} className="text-indigo-400" />
                        </div>
                      )}
                      <h1 className="text-2xl font-bold text-white flex items-center gap-3">
                        {activeCollectionId
                          ? collections.find(c => c.id === activeCollectionId)?.name || 'Collection'
                          : t('library.title')}
                        {activeCollectionId && (
                          <span className="flex items-center gap-1 text-sm font-normal px-2 py-0.5 bg-zinc-800 rounded text-zinc-400">
                            <span>{collectionVideoIds.length}</span>
                            <VideoIcon size={14} className="text-indigo-400" />
                          </span>
                        )}
                      </h1>
                    </div>
                    {!activeCollectionId && (
                      <p className="text-zinc-400 text-sm">{t('library.subtitle')}</p>
                    )}
                  </div>
                  
                  <div className="flex flex-col md:flex-row gap-3 w-full md:w-auto">
                     {/* Refresh Button for Desktop */}
                     <button 
                        onClick={loadLibraryData}
                        disabled={isLoadingLibrary}
                        className="p-2 bg-zinc-900 rounded-lg border border-zinc-800 text-zinc-400 hover:text-white hover:bg-zinc-800 transition-colors"
                        title="Refresh Data"
                     >
                        <RefreshCw size={20} className={isLoadingLibrary ? "animate-spin" : ""} />
                     </button>

                     {/* Semantic Search */}
                     <SemanticSearchBar
                       onSearch={(results, query) => {
                         setSearchResults(results);
                         setSearchQueryText(query);
                         setIsSearchActive(true);
                       }}
                       onClear={() => {
                         setSearchResults([]);
                         setSearchQueryText('');
                         setIsSearchActive(false);
                       }}
                       placeholder={t('library.searchPlaceholder')}
                       className="flex-1 md:w-80"
                       library={library}
                     />

                     {/* View Toggle */}
                     <div className="hidden md:flex bg-zinc-900 rounded-lg border border-zinc-800 p-1">
                        <button 
                          onClick={() => setLibraryViewMode('list')}
                          className={`p-1.5 rounded-md transition-all ${libraryViewMode === 'list' ? 'bg-zinc-800 text-white shadow-sm' : 'text-zinc-500 hover:text-zinc-300'}`}
                          title="List View"
                        >
                          <LayoutList size={18} />
                        </button>
                        <button 
                          onClick={() => setLibraryViewMode('grid')}
                          className={`p-1.5 rounded-md transition-all ${libraryViewMode === 'grid' ? 'bg-zinc-800 text-white shadow-sm' : 'text-zinc-500 hover:text-zinc-300'}`}
                          title="Grid View"
                        >
                          <LayoutGrid size={18} />
                        </button>
                        <button 
                          onClick={() => setLibraryViewMode('feed')}
                          className={`p-1.5 rounded-md transition-all ${libraryViewMode === 'feed' ? 'bg-zinc-800 text-white shadow-sm' : 'text-zinc-500 hover:text-zinc-300'}`}
                          title="Feed View"
                        >
                          <Smartphone size={18} />
                        </button>
                     </div>
                  </div>
                </header>
                )}

                {/* Library Content - Show when in My Library or inside a collection */}
                {(activeLibraryTab === 'my-library' || activeCollectionId) && (
                <div className="h-full relative flex-1 min-h-0">
                  {isLoadingLibrary && library.length === 0 ? (
                    <div className="flex flex-col items-center justify-center h-96 text-zinc-500">
                      <Loader2 className="w-8 h-8 animate-spin mb-4 text-indigo-500" />
                      <p>Loading your collection...</p>
                    </div>
                  ) : (
                    <>
                      {libraryError && (
                        <div className="mb-4 mx-4 md:mx-0 p-3 bg-yellow-900/20 border border-yellow-900/50 rounded-lg flex items-center gap-3 text-sm text-yellow-200/80">
                          <CloudOff size={16} />
                          {libraryError}
                        </div>
                      )}

                      {libraryViewMode === 'grid' ? (
                        <div className="p-2 md:p-0 w-full">
                          {filteredLibrary.length > 0 ? (
                             <>
                               {/* Grid layout */}
                               <div className="grid grid-cols-2 md:grid-cols-3 lg:grid-cols-4 xl:grid-cols-5 2xl:grid-cols-6 gap-3 w-full">
                                  {filteredLibrary.map((item, idx) => (
                                    <CompactMediaCard
                                      key={`${item.platform_id}-${idx}`}
                                      data={item}
                                      onClick={() => setSelectedLibraryItem(item)}
                                      isShared={sharedVideoIds.includes(item.platform_id)}
                                    />
                                  ))}
                               </div>
                               {/* Infinite scroll trigger */}
                               {!isSearchActive && (
                                 <div ref={loadMoreRef} className="w-full py-8 flex justify-center">
                                   {isLoadingMore ? (
                                     <div className="flex items-center gap-2 text-zinc-500">
                                       <Loader2 className="w-5 h-5 animate-spin" />
                                       <span className="text-sm">Loading more...</span>
                                     </div>
                                   ) : hasMoreData ? (
                                     <div className="text-zinc-600 text-sm">Scroll for more</div>
                                   ) : library.length > 0 ? (
                                     <div className="text-zinc-600 text-sm">All {library.length} items loaded</div>
                                   ) : null}
                                 </div>
                               )}
                             </>
                          ) : (
                            <div className="text-center py-20 bg-zinc-900/30 rounded-2xl border border-dashed border-zinc-800 text-zinc-500">
                              {isLoadingLibrary ? "Searching..." : "No items found matching your search."}
                            </div>
                          )}
                        </div>
                      ) : libraryViewMode === 'list' ? (
                        <div className="p-4 md:p-0">
                          <LibraryTable
                            data={filteredLibrary}
                            onUpdate={handleUpdateLibraryItem}
                            onItemClick={(item) => setSelectedLibraryItem(item)}
                          />
                        </div>
                      ) : (
                        <LibraryFeed data={filteredLibrary} />
                      )}
                    </>
                  )}
                </div>
                )}
              </>
            )}
          </div>
        )}

        {/* VIEW: DASHBOARD */}
        {view === 'dashboard' && (
          <div className="animate-in fade-in slide-in-from-bottom-4 duration-500">
            {dashboardSubView === 'overview' && (
              <div className="space-y-8">
                <header className="mb-8">
                  <h1 className="text-2xl font-bold text-white">{t('dashboard.title')}</h1>
                  <p className="text-zinc-400 text-sm">{t('dashboard.subtitle')}</p>
                </header>

                <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-4 gap-4 mb-8">
                  {(() => {
                    const formatStorage = (bytes: number): string => {
                      if (bytes >= 1024 * 1024 * 1024) {
                        return `${(bytes / (1024 * 1024 * 1024)).toFixed(1)} GB`;
                      } else if (bytes >= 1024 * 1024) {
                        return `${(bytes / (1024 * 1024)).toFixed(1)} MB`;
                      } else if (bytes >= 1024) {
                        return `${(bytes / 1024).toFixed(1)} KB`;
                      }
                      return `${bytes} B`;
                    };

                    const stats = dashboardStats;
                    return [
                      {
                        label: t('dashboard.totalVideos'),
                        val: stats?.totalVideos?.toLocaleString() || "0",
                        change: `${stats?.completedDownloads || 0} done`,
                        color: "text-indigo-400"
                      },
                      {
                        label: t('dashboard.storageUsed'),
                        val: formatStorage(stats?.totalStorageBytes || 0),
                        change: `${stats?.pendingDownloads || 0} pending`,
                        color: "text-purple-400"
                      },
                      {
                        label: t('dashboard.savedCreators'),
                        val: (stats?.uniqueAuthors || 0).toString(),
                        change: `${stats?.failedDownloads || 0} failed`,
                        color: "text-pink-400"
                      },
                      {
                        label: t('dashboard.successRate'),
                        val: stats?.totalVideos
                          ? `${Math.round((stats.completedDownloads / stats.totalVideos) * 100)}%`
                          : "0%",
                        change: "overall",
                        color: "text-green-400"
                      },
                    ];
                  })().map((stat, i) => (
                    <div key={i} className="bg-zinc-900 border border-zinc-800 p-6 rounded-xl shadow-sm">
                      <p className="text-zinc-500 text-sm font-medium mb-2">{stat.label}</p>
                      <div className="flex items-end justify-between">
                        <span className="text-2xl font-bold text-white">{stat.val}</span>
                        <span className={`text-xs ${stat.color} bg-zinc-950 px-1.5 py-0.5 rounded`}>{stat.change}</span>
                      </div>
                    </div>
                  ))}
                </div>

                <StatsChart
                  weeklyActivity={dashboardStats?.weeklyActivity || []}
                  mediaDistribution={dashboardStats?.mediaDistribution || []}
                  topTags={dashboardStats?.topTags || []}
                />

                {/* Recent Activity */}
                {dashboardStats?.recentLogs && dashboardStats.recentLogs.length > 0 && (
                  <div className="mt-8 bg-zinc-900 border border-zinc-800 rounded-xl p-6">
                    <div className="flex items-center justify-between mb-4">
                      <div className="flex items-center gap-2">
                        <Activity size={18} className="text-amber-400" />
                        <h3 className="text-sm font-semibold text-zinc-200">Recent Activity</h3>
                      </div>
                      <button
                        onClick={() => setDashboardSubView('logs')}
                        className="text-xs text-indigo-400 hover:text-indigo-300 transition-colors"
                      >
                        View all logs
                      </button>
                    </div>
                    <div className="space-y-2">
                      {dashboardStats.recentLogs.slice(0, 5).map((log, i) => (
                        <div key={i} className="flex items-center gap-3 py-1.5">
                          <div className={`w-1.5 h-1.5 rounded-full flex-shrink-0 ${
                            log.status === 'success' ? 'bg-green-400' :
                            log.status === 'error' ? 'bg-red-400' :
                            'bg-zinc-500'
                          }`} />
                          <span className="text-sm text-zinc-300 truncate flex-1">{log.message}</span>
                          <span className="text-xs text-zinc-600 flex-shrink-0">{log.time}</span>
                        </div>
                      ))}
                    </div>
                  </div>
                )}
              </div>
            )}

            {dashboardSubView === 'tasks' && (
              <TasksPanel />
            )}

            {dashboardSubView === 'logs' && (
              <LogsPanel />
            )}

            {dashboardSubView === 'monitor' && userProfile.role === 'admin' && (
              <SystemMonitorPanel />
            )}
          </div>
        )}
        
        {/* VIEW: SETTINGS */}
        {view === 'settings' && (
           <SettingsView
             settings={userSettings}
             onUpdateSettings={handleUpdateSettings}
             activeTab={settingsTab}
             aiSettings={aiSettings}
             onSaveAISettings={setAISettings}
           />
        )}

        {/* VIEW: CLEANUP */}
        {view === 'cleanup' && (
          <div className="max-w-5xl mx-auto animate-in fade-in slide-in-from-bottom-4 duration-500">
            <div className="mb-8">
              <h1 className="text-3xl font-bold text-white mb-2">Storage Cleanup</h1>
              <p className="text-zinc-400">Review and clean up videos to free up storage space</p>
            </div>
            <CleanupSuggestionsView />
          </div>
        )}

        {/* VIEW: MEDIATRACK */}
        {view === 'mediatrack' && (
          reviewFile && selectedProject ? (
            <VideoReviewPage
              projectId={selectedProject.id}
              file={reviewFile}
              onBack={() => setReviewFile(null)}
              currentUserId={currentUserId || ''}
            />
          ) : (
            <div className="max-w-7xl mx-auto animate-in fade-in slide-in-from-bottom-4 duration-500">
              {selectedProject ? (
                <ProjectFilesView
                  project={selectedProject}
                  onBack={() => setSelectedProject(null)}
                  onFileReview={(file) => setReviewFile(file)}
                />
              ) : (
                <ProjectsListView
                  onProjectSelect={setSelectedProject}
                  onCreateProject={() => setIsCreateProjectModalOpen(true)}
                />
              )}
            </div>
          )
        )}

        {/* VIEW: POINTS */}
        {view === 'points' && (
          <PointsCenter onBuyPackage={(pkg) => setSelectedPaymentPackage(pkg)} />
        )}

        {/* VIEW: BILLING */}
        {view === 'billing' && selectedTeamId && (
          <BillingView
            teamId={selectedTeamId}
            permissions={userPermissions}
            onBuyPackage={(pkg) => setSelectedPaymentPackage(pkg)}
          />
        )}

        {/* VIEW: MEMBERS */}
        {view === 'members' && selectedTeamId && currentTeam && (
          <MembersView
            teamId={selectedTeamId}
            teamName={currentTeam.name}
            currentUserId={currentUserId || ''}
            permissions={userPermissions}
          />
        )}

        {/* VIEW: RESOURCES */}
        {view === 'resources' && (
          <ResourcesView
            scopeType={selectedTeamId ? 'team' : 'personal'}
            scopeId={selectedTeamId || currentUserId || ''}
          />
        )}

        {/* VIEW: TODOLIST */}
        {view === 'todolist' && (
          <div className="flex flex-col items-center justify-center h-64 text-zinc-500">
            <ListTodo size={48} className="mb-4 text-zinc-600" />
            <p className="text-lg font-medium text-zinc-400">Todolist</p>
            <p className="text-sm mt-1">Coming soon</p>
          </div>
        )}

        {/* VIEW: MANAGEMENT */}
        {view === 'management' && (
          <div className="flex flex-col items-center justify-center h-64 text-zinc-500">
            <Settings size={48} className="mb-4 text-zinc-600" />
            <p className="text-lg font-medium text-zinc-400">Team Management</p>
            <p className="text-sm mt-1">Coming soon</p>
          </div>
        )}

      </main>

      {/* Payment Modal */}
      {selectedPaymentPackage && selectedTeamId && (
        <PaymentModal
          package={selectedPaymentPackage}
          teamId={selectedTeamId}
          onClose={() => setSelectedPaymentPackage(null)}
          onSuccess={() => {
            setSelectedPaymentPackage(null);
            // Refresh the points or billing view if currently viewing it
            if (view === 'points' || view === 'billing') {
              const currentView = view;
              setView('parser');
              setTimeout(() => setView(currentView), 0);
            }
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
          setView('mediatrack');
        }}
      />

      {/* Mobile Library Search - Fixed outside main to avoid transform issues */}
      {view === 'library' && !selectedLibraryItem && (activeLibraryTab === 'my-library' || activeCollectionId) && (
        <div className="md:hidden fixed top-0 left-0 right-0 z-30 p-4 flex justify-end items-start pointer-events-none bg-gradient-to-b from-black/60 to-transparent">
          <div className="pointer-events-auto flex items-center justify-end w-full max-w-[calc(100%-16px)]">
            {isMobileSearchOpen ? (
              <div className="flex items-center bg-black/50 backdrop-blur-md rounded-full px-4 py-2.5 w-full animate-in slide-in-from-right-10 duration-200 border border-white/10 shadow-lg">
                <Search size={16} className="text-zinc-300 mr-2 flex-shrink-0"/>
                <input
                  autoFocus
                  className="bg-transparent border-none outline-none text-white text-sm w-full placeholder-zinc-400"
                  placeholder="Search collection..."
                  value={searchQuery}
                  onChange={(e) => setSearchQuery(e.target.value)}
                />
                <button
                  onClick={() => {
                    setIsMobileSearchOpen(false);
                    setSearchQuery('');
                  }}
                  className="ml-2 text-zinc-400 hover:text-white"
                >
                  <X size={16} />
                </button>
              </div>
            ) : (
              <button
                onClick={() => setIsMobileSearchOpen(true)}
                className="p-3 bg-black/20 backdrop-blur-md rounded-full text-white hover:bg-black/40 transition-colors shadow-lg border border-white/5"
              >
                <Search size={22} className="drop-shadow-md" />
              </button>
            )}
          </div>
        </div>
      )}
    </div>
    </ToastProvider>
  );
}

export default function App() {
  return (
    <AuthProvider>
      <AppInner />
    </AuthProvider>
  );
}
