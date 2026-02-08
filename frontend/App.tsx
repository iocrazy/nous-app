
import React, { useState, useEffect, useRef } from 'react';
import {
  LayoutDashboard, Search, Library, Settings, LogOut,
  Link as LinkIcon, AlertCircle, Loader2, Sparkles, User, Database,
  LayoutGrid, LayoutList, ChevronDown, FolderOpen, Folder, Key, Smartphone, X,
  CloudOff, RefreshCw, Terminal, Activity, CheckCircle2,
  ListVideo, Wifi, HardDrive, ArrowLeft, Check, Music, Video as VideoIcon, Image as ImageIcon, Tag,
  Layers, Download, Users, Trash2, ScrollText, ListTodo
} from 'lucide-react';
import { useTranslation } from 'react-i18next';
import { getSupabaseClient, isSupabaseConfigured, reinitializeSupabaseClient, getSupabaseCredentials } from './supabaseClient';
import { Video, ViewState, UserProfile, UserSettings, Team, Collection, AISettings as AISettingsType } from './types';
import { parseShareLink, parseBatchLinks, FetchResponse } from './services/parserService';
import { fetchLibrary, fetchLibraryPaginated, fetchVideoByPlatformId, saveItem, updateItem, deleteItem, fetchDashboardStats, DashboardStats, fetchUserSettings, saveUserSettings, fetchFrontendConfig, saveFrontendConfig } from './services/dataService';
import { fetchMyTeams } from './services/teamService';
import { fetchNotifications, markAsRead, markAllAsRead, NotificationWithRead } from './services/notificationService';
import { fetchMyCollections, createCollection, fetchVideoCollections, addVideoToCollection, removeVideoFromCollection } from './services/collectionService';
import { addTagsToVideo } from './services/tagsService';
import { MOCK_LIBRARY } from './constants';
import { MediaCard } from './components/MediaCard';
import { CompactMediaCard } from './components/CompactMediaCard';
import { StatsChart } from './components/StatsChart';
import { LibraryTable } from './components/LibraryTable';
import { LibraryFeed } from './components/LibraryFeed';
import { SettingsView } from './components/SettingsView';
import { UserProfileModal } from './components/UserProfileModal';
import { LandingPage } from './components/LandingPage';
import { AuthOverlay } from './components/AuthOverlay';
import { Header } from './components/Header';
import { ParseModeCard } from './components/ParseModeCard';
import { UserDropdown } from './components/UserDropdown';
import { ParserTagSelector } from './components/ParserTagSelector';
import { NotificationPanel } from './components/NotificationPanel';
import { CreateTeamModal } from './components/CreateTeamModal';
import { CreateCollectionModal } from './components/CreateCollectionModal';
import { DownloadProgress, DownloadStatus as ProgressStatus } from './components/DownloadProgress';
import { useDownloadProgress } from './hooks/useDownloadProgress';
import { SmartCollectionsSidebar } from './components/SmartCollectionsSidebar';
import { SemanticSearchBar } from './components/SemanticSearchBar';
import { CleanupSuggestionsView } from './components/CleanupSuggestionsView';
import { SmartCollection } from './services/smartCollectionService';
import { SearchResult } from './services/searchService';
import { LibraryTabs, LibraryTab } from './components/LibraryTabs';
import { TeamLibraryView } from './components/TeamLibraryView';
import { SettingsModal } from './components/SettingsModal';
import { VideoDetailPanel } from './components/VideoDetailPanel';
import { getSystemStatus, SystemStatus, getQueueDisplay, getStorageDisplay } from './services/systemService';
import { ToastProvider } from './components/Toast';

// --- Types for Monitor ---
interface LogEntry {
  id: string;
  time: string;
  message: string;
  type: 'info' | 'success' | 'warning' | 'error';
}

// --- Sub-components for Cleaner App ---

const SidebarItem = ({ 
  icon: Icon, 
  label, 
  active, 
  onClick,
  hasSubmenu = false,
  isOpen = false
}: { 
  icon: React.ElementType, 
  label: string, 
  active: boolean, 
  onClick: () => void,
  hasSubmenu?: boolean,
  isOpen?: boolean
}) => (
  <button 
    onClick={onClick}
    className={`w-full flex items-center justify-between px-4 py-3 rounded-xl transition-all duration-200 group ${
      active 
        ? 'bg-indigo-600/10 text-indigo-400 font-medium' 
        : 'text-zinc-400 hover:bg-zinc-800/50 hover:text-zinc-200'
    }`}
  >
    <div className="flex items-center gap-3">
      <Icon className={`w-5 h-5 ${active ? 'text-indigo-400' : 'text-zinc-500 group-hover:text-zinc-300'}`} />
      <span>{label}</span>
    </div>
    {hasSubmenu && (
      <ChevronDown size={16} className={`transition-transform duration-200 ${isOpen ? 'rotate-180' : ''}`} />
    )}
  </button>
);

// --- Task Monitor Component ---
const TaskMonitor = ({
  logs,
  progress,
  status,
  systemStatus
}: {
  logs: LogEntry[],
  progress: number,
  status: string,
  systemStatus: SystemStatus | null
}) => {
  const logsEndRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    logsEndRef.current?.scrollIntoView({ behavior: 'smooth' });
  }, [logs]);

  return (
    <div className="mt-8 bg-zinc-950 border border-zinc-800 rounded-xl overflow-hidden shadow-2xl animate-in slide-in-from-bottom-2 duration-300">
      {/* Header / Status Bar */}
      <div className="bg-zinc-900/80 backdrop-blur-sm border-b border-zinc-800 p-4">
        <div className="flex items-center justify-between mb-3">
          <div className="flex items-center gap-3">
             <div className="relative">
                {progress === 100 ? (
                  <div className="w-3 h-3 bg-green-500 rounded-full shadow-[0_0_10px_rgba(34,197,94,0.5)]"></div>
                ) : (
                  <div className="w-3 h-3 bg-indigo-500 rounded-full animate-pulse shadow-[0_0_10px_rgba(99,102,241,0.5)]"></div>
                )}
             </div>
             <span className="font-mono text-sm text-zinc-200 font-semibold uppercase tracking-wider">
               {status}
             </span>
          </div>
          <span className="text-xs font-mono text-zinc-500">{progress}%</span>
        </div>
        
        {/* Progress Bar */}
        <div className="h-1.5 w-full bg-zinc-800 rounded-full overflow-hidden">
           <div 
             className="h-full bg-gradient-to-r from-indigo-500 to-purple-500 transition-all duration-300 ease-out relative"
             style={{ width: `${progress}%` }}
           >
              <div className="absolute inset-0 bg-white/20 animate-[shimmer_2s_infinite]"></div>
           </div>
        </div>

        {/* Quick Stats Grid */}
        <div className="grid grid-cols-3 gap-4 mt-4 pt-4 border-t border-zinc-800/50">
           <div className="flex flex-col items-center">
              <span className="text-[10px] text-zinc-500 uppercase tracking-widest mb-1">Queue</span>
              <div className="flex items-center gap-1.5 text-zinc-300">
                 <ListVideo size={14} className={
                   systemStatus?.queue.status === 'offline' ? 'text-red-400' :
                   systemStatus?.queue.active ? 'text-indigo-400' : 'text-zinc-500'
                 } />
                 <span className="font-mono text-xs font-medium">
                   {systemStatus ? getQueueDisplay(systemStatus.queue) : '...'}
                 </span>
              </div>
           </div>
           <div className="flex flex-col items-center border-l border-zinc-800/50">
              <span className="text-[10px] text-zinc-500 uppercase tracking-widest mb-1">Network</span>
              <div className="flex items-center gap-1.5 text-zinc-300">
                 <Wifi size={14} className={
                   systemStatus?.network.status === 'active' ? 'text-emerald-400' :
                   systemStatus?.network.status === 'error' ? 'text-red-400' : 'text-zinc-500'
                 } />
                 <span className="font-mono text-xs font-medium">
                   {systemStatus?.network.speed || '0 B/s'}
                 </span>
              </div>
           </div>
           <div className="flex flex-col items-center border-l border-zinc-800/50">
              <span className="text-[10px] text-zinc-500 uppercase tracking-widest mb-1">Storage</span>
              <div className="flex items-center gap-1.5 text-zinc-300">
                 <HardDrive size={14} className={
                   systemStatus?.storage.status === 'ok' ? 'text-purple-400' :
                   systemStatus?.storage.status === 'warning' ? 'text-yellow-400' :
                   systemStatus?.storage.status === 'critical' ? 'text-red-400' : 'text-zinc-500'
                 } />
                 <span className="font-mono text-xs font-medium">
                   {systemStatus ? getStorageDisplay(systemStatus.storage) : '...'}
                 </span>
              </div>
           </div>
        </div>
      </div>

      {/* Terminal Log View */}
      <div className="bg-[#0c0c0e] p-4 h-48 overflow-y-auto font-mono text-xs space-y-1.5 custom-scrollbar">
        {logs.length === 0 && (
          <div className="text-zinc-700 italic">Waiting for task logs...</div>
        )}
        {logs.map((log) => (
          <div key={log.id} className="flex gap-3 hover:bg-white/5 p-0.5 rounded px-2 transition-colors">
            <span className="text-zinc-600 flex-shrink-0">[{log.time}]</span>
            <span className={`break-all ${
              log.type === 'error' ? 'text-red-400' :
              log.type === 'success' ? 'text-green-400' :
              log.type === 'warning' ? 'text-yellow-400' :
              'text-zinc-300'
            }`}>
              {log.type === 'success' && '✓ '}
              {log.type === 'error' && '✗ '}
              {log.message}
            </span>
          </div>
        ))}
        <div ref={logsEndRef} />
      </div>
    </div>
  );
};


export default function App() {
  const { t } = useTranslation();
  const [view, setView] = useState<ViewState>('parser');
  const [settingsTab, setSettingsTab] = useState<'general' | 'api' | 'logs' | 'monitor' | 'tasks' | 'tags' | 'ai'>('general');
  const [urlInput, setUrlInput] = useState('');

  // Team and Notification State
  const [teams, setTeams] = useState<Team[]>([]);
  const [notifications, setNotifications] = useState<NotificationWithRead[]>([]);
  const [isUserDropdownOpen, setIsUserDropdownOpen] = useState(false);
  const [isNotificationPanelOpen, setIsNotificationPanelOpen] = useState(false);
  const [isCreateTeamModalOpen, setIsCreateTeamModalOpen] = useState(false);
  const [isSettingsModalOpen, setIsSettingsModalOpen] = useState(false);
  const [settingsModalInitialTab, setSettingsModalInitialTab] = useState<'personal' | 'team'>('personal');
  const [selectedTeamId, setSelectedTeamId] = useState<string | null>(() => {
    const saved = localStorage.getItem('mediahub_library_preferences');
    if (saved) {
      try {
        const parsed = JSON.parse(saved);
        return parsed.selectedTeamId || null;
      } catch {
        return null;
      }
    }
    return null;
  });
  const [collections, setCollections] = useState<Collection[]>([]);
  const [activeCollectionId, setActiveCollectionId] = useState<string | null>(null);
  const [collectionVideoIds, setCollectionVideoIds] = useState<string[]>([]);
  const [isCreateCollectionModalOpen, setIsCreateCollectionModalOpen] = useState(false);
  const [isLibraryOpen, setIsLibraryOpen] = useState(true);
  const [isSettingsOpen, setIsSettingsOpen] = useState(false);

  // Library Tab State (new unified library)
  const [activeLibraryTab, setActiveLibraryTab] = useState<LibraryTab>(() => {
    const saved = localStorage.getItem('mediahub_library_preferences');
    if (saved) {
      try {
        const parsed = JSON.parse(saved);
        return parsed.activeTab || 'my-library';
      } catch {
        return 'my-library';
      }
    }
    return 'my-library';
  });

  // Accordion behavior - close other menus when opening one
  const toggleLibraryMenu = () => {
    const newState = !isLibraryOpen;
    setIsLibraryOpen(newState);
    if (newState) {
      setIsSettingsOpen(false);
    }
  };

  const toggleSettingsMenu = () => {
    const newState = !isSettingsOpen;
    setIsSettingsOpen(newState);
    if (newState) {
      setIsLibraryOpen(false);
    }
  };

  // Helper: Check if team library is active (for filtering)
  const isTeamLibraryActive = activeLibraryTab === 'team-library';

  // Get current team based on selectedTeamId
  const currentTeam = teams.find(t => t.id === selectedTeamId) || null;

  // Smart Collections State
  const [activeSmartCollectionId, setActiveSmartCollectionId] = useState<number | null>(null);

  // Handle tab change (defined early, uses setActiveSmartCollectionId)
  const handleLibraryTabChange = (tab: LibraryTab) => {
    setActiveLibraryTab(tab);
    setActiveCollectionId(null); // Reset collection when switching tabs
    setActiveSmartCollectionId(null); // Reset smart collection
  };
  const [searchResults, setSearchResults] = useState<SearchResult[]>([]);
  const [isSearchActive, setIsSearchActive] = useState(false);
  const [searchQueryText, setSearchQueryText] = useState('');

  // Parser Configuration State
  const [parserMode, setParserMode] = useState<'single' | 'batch'>('single');
  const [batchInput, setBatchInput] = useState('');
  const [downloadOptions, setDownloadOptions] = useState({
    video: true,
    audio: false,
    cover: true
  });
  const [selectedTagIds, setSelectedTagIds] = useState<string[]>([]);
  
  // Parser & Task State
  const [isParsing, setIsParsing] = useState(false);
  const [taskStatus, setTaskStatus] = useState('Idle');
  const [taskProgress, setTaskProgress] = useState(0);
  const [socketLogs, setSocketLogs] = useState<LogEntry[]>([]);
  const [systemStatus, setSystemStatus] = useState<SystemStatus | null>(null);

  const [currentResult, setCurrentResult] = useState<Video | null>(null);
  const [batchResults, setBatchResults] = useState<Video[]>([]);

  // Progressive download state
  const [downloadTaskId, setDownloadTaskId] = useState<string | null>(null);

  // Library State
  const [library, setLibrary] = useState<Video[]>([]);
  const [isLoadingLibrary, setIsLoadingLibrary] = useState(false);
  const [libraryError, setLibraryError] = useState<string | null>(null);
  const [selectedLibraryItem, setSelectedLibraryItem] = useState<Video | null>(null); // For detail view

  // Infinite scroll pagination state
  const [currentPage, setCurrentPage] = useState(0);
  const [hasMoreData, setHasMoreData] = useState(true);
  const [isLoadingMore, setIsLoadingMore] = useState(false);
  const loadMoreRef = useRef<HTMLDivElement>(null);
  
  const [libraryViewMode, setLibraryViewMode] = useState<'grid' | 'list' | 'feed'>(() => {
    const saved = localStorage.getItem('mediahub_library_preferences');
    if (saved) {
      try {
        const parsed = JSON.parse(saved);
        if (parsed.viewMode && ['grid', 'list', 'feed'].includes(parsed.viewMode)) {
          return parsed.viewMode;
        }
      } catch {
        return 'grid';
      }
    }
    return 'grid';
  });

  // Save library preferences to localStorage (must be after libraryViewMode is defined)
  useEffect(() => {
    localStorage.setItem('mediahub_library_preferences', JSON.stringify({
      activeTab: activeLibraryTab,
      selectedTeamId,
      viewMode: libraryViewMode,
    }));
  }, [activeLibraryTab, selectedTeamId, libraryViewMode]);

  const [searchQuery, setSearchQuery] = useState('');
  const [isMobileMenuOpen, setIsMobileMenuOpen] = useState(false);
  const [isMobileSearchOpen, setIsMobileSearchOpen] = useState(false);

  // Dashboard Stats State
  const [dashboardStats, setDashboardStats] = useState<DashboardStats | null>(null);

  // User & Settings State - Load from LocalStorage if available
  const [userProfile, setUserProfile] = useState<UserProfile>({
    name: 'Demo User',
    email: 'demo@example.com',
    avatarUrl: '',
    plan: 'Free Plan'
  });

  const [userSettings, setUserSettings] = useState<UserSettings>({
    downloadPath: '/home/user/downloads/mediahub',
    supabaseUrl: '',
    supabaseAnonKey: ''
  });

  const [aiSettings, setAISettings] = useState<AISettingsType>({
    ai_enabled: false,
    auto_transcribe: false,
    auto_summarize: false,
    preferred_language: 'auto',
    providers: {},
    task_assignment: {
      transcription: '',
      summarization: '',
      visual_analysis: '',
    },
  });

  const [isProfileModalOpen, setIsProfileModalOpen] = useState(false);

  const [error, setError] = useState<string | null>(null);

  // Auth state
  const [isAuthenticated, setIsAuthenticated] = useState(false);
  const [currentUserId, setCurrentUserId] = useState<string | null>(null);
  // Controls the visibility of the login modal when on Landing Page
  const [showAuthModal, setShowAuthModal] = useState(false);

  // Config loading state
  const [isConfigLoaded, setIsConfigLoaded] = useState(false);

  // Load frontend config from backend YAML on mount
  useEffect(() => {
    const loadFrontendConfig = async () => {
      try {
        const config = await fetchFrontendConfig();
        if (config) {
          // 如果后端YAML配置了Supabase凭据，使用它们重新初始化客户端
          if (config.supabase_url && config.supabase_anon_key) {
            reinitializeSupabaseClient(config.supabase_url, config.supabase_anon_key);
            setUserSettings(prev => ({
              ...prev,
              supabaseUrl: config.supabase_url || '',
              supabaseAnonKey: config.supabase_anon_key || '',
              downloadPath: config.default_download_path || prev.downloadPath,
            }));
          } else if (config.default_download_path) {
            setUserSettings(prev => ({
              ...prev,
              downloadPath: config.default_download_path || prev.downloadPath,
            }));
          }
        }
      } catch (err) {
        console.error('Failed to load frontend config:', err);
      } finally {
        setIsConfigLoaded(true);
      }
    };
    loadFrontendConfig();
  }, []);

  // Check for existing session and listen for auth state changes
  useEffect(() => {
    if (!isConfigLoaded) return;

    const supabase = getSupabaseClient();
    if (!isSupabaseConfigured() || !supabase) return;

    // Update userSettings with Supabase credentials
    const credentials = getSupabaseCredentials();
    setUserSettings(prev => ({
      ...prev,
      supabaseUrl: credentials.url || prev.supabaseUrl,
      supabaseAnonKey: credentials.anonKey || prev.supabaseAnonKey,
    }));

    // Handle session state
    const handleSession = async (session: { user: { id: string; email?: string | null } } | null) => {
      if (session?.user) {
        setUserProfile(prev => ({
          ...prev,
          name: session.user.email?.split('@')[0] || 'User',
          email: session.user.email || '',
        }));
        setCurrentUserId(session.user.id);
        setIsAuthenticated(true);

        // Load user settings from Supabase
        try {
          const settings = await fetchUserSettings();
          if (settings) {
            setUserSettings(prev => ({
              ...prev,
              downloadPath: settings.download_path || prev.downloadPath,
            }));
          }
        } catch (err) {
          console.error('Failed to load user settings:', err);
        }
      } else {
        setIsAuthenticated(false);
        setCurrentUserId(null);
      }
    };

    // Check initial session
    supabase.auth.getSession().then(({ data: { session } }) => {
      handleSession(session);
    });

    // Listen for auth state changes (login, logout, token refresh)
    const { data: { subscription } } = supabase.auth.onAuthStateChange(
      async (event, session) => {
        console.log('Auth state changed:', event);
        if (event === 'SIGNED_OUT') {
          setIsAuthenticated(false);
          setCurrentUserId(null);
          setLibrary([]);
          setTeams([]);
          setCollections([]);
          setNotifications([]);
        } else if (event === 'SIGNED_IN' || event === 'TOKEN_REFRESHED') {
          handleSession(session);
        }
      }
    );

    // Cleanup subscription on unmount
    return () => {
      subscription.unsubscribe();
    };
  }, [isConfigLoaded]);

  // Fetch Library Data on Mount
  useEffect(() => {
    if (isAuthenticated) {
      loadLibraryData();
    }
  }, [isAuthenticated]);

  // Fetch teams, notifications, and collections when user logs in
  useEffect(() => {
    if (isAuthenticated) {
      // Fetch teams
      fetchMyTeams().then(setTeams).catch(console.error);
      // Fetch notifications
      fetchNotifications().then(setNotifications).catch(console.error);
      // Fetch collections
      fetchMyCollections().then(setCollections).catch(console.error);
    }
  }, [isAuthenticated]);

  // Poll for system status (queue, storage, network)
  useEffect(() => {
    if (!isAuthenticated) return;

    const fetchStatus = async () => {
      try {
        const status = await getSystemStatus();
        setSystemStatus(status);
      } catch (err) {
        // Silently fail - status is optional
        console.debug('Failed to fetch system status:', err);
      }
    };

    // Fetch immediately
    fetchStatus();

    // Poll every 10 seconds (reduced from 3s for better performance)
    const interval = setInterval(fetchStatus, 10000);

    return () => clearInterval(interval);
  }, [isAuthenticated]);

  // Auto-select first team if no team is selected
  useEffect(() => {
    if (teams.length > 0 && !selectedTeamId) {
      setSelectedTeamId(teams[0].id);
    }
  }, [teams, selectedTeamId]);

  // State for team library video IDs (all videos in team collections)
  const [teamLibraryVideoIds, setTeamLibraryVideoIds] = useState<string[]>([]);
  // State for all shared video IDs (for showing shared badge in library)
  const [sharedVideoIds, setSharedVideoIds] = useState<string[]>([]);

  // Function to load collection video IDs
  const loadCollectionVideos = async (collectionId: string | null) => {
    if (collectionId) {
      const supabase = getSupabaseClient();
      if (supabase) {
        const { data } = await supabase
          .from('video_collections')
          .select('video_id')
          .eq('collection_id', parseInt(collectionId));

        if (data) {
          // Get platform_ids from video_ids
          const videoIds = data.map(v => v.video_id);
          if (videoIds.length > 0) {
            const { data: videos } = await supabase
              .from('videos')
              .select('platform_id')
              .in('id', videoIds);
            setCollectionVideoIds(videos?.map(v => v.platform_id) || []);
          } else {
            setCollectionVideoIds([]);
          }
        } else {
          setCollectionVideoIds([]);
        }
      }
    } else {
      setCollectionVideoIds([]);
    }
  };

  // Load collection video IDs when collection is selected
  useEffect(() => {
    loadCollectionVideos(activeCollectionId);
  }, [activeCollectionId]);

  // Load all team collection video IDs when Team Library is active
  useEffect(() => {
    const loadTeamLibraryVideos = async () => {
      if (isTeamLibraryActive && !activeCollectionId) {
        const supabase = getSupabaseClient();
        if (supabase) {
          // Get all team collections (collections with team_id)
          const teamCollections = collections.filter(c => c.team_id);
          if (teamCollections.length === 0) {
            setTeamLibraryVideoIds([]);
            return;
          }

          const teamCollectionIds = teamCollections.map(c => parseInt(c.id));
          const { data } = await supabase
            .from('video_collections')
            .select('video_id')
            .in('collection_id', teamCollectionIds);

          if (data && data.length > 0) {
            const videoIds = [...new Set(data.map(v => v.video_id))];
            const { data: videos } = await supabase
              .from('videos')
              .select('platform_id')
              .in('id', videoIds);

            setTeamLibraryVideoIds(videos?.map(v => v.platform_id) || []);
          } else {
            setTeamLibraryVideoIds([]);
          }
        }
      } else if (!isTeamLibraryActive) {
        setTeamLibraryVideoIds([]);
      }
    };
    loadTeamLibraryVideos();
  }, [isTeamLibraryActive, activeCollectionId, collections]);

  // Function to load all shared video IDs for badge display
  const loadAllSharedVideos = async (collectionsToCheck: Collection[]) => {
    const supabase = getSupabaseClient();
    if (!supabase || !isAuthenticated) {
      setSharedVideoIds([]);
      return;
    }

    // Get all team collections (collections with team_id)
    const teamCollections = collectionsToCheck.filter(c => c.team_id);
    if (teamCollections.length === 0) {
      setSharedVideoIds([]);
      return;
    }

    const teamCollectionIds = teamCollections.map(c => parseInt(c.id));
    const { data } = await supabase
      .from('video_collections')
      .select('video_id')
      .in('collection_id', teamCollectionIds);

    if (data && data.length > 0) {
      const videoIds = [...new Set(data.map(v => v.video_id))];
      const { data: videos } = await supabase
        .from('videos')
        .select('platform_id')
        .in('id', videoIds);

      setSharedVideoIds(videos?.map(v => v.platform_id) || []);
    } else {
      setSharedVideoIds([]);
    }
  };

  // Load all shared video IDs for badge display in library (regardless of Team Library mode)
  useEffect(() => {
    loadAllSharedVideos(collections);
  }, [isAuthenticated, collections]);

  // Supabase Realtime 订阅 - 自动同步数据库变化
  useEffect(() => {
    const supabase = getSupabaseClient();
    if (!isAuthenticated || !isSupabaseConfigured() || !supabase) {
      return;
    }

    const channel = supabase
      .channel('videos_realtime')
      .on(
        'postgres_changes',
        {
          event: '*',
          schema: 'public',
          table: 'videos',
        },
        async (payload) => {
          console.log('Realtime update:', payload.eventType, payload);

          // 获取当前用户 ID
          const { data: { user } } = await supabase.auth.getUser();
          if (!user) return;

          const newRecord = payload.new as Video;
          const oldRecord = payload.old as Video;

          // 只处理当前用户的数据
          if (payload.eventType === 'INSERT' && newRecord.user_id === user.id) {
            setLibrary(prev => {
              // 避免重复添加
              if (prev.find(item => item.platform_id === newRecord.platform_id)) {
                return prev;
              }
              return [newRecord, ...prev];
            });
          } else if (payload.eventType === 'UPDATE' && newRecord.user_id === user.id) {
            setLibrary(prev =>
              prev.map(item =>
                item.platform_id === newRecord.platform_id ? newRecord : item
              )
            );
            // 如果当前选中的项被更新，也更新它
            setSelectedLibraryItem(prev =>
              prev?.platform_id === newRecord.platform_id ? newRecord : prev
            );
            // 如果当前解析结果被更新，也更新它
            setCurrentResult(prev =>
              prev?.platform_id === newRecord.platform_id ? newRecord : prev
            );
          } else if (payload.eventType === 'DELETE' && oldRecord?.user_id === user.id) {
            setLibrary(prev =>
              prev.filter(item => item.platform_id !== oldRecord.platform_id)
            );
          }
        }
      )
      .subscribe((status) => {
        console.log('Realtime subscription status:', status);
      });

    // 清理订阅
    return () => {
      console.log('Unsubscribing from realtime channel');
      supabase.removeChannel(channel);
    };
  }, [isAuthenticated]);

  // Supabase Realtime for collection_videos - 共享集合实时同步
  useEffect(() => {
    const supabase = getSupabaseClient();
    if (!isAuthenticated || !isSupabaseConfigured() || !supabase) {
      return;
    }

    const collectionChannel = supabase
      .channel('video_collections_realtime')
      .on(
        'postgres_changes',
        {
          event: '*',
          schema: 'public',
          table: 'video_collections',
        },
        async (payload) => {
          console.log('Collection videos realtime update:', payload.eventType, payload);

          // video_collections 表的字段: collection_id (number), video_id (number), added_by, added_at
          const newRecord = payload.new as { collection_id: number; video_id: number };
          const oldRecord = payload.old as { collection_id: number; video_id: number };

          const changedCollectionId = newRecord?.collection_id || oldRecord?.collection_id;

          // 如果当前正在查看的集合有变化，重新加载集合视频列表
          if (activeCollectionId && changedCollectionId === parseInt(activeCollectionId)) {
            loadCollectionVideos(activeCollectionId);
          }

          // 如果正在查看的视频的集合关系变化，重新加载选中视频的集合列表
          if (selectedLibraryItem?.platform_id) {
            fetchVideoCollections(selectedLibraryItem.platform_id)
              .then(setSelectedVideoCollectionIds)
              .catch(console.error);
          }

          // 刷新集合列表以更新 video_count 和 sharedVideoIds
          fetchMyCollections().then(newCollections => {
            setCollections(newCollections);
            // 刷新共享视频ID列表以更新徽章显示
            loadAllSharedVideos(newCollections);
          }).catch(console.error);
        }
      )
      .subscribe((status) => {
        console.log('Collection videos realtime subscription status:', status);
      });

    return () => {
      console.log('Unsubscribing from collection videos realtime channel');
      supabase.removeChannel(collectionChannel);
    };
  }, [isAuthenticated, activeCollectionId, selectedLibraryItem?.platform_id]);

  // Supabase Realtime for video_tags - 标签实时同步
  useEffect(() => {
    const supabase = getSupabaseClient();
    if (!isAuthenticated || !isSupabaseConfigured() || !supabase) {
      return;
    }

    const tagsChannel = supabase
      .channel('video_tags_realtime')
      .on(
        'postgres_changes',
        {
          event: '*',
          schema: 'public',
          table: 'video_tags',
        },
        async (payload) => {
          console.log('Video tags realtime update:', payload.eventType, payload);

          const newRecord = payload.new as { video_id: number; tag_id: string };
          const oldRecord = payload.old as { video_id: number; tag_id: string };
          const videoId = newRecord?.video_id || oldRecord?.video_id;

          if (!videoId) return;

          // Fetch updated video with tags from the view
          const { data: updatedVideo, error } = await supabase
            .from('videos_with_tags')
            .select('*')
            .eq('id', videoId)
            .single();

          if (error || !updatedVideo) {
            console.error('Failed to fetch updated video:', error);
            return;
          }

          // Update the video in library state with new tags
          setLibrary(prev =>
            prev.map(item =>
              item.id === videoId ? { ...item, tags: updatedVideo.tags || [] } : item
            )
          );

          // Also update selected item if it's the same video
          setSelectedLibraryItem(prev =>
            prev?.id === videoId ? { ...prev, tags: updatedVideo.tags || [] } : prev
          );
        }
      )
      .subscribe((status) => {
        console.log('Video tags realtime subscription status:', status);
      });

    return () => {
      console.log('Unsubscribing from video tags realtime channel');
      supabase.removeChannel(tagsChannel);
    };
  }, [isAuthenticated]);

  // Load collections for selected video
  useEffect(() => {
    if (selectedLibraryItem?.platform_id) {
      loadSelectedVideoCollections(selectedLibraryItem.platform_id);
    } else {
      setSelectedVideoCollectionIds([]);
    }
  }, [selectedLibraryItem?.platform_id]);

  const handleLogin = (user: { email: string; id: string }) => {
    setUserProfile(prev => ({
      ...prev,
      name: user.email.split('@')[0] || 'User',
      email: user.email,
    }));
    setCurrentUserId(user.id);
    setIsAuthenticated(true);
    setShowAuthModal(false);
  };

  const handleLogout = async () => {
    const supabase = getSupabaseClient();
    if (isSupabaseConfigured() && supabase) {
      await supabase.auth.signOut();
    }
    setIsAuthenticated(false);
    setIsProfileModalOpen(false);
    setIsUserDropdownOpen(false);
    setUserProfile({
      name: 'Demo User',
      email: 'demo@example.com',
      avatarUrl: '',
      plan: 'Free Plan'
    });
    // Reset team and notification state
    setTeams([]);
    setNotifications([]);
    setActiveTeamId(null);
    setCurrentUserId(null);
    // When logging out, we go back to landing page.
    // Ensure modal is closed
    setShowAuthModal(false);
  };

  // Notification handlers
  const handleMarkNotificationRead = async (id: string) => {
    try {
      await markAsRead(id);
      setNotifications(prev => prev.map(n => n.id === id ? { ...n, read: true } : n));
    } catch (error) {
      console.error('Failed to mark notification as read:', error);
    }
  };

  const handleMarkAllNotificationsRead = async () => {
    try {
      await markAllAsRead();
      setNotifications(prev => prev.map(n => ({ ...n, read: true })));
    } catch (error) {
      console.error('Failed to mark all notifications as read:', error);
    }
  };

  // Team handlers
  const handleCreateTeam = () => {
    setIsUserDropdownOpen(false);
    setIsCreateTeamModalOpen(true);
  };

  const handleTeamCreated = (team: Team) => {
    setTeams(prev => [...prev, team]);
  };

  const handleTeamSettings = (teamId: string) => {
    setIsUserDropdownOpen(false);
    setSelectedTeamId(teamId);
    setSettingsModalInitialTab('team');
    setIsSettingsModalOpen(true);
  };

  const handleTeamUpdated = (updatedTeam: Team) => {
    setTeams(prev => prev.map(t => t.id === updatedTeam.id ? updatedTeam : t));
  };

  const handleTeamDeleted = (teamId: string) => {
    setTeams(prev => prev.filter(t => t.id !== teamId));
    if (selectedTeamId === teamId) {
      setSelectedTeamId(null);
    }
  };

  const handleTeamLeft = (teamId: string) => {
    setTeams(prev => prev.filter(t => t.id !== teamId));
    if (selectedTeamId === teamId) {
      setSelectedTeamId(null);
    }
  };

  const handleCreateCollection = async (name: string, teamId: string | null) => {
    const newCollection = await createCollection(name, teamId || undefined);
    setCollections(prev => [newCollection, ...prev]);
  };

  // State for selected video's collection IDs
  const [selectedVideoCollectionIds, setSelectedVideoCollectionIds] = useState<string[]>([]);

  // Load collections for selected video
  const loadSelectedVideoCollections = async (awemeId: string) => {
    try {
      const collectionIds = await fetchVideoCollections(awemeId);
      setSelectedVideoCollectionIds(collectionIds);
    } catch (err) {
      console.error('Failed to load video collections:', err);
      setSelectedVideoCollectionIds([]);
    }
  };

  // Toggle video in collection
  const handleToggleVideoCollection = async (collectionId: string) => {
    if (!selectedLibraryItem?.platform_id) return;

    const awemeId = selectedLibraryItem.platform_id;
    const isInCollection = selectedVideoCollectionIds.includes(collectionId);

    try {
      if (isInCollection) {
        await removeVideoFromCollection(collectionId, awemeId);
        setSelectedVideoCollectionIds(prev => prev.filter(id => id !== collectionId));
      } else {
        await addVideoToCollection(collectionId, awemeId);
        setSelectedVideoCollectionIds(prev => [...prev, collectionId]);
      }
    } catch (err) {
      console.error('Failed to toggle video collection:', err);
    }
  };

  const handleUpdateSettings = async (newSettings: UserSettings) => {
    // Check if critical DB config changed
    const dbChanged =
      newSettings.supabaseUrl !== userSettings.supabaseUrl ||
      newSettings.supabaseAnonKey !== userSettings.supabaseAnonKey;

    try {
      // 保存 Supabase 配置到后端 YAML 文件
      await saveFrontendConfig({
        supabase_url: newSettings.supabaseUrl || undefined,
        supabase_anon_key: newSettings.supabaseAnonKey || undefined,
        default_download_path: newSettings.downloadPath,
      });

      // Save download_path to Supabase (for per-user settings)
      if (isAuthenticated && isSupabaseConfigured()) {
        try {
          await saveUserSettings({
            download_path: newSettings.downloadPath
          });
        } catch (err) {
          console.error('Failed to save settings to Supabase:', err);
        }
      }

      setUserSettings(newSettings);

      if (dbChanged) {
        if (confirm("Database configuration changed. Application must reload to apply changes. Reload now?")) {
          window.location.reload();
        }
      } else {
        alert("Settings saved successfully!");
      }
    } catch (err) {
      console.error('Failed to save settings:', err);
      alert("Failed to save settings. Please try again.");
    }
  };

  const loadLibraryData = async () => {
    setIsLoadingLibrary(true);
    setLibraryError(null);
    // Reset pagination state
    setCurrentPage(0);
    setHasMoreData(true);
    try {
      let data: Video[];
      if (isSupabaseConfigured()) {
        // Load first page with pagination
        const result = await fetchLibraryPaginated(0);
        data = result.data;
        setLibrary(data);
        setHasMoreData(result.hasMore);
        setCurrentPage(0);
        if (data.length === 0) {
           console.log("Supabase connected but returned no data. You may need to create the 'videos' table.");
        }
      } else {
        // Fallback to mock if not configured
        data = MOCK_LIBRARY;
        setLibrary(data);
        setHasMoreData(false);
      }
      // Calculate dashboard stats from library data
      const stats = await fetchDashboardStats(data);
      setDashboardStats(stats);
    } catch (err: any) {
      console.error("Failed to load library:", err);
      // Fallback to mock on error (e.g. table doesn't exist yet)
      setLibrary(MOCK_LIBRARY);
      setHasMoreData(false);
      // Calculate dashboard stats even from mock data
      const stats = await fetchDashboardStats(MOCK_LIBRARY);
      setDashboardStats(stats);
      // Display a more useful error message than [object Object]
      const errorMessage = err?.message || (typeof err === 'object' ? JSON.stringify(err) : String(err));
      setLibraryError(`Could not fetch real data (${errorMessage}). Using local cache.`);
    } finally {
      setIsLoadingLibrary(false);
    }
  };

  // Load more library data for infinite scroll
  const loadMoreLibrary = async () => {
    if (!hasMoreData || isLoadingMore || !isSupabaseConfigured()) return;

    setIsLoadingMore(true);
    try {
      const nextPage = currentPage + 1;
      const result = await fetchLibraryPaginated(nextPage);

      if (result.data.length > 0) {
        setLibrary(prev => [...prev, ...result.data]);
        setCurrentPage(nextPage);
        setHasMoreData(result.hasMore);
      } else {
        setHasMoreData(false);
      }
    } catch (err) {
      console.error("Failed to load more library data:", err);
    } finally {
      setIsLoadingMore(false);
    }
  };

  // Intersection Observer for infinite scroll
  useEffect(() => {
    if (!loadMoreRef.current) return;

    const observer = new IntersectionObserver(
      (entries) => {
        const [entry] = entries;
        if (entry.isIntersecting && hasMoreData && !isLoadingMore && !isSearchActive) {
          loadMoreLibrary();
        }
      },
      { threshold: 0.1, rootMargin: '100px' }
    );

    observer.observe(loadMoreRef.current);
    return () => observer.disconnect();
  }, [hasMoreData, isLoadingMore, isSearchActive, currentPage]);

  // Helper to add log with timestamp
  const addLog = (msg: string, type: 'info' | 'success' | 'warning' | 'error' = 'info') => {
    const time = new Date().toLocaleTimeString('en-US', {hour12: false, hour: "numeric", minute: "numeric", second: "numeric"});
    setSocketLogs(prev => [...prev, {
      id: Math.random().toString(36).substr(2, 9),
      time,
      message: msg,
      type
    }]);
  };

  // Download progress hook for progressive download tracking
  const {
    status: downloadStatus,
    percent: downloadPercent,
    speed: downloadSpeed,
  } = useDownloadProgress(downloadTaskId, {
    onComplete: async () => {
      addLog("Download completed successfully!", 'success');
      setTaskProgress(100);
      setTaskStatus('Completed');
      // Refresh library to get the updated item with download paths
      loadLibraryData();
      // Also refresh currentResult to update video player with download_path
      if (currentResult?.platform_id) {
        const updatedVideo = await fetchVideoByPlatformId(currentResult.platform_id);
        if (updatedVideo) {
          setCurrentResult(updatedVideo);
        }
      }
    },
    onError: (error) => {
      addLog(`Download failed: ${error}`, 'error');
    },
  });

  const handleParse = async () => {
    if (parserMode === 'batch') {
      await handleBatchParse();
    } else {
      await handleSingleParse();
    }
  };

  const handleSingleParse = async () => {
    if (!urlInput) return;

    // Reset State
    setIsParsing(true);
    setCurrentResult(null);
    setError(null);
    setSocketLogs([]);
    setTaskProgress(0);
    setTaskStatus('Initializing');
    setDownloadTaskId(null); // Reset download task ID

    try {
      // Step 1: Initialize Connection
      addLog("Connecting to backend API...");
      setTaskStatus('Connecting');
      setTaskProgress(10);

      // Step 2: Call Backend API
      addLog(`Sending request to parse: ${urlInput.substring(0, 35)}...`);
      setTaskStatus('Analyzing');
      setTaskProgress(30);

      const response = await parseShareLink(urlInput, {
        video_bool: downloadOptions.video,
        music_bool: downloadOptions.audio,
        cover_bool: downloadOptions.cover,
      });

      addLog("Backend received the request", 'success');
      setTaskProgress(50);

      if (response.success) {
        addLog(`Video parsed: ${response.title || response.platform_id}`, 'success');
        if (response.fallback_used) {
          addLog(`LightHTTP failed, used fallback: ${response.parse_method_name}`, 'warning');
        } else {
          addLog(`Parse method: ${response.parse_method_name || 'Unknown'}`, 'info');
        }
        addLog(`Author: ${response.author || 'Unknown'}`, 'info');

        // Create result from response data immediately (progressive: show metadata first)
        const parsedResult: Video = {
          id: response.id,  // Database ID for tag operations
          platform_id: response.platform_id,
          title: response.title,
          author: response.author,
          media_type: response.media_type,
          original_url: response.original_url || urlInput,
          // URLs
          video_download_urls: response.video_download_urls || [],
          cover_urls: response.cover_urls || [],
          image_download_urls: response.image_download_urls || [],
          // Stats
          like_count: response.like_count || 0,
          comment_count: response.comment_count || 0,
          share_count: response.share_count || 0,
          favorite_count: response.favorite_count || 0,
          // Video info
          duration: response.duration || "0",
          published_at: response.published_at,
          description: response.description,
          resolution: response.resolution,
          video_download_status: response.video_download_status as DownloadStatus || DownloadStatus.PENDING,
        };

        // Show metadata immediately
        setCurrentResult(parsedResult);

        // Add selected tags to the parsed video
        if (selectedTagIds.length > 0 && response.id) {
          try {
            await addTagsToVideo(response.id, selectedTagIds);
            addLog(`Added ${selectedTagIds.length} tag(s) to video`, 'success');
            setSelectedTagIds([]);  // Clear after adding
          } catch (tagError) {
            console.error("Failed to add tags:", tagError);
            addLog("Warning: Failed to add tags to video", 'warning');
          }
        }

        // Check if we have a download task to track
        if (response.download_task_id) {
          addLog("Download task submitted to background queue", 'info');
          addLog(`Tracking download progress: ${response.download_task_id}`, 'info');
          setTaskStatus('Downloading');
          setTaskProgress(60);
          // Set download task ID to start progress polling
          setDownloadTaskId(response.download_task_id);
        } else {
          // No download task, complete immediately (e.g., already downloaded or no download needed)
          setTaskProgress(100);
          setTaskStatus('Completed');
          addLog("Task completed successfully!", 'success');
          // Refresh library to get latest data
          await loadLibraryData();
        }
      } else {
        throw new Error(response.message || "Parse failed");
      }

    } catch (err: any) {
      setError(err.message || "Failed to parse link");
      addLog(`Error: ${err.message}`, 'error');
      setTaskStatus('Failed');
      setTaskProgress(0);
    } finally {
      setIsParsing(false);
    }
  };

  const handleBatchParse = async () => {
    const links = batchInput.split(/\r?\n/).filter(line => line.trim().length > 0);
    if (links.length === 0) return;

    setIsParsing(true);
    setBatchResults([]);
    setCurrentResult(null);
    setError(null);
    setSocketLogs([]);
    setTaskProgress(0);
    setTaskStatus('Batch Mode Active');

    try {
      addLog(`Starting batch job. Found ${links.length} links to process.`);
      setTaskProgress(10);

      // Call backend batch API
      addLog("Sending batch request to backend...");
      setTaskStatus('Processing Batch');

      const response = await parseBatchLinks(links, {
        video_bool: downloadOptions.video,
        music_bool: downloadOptions.audio,
        cover_bool: downloadOptions.cover,
      });

      setTaskProgress(50);

      // Log results
      addLog(`Backend processed ${response.total} links`, 'info');
      addLog(`Successfully submitted: ${response.submitted}`, 'success');

      if (response.failed > 0) {
        addLog(`Failed: ${response.failed}`, 'warning');
        response.errors.forEach((err) => {
          addLog(`  - ${err.url.substring(0, 30)}...: ${err.error}`, 'error');
        });
      }

      // 直接从响应中提取完整数据
      const batchData: Video[] = [];
      response.results.forEach((result: any) => {
        addLog(`  ✓ ${result.platform_id}: ${result.status}`, 'success');
        if (result.data) {
          batchData.push(result.data as Video);
        }
      });

      setTaskProgress(80);

      // 设置批量结果（直接使用返回的数据）
      setBatchResults(batchData);

      // 后台刷新 library（Realtime 也会自动同步）
      loadLibraryData();

      setTaskProgress(100);
      setTaskStatus('Batch Job Completed');
      addLog(`Batch processing finished. ${batchData.length}/${response.total} successful.`, 'success');

    } catch (err: any) {
      setError(err.message || "Batch processing failed");
      addLog(`Error: ${err.message}`, 'error');
      setTaskStatus('Failed');
    } finally {
      setIsParsing(false);
    }
  };

  const handleSaveToLibrary = async (item: Video, silent = false, tagIds?: string[]) => {
    // Preserve existing notes/tags if they exist, otherwise init as empty
    const newItem = {
      ...item,
      notes: item.notes || '',
      tags: item.tags || []
    };

    try {
      // Optimistic update
      setLibrary(prev => {
        // Check if exists
        if (prev.find(i => i.platform_id === item.platform_id)) return prev;
        return [newItem, ...prev];
      });

      if (isSupabaseConfigured()) {
        const savedItem = await saveItem(newItem);

        // Add selected tags to the saved video
        const tagsToAdd = tagIds || selectedTagIds;
        if (tagsToAdd.length > 0 && savedItem.id) {
          try {
            await addTagsToVideo(savedItem.id, tagsToAdd);
            // Clear selected tags after successful save
            setSelectedTagIds([]);
          } catch (tagError) {
            console.error("Failed to add tags:", tagError);
            // Don't fail the whole save if tag assignment fails
          }
        }

        if (!silent) alert("Saved to Cloud Collection!");
      } else {
        if (!silent) alert("Saved to Local Collection (Supabase not configured)");
      }
    } catch (err) {
      console.error("Save failed:", err);
      if (!silent) alert("Failed to save to cloud database.");
    }
  };

  const handleBatchSave = async () => {
    if (batchResults.length === 0) return;
    
    let savedCount = 0;
    for (const item of batchResults) {
        await handleSaveToLibrary(item, true);
        savedCount++;
    }
    alert(`Successfully saved ${savedCount} items to your library!`);
  };

  const handleUpdateLibraryItem = async (id: string, updates: Partial<Video>) => {
    // Optimistic update
    setLibrary(prev => prev.map(item =>
      item.platform_id === id ? { ...item, ...updates } : item
    ));

    try {
      if (isSupabaseConfigured()) {
        await updateItem(id, updates);
      }
    } catch (err) {
      console.error("Update failed:", err);
    }
  };

  const handleDeleteLibraryItem = async (id: string, deleteFiles: boolean) => {
    try {
      if (isSupabaseConfigured()) {
        await deleteItem(id, deleteFiles);
        // Remove from library state
        setLibrary(prev => prev.filter(item => item.platform_id !== id));
        // Clear selection if this item was selected
        if (selectedLibraryItem?.platform_id === id) {
          setSelectedLibraryItem(null);
        }
      }
    } catch (err) {
      console.error("Delete failed:", err);
      throw err; // Re-throw to let the UI handle the error
    }
  };

  const handleMobileLibraryClick = () => {
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

  const handleMobileNavClick = (targetView: ViewState) => {
    setView(targetView);
    setIsMobileMenuOpen(false);
  };

  // Filter and sort library - 默认按添加时间降序（最新在前）
  const filteredLibrary = React.useMemo(() => {
    // If semantic search is active, filter by search results
    if (isSearchActive) {
      // If search returned no results, return empty array
      if (searchResults.length === 0) {
        return [];
      }
      const searchAwemeIds = new Set(searchResults.map(r => r.platform_id));
      return library
        .filter(item => searchAwemeIds.has(item.platform_id))
        .sort((a, b) => {
          // Sort by search relevance (similarity score)
          const aScore = searchResults.find(r => r.platform_id === a.platform_id)?.similarity_score || 0;
          const bScore = searchResults.find(r => r.platform_id === b.platform_id)?.similarity_score || 0;
          return bScore - aScore;
        });
    }

    return library
      .filter(item => {
        // Filter by specific collection if selected
        if (activeCollectionId && !collectionVideoIds.includes(item.platform_id)) {
          return false;
        }
        // Filter by team library (all team collections) if active but no specific collection
        if (isTeamLibraryActive && !activeCollectionId && !teamLibraryVideoIds.includes(item.platform_id)) {
          return false;
        }
        // Mobile search filter - simple text matching
        if (searchQuery.trim()) {
          const query = searchQuery.toLowerCase().trim();
          const title = (item.title || '').toLowerCase();
          const author = (item.author_nickname || '').toLowerCase();
          const desc = (item.description || '').toLowerCase();
          const tags = (item.video_tag || []).join(' ').toLowerCase();
          return title.includes(query) || author.includes(query) || desc.includes(query) || tags.includes(query);
        }
        return true;
      })
      .sort((a, b) => {
        // 按 created_at 降序排序（最新在前）
        const aTime = a.created_at ? new Date(a.created_at).getTime() : 0;
        const bTime = b.created_at ? new Date(b.created_at).getTime() : 0;
        return bTime - aTime;
      });
  }, [library, isSearchActive, searchResults, activeCollectionId, collectionVideoIds, isTeamLibraryActive, teamLibraryVideoIds, searchQuery]);

  // Calculate main content classes based on view to handle mobile padding
  // Added md:pt-20 to account for the fixed header on desktop
  const mainContentClass = `flex-1 md:ml-64 w-full ${
    view === 'library'
      ? 'p-0 md:p-8 md:pt-20 pb-20 md:pb-8' // No padding on mobile library to allow edge-to-edge feed
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
    <div className="flex min-h-screen bg-black text-zinc-100 font-sans selection:bg-indigo-500/30">
      
      {/* User Profile Modal */}
      <UserProfileModal
        isOpen={isProfileModalOpen}
        onClose={() => setIsProfileModalOpen(false)}
        user={userProfile}
        onSave={(updated) => setUserProfile(updated)}
        onLogout={handleLogout}
      />

      {/* Header - Desktop Only */}
      <Header
        userProfile={{
          name: userProfile.name,
          email: userProfile.email,
          avatarUrl: userProfile.avatarUrl,
        }}
        unreadCount={notifications.filter(n => !n.read).length}
        onNotificationClick={() => setIsNotificationPanelOpen(!isNotificationPanelOpen)}
        onUserClick={() => setIsUserDropdownOpen(true)}
      />

      {/* Notification Panel - positioned relative to header */}
      {isNotificationPanelOpen && (
        <div className="hidden md:block fixed top-16 right-4 z-50">
          <NotificationPanel
            isOpen={isNotificationPanelOpen}
            onClose={() => setIsNotificationPanelOpen(false)}
            notifications={notifications.map(n => ({
              id: n.id,
              type: n.type,
              title: n.title,
              content: n.content || undefined,
              createdAt: n.created_at,
              read: n.read,
            }))}
            onMarkRead={handleMarkNotificationRead}
            onMarkAllRead={handleMarkAllNotificationsRead}
          />
        </div>
      )}

      {/* User Dropdown */}
      <UserDropdown
        isOpen={isUserDropdownOpen}
        onClose={() => setIsUserDropdownOpen(false)}
        user={{
          name: userProfile.name,
          email: userProfile.email,
          avatarUrl: userProfile.avatarUrl,
        }}
        teams={teams.map(t => ({
          id: t.id,
          name: t.name,
          isOwner: t.owner_id === currentUserId,
        }))}
        activeTeamId={selectedTeamId}
        onTeamSelect={(teamId) => {
          setSelectedTeamId(teamId);
          // Reset collection when switching teams
          setActiveCollectionId(null);
        }}
        onCreateTeam={handleCreateTeam}
        onTeamSettings={handleTeamSettings}
        onProfile={() => {
          setIsUserDropdownOpen(false);
          setSettingsModalInitialTab('personal');
          setIsSettingsModalOpen(true);
        }}
        onAccount={() => {
          setIsUserDropdownOpen(false);
          setSettingsModalInitialTab('personal');
          setIsSettingsModalOpen(true);
        }}
        onLogout={handleLogout}
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
        initialTab={settingsModalInitialTab}
        currentTeamId={selectedTeamId}
        currentTeamName={teams.find(t => t.id === selectedTeamId)?.name}
        isTeamOwner={teams.find(t => t.id === selectedTeamId)?.owner_id === currentUserId}
        user={{
          id: currentUserId || '',
          name: userProfile.name,
          email: userProfile.email,
          avatarUrl: userProfile.avatarUrl,
          bio: '', // TODO: Add bio to userProfile if needed
        }}
        onUserUpdated={() => {
          // Refresh user profile
        }}
        onTeamDeleted={() => {
          if (selectedTeamId) {
            handleTeamDeleted(selectedTeamId);
          }
          setIsSettingsModalOpen(false);
        }}
        onTeamLeft={() => {
          if (selectedTeamId) {
            handleTeamLeft(selectedTeamId);
          }
          setIsSettingsModalOpen(false);
        }}
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
        
        {/* 3. Dashboard */}
        <button 
          onClick={() => handleMobileNavClick('dashboard')} 
          className={`flex flex-col items-center gap-1 transition-colors ${view === 'dashboard' ? 'text-indigo-400' : 'text-zinc-500 hover:text-zinc-300'}`}
        >
          <LayoutDashboard size={24}/>
        </button>

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
      <aside className="hidden md:flex flex-col w-64 border-r border-zinc-800 bg-zinc-950 p-6 fixed h-full z-10">
        <div className="flex items-center gap-3 mb-10 px-2">
          <div className="w-8 h-8 bg-gradient-to-br from-indigo-500 to-purple-600 rounded-lg flex items-center justify-center shadow-lg shadow-indigo-500/20">
            <Sparkles className="w-5 h-5 text-white" />
          </div>
          <span className="text-xl font-bold tracking-tight">MediaHub</span>
        </div>

        <nav className="flex-1 space-y-2">
          <SidebarItem
            icon={Search}
            label={t('nav.linkParser')}
            active={view === 'parser'}
            onClick={() => setView('parser')}
          />
          {/* Library with submenu (unified My Library + Team Library) */}
          <div className="space-y-1">
            <SidebarItem
              icon={Library}
              label="Library"
              active={view === 'library'}
              onClick={() => {
                setView('library');
                if (!isLibraryOpen) {
                  toggleLibraryMenu();
                }
              }}
              hasSubmenu
              isOpen={isLibraryOpen}
            />

            {/* Library Sub-menu - Smart Collections */}
            {isLibraryOpen && (
              <div className="ml-9 border-l border-zinc-800 space-y-1 animate-in slide-in-from-left-2 duration-200">
                {/* Smart Collections inline */}
                <SmartCollectionsSidebar
                  activeCollectionId={activeSmartCollectionId}
                  onSelectCollection={(collection) => {
                    // Clear search state when selecting a smart collection
                    setIsSearchActive(false);
                    setSearchResults([]);
                    setSearchQueryText('');
                    setActiveSmartCollectionId(collection?.id || null);
                    setView('library');
                    setActiveCollectionId(null);
                    setActiveLibraryTab('my-library');
                  }}
                  isInline
                />

                {/* Storage Cleanup */}
                <button
                  onClick={() => setView('cleanup')}
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
            onClick={() => setView('dashboard')}
          />

          {/* Settings with submenu */}
          <div className="space-y-1">
            <SidebarItem
              icon={Settings}
              label={t('nav.settings')}
              active={view === 'settings'}
              onClick={() => {
                toggleSettingsMenu();
                if (!isSettingsOpen) {
                  setView('settings');
                  if (settingsTab === 'api') setSettingsTab('general');
                }
              }}
              hasSubmenu
              isOpen={isSettingsOpen}
            />

            {/* Settings Sub-menu */}
            {isSettingsOpen && (
              <div className="ml-9 border-l border-zinc-800 space-y-1 animate-in slide-in-from-left-2 duration-200">
                <button
                  onClick={() => { setView('settings'); setSettingsTab('general'); }}
                  className={`w-full text-left px-4 py-2 text-sm rounded-r-lg transition-colors ${
                    view === 'settings' && settingsTab === 'general'
                      ? 'text-indigo-400 bg-indigo-500/5'
                      : 'text-zinc-500 hover:text-zinc-300'
                  }`}
                >
                  <div className="flex items-center gap-2">
                    <FolderOpen size={14} />
                    <span>General</span>
                  </div>
                </button>
                <button
                  onClick={() => { setView('settings'); setSettingsTab('api'); }}
                  className={`w-full text-left px-4 py-2 text-sm rounded-r-lg transition-colors ${
                    view === 'settings' && settingsTab === 'api'
                      ? 'text-indigo-400 bg-indigo-500/5'
                      : 'text-zinc-500 hover:text-zinc-300'
                  }`}
                >
                  <div className="flex items-center gap-2">
                    <Key size={14} />
                    <span>API Management</span>
                  </div>
                </button>
                <button
                  onClick={() => { setView('settings'); setSettingsTab('logs'); }}
                  className={`w-full text-left px-4 py-2 text-sm rounded-r-lg transition-colors ${
                    view === 'settings' && settingsTab === 'logs'
                      ? 'text-indigo-400 bg-indigo-500/5'
                      : 'text-zinc-500 hover:text-zinc-300'
                  }`}
                >
                  <div className="flex items-center gap-2">
                    <ScrollText size={14} />
                    <span>Logs</span>
                  </div>
                </button>
                <button
                  onClick={() => { setView('settings'); setSettingsTab('tasks'); }}
                  className={`w-full text-left px-4 py-2 text-sm rounded-r-lg transition-colors ${
                    view === 'settings' && settingsTab === 'tasks'
                      ? 'text-indigo-400 bg-indigo-500/5'
                      : 'text-zinc-500 hover:text-zinc-300'
                  }`}
                >
                  <div className="flex items-center gap-2">
                    <ListTodo size={14} />
                    <span>Tasks</span>
                  </div>
                </button>
                <button
                  onClick={() => { setView('settings'); setSettingsTab('tags'); }}
                  className={`w-full text-left px-4 py-2 text-sm rounded-r-lg transition-colors ${
                    view === 'settings' && settingsTab === 'tags'
                      ? 'text-indigo-400 bg-indigo-500/5'
                      : 'text-zinc-500 hover:text-zinc-300'
                  }`}
                >
                  <div className="flex items-center gap-2">
                    <Tag size={14} />
                    <span>Tags</span>
                  </div>
                </button>
                <button
                  onClick={() => { setView('settings'); setSettingsTab('ai'); }}
                  className={`w-full text-left px-4 py-2 text-sm rounded-r-lg transition-colors ${
                    view === 'settings' && settingsTab === 'ai'
                      ? 'text-indigo-400 bg-indigo-500/5'
                      : 'text-zinc-500 hover:text-zinc-300'
                  }`}
                >
                  <div className="flex items-center gap-2">
                    <Sparkles size={14} />
                    <span>AI</span>
                  </div>
                </button>
              </div>
            )}
          </div>
        </nav>

      </aside>

      {/* Main Content */}
      <main className={mainContentClass}>
        
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
              <div className="grid grid-cols-1 md:grid-cols-3 gap-4 mt-8">
                 {/* Queue Status */}
                 <div className="p-6 rounded-2xl bg-zinc-900/50 border border-zinc-800/50 hover:border-zinc-700 transition-colors text-center">
                    <div className={`w-10 h-10 rounded-lg flex items-center justify-center mx-auto mb-3 ${
                      systemStatus?.queue.status === 'offline' ? 'bg-red-900/30 text-red-400' :
                      systemStatus?.queue.active ? 'bg-indigo-900/30 text-indigo-400' : 'bg-zinc-800/50 text-zinc-500'
                    }`}>
                      <ListVideo size={20} />
                    </div>
                    <h4 className="font-semibold text-zinc-200 mb-1">Queue</h4>
                    <p className={`text-sm font-mono ${
                      systemStatus?.queue.status === 'offline' ? 'text-red-400' :
                      systemStatus?.queue.active ? 'text-indigo-400' : 'text-zinc-500'
                    }`}>
                      {systemStatus ? getQueueDisplay(systemStatus.queue) : 'Loading...'}
                    </p>
                    {systemStatus?.queue.pending ? (
                      <p className="text-xs text-zinc-600 mt-1">{systemStatus.queue.pending} pending</p>
                    ) : null}
                 </div>
                 {/* Parse Mode */}
                 <ParseModeCard />
                 {/* Storage Status */}
                 <div className="p-6 rounded-2xl bg-zinc-900/50 border border-zinc-800/50 hover:border-zinc-700 transition-colors text-center">
                    <div className={`w-10 h-10 rounded-lg flex items-center justify-center mx-auto mb-3 ${
                      systemStatus?.storage.status === 'ok' ? 'bg-purple-900/30 text-purple-400' :
                      systemStatus?.storage.status === 'warning' ? 'bg-yellow-900/30 text-yellow-400' :
                      systemStatus?.storage.status === 'critical' ? 'bg-red-900/30 text-red-400' : 'bg-zinc-800/50 text-zinc-500'
                    }`}>
                      <HardDrive size={20} />
                    </div>
                    <h4 className="font-semibold text-zinc-200 mb-1">Storage</h4>
                    <p className={`text-sm font-mono ${
                      systemStatus?.storage.status === 'ok' ? 'text-purple-400' :
                      systemStatus?.storage.status === 'warning' ? 'text-yellow-400' :
                      systemStatus?.storage.status === 'critical' ? 'text-red-400' : 'text-zinc-500'
                    }`}>
                      {systemStatus ? getStorageDisplay(systemStatus.storage) : 'Loading...'}
                    </p>
                    {systemStatus?.storage.percent_used ? (
                      <p className="text-xs text-zinc-600 mt-1">{systemStatus.storage.percent_used}% used</p>
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
                {/* Library Tabs - My Library | Team Library */}
                <div className="hidden md:block">
                  <LibraryTabs
                    activeTab={activeLibraryTab}
                    onTabChange={handleLibraryTabChange}
                    currentTeam={currentTeam}
                    onTeamClick={() => setIsUserDropdownOpen(true)}
                  />
                </div>

                {/* Team Library Folder View - When no collection is selected */}
                {activeLibraryTab === 'team-library' && !activeCollectionId && (
                  <TeamLibraryView
                      collections={collections.filter(c => c.team_id === selectedTeamId)}
                    activeCollectionId={activeCollectionId}
                    onSelectCollection={(id) => {
                      // Clear search state when selecting a collection
                      setIsSearchActive(false);
                      setSearchResults([]);
                      setSearchQueryText('');
                      setActiveCollectionId(id);
                    }}
                    onBackToFolders={() => setActiveCollectionId(null)}
                    onCreateCollection={() => {
                      // Pre-select current team when creating from Team Library
                      setIsCreateCollectionModalOpen(true);
                    }}
                    currentTeam={currentTeam}
                    isLoading={isLoadingLibrary}
                  />
                )}

                {/* Desktop Header - Show when in My Library or inside a collection */}
                {(activeLibraryTab === 'my-library' || activeCollectionId) && (
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
          <div className="space-y-8 animate-in fade-in slide-in-from-bottom-4 duration-500">
             <header className="mb-8">
                <h1 className="text-2xl font-bold text-white">{t('dashboard.title')}</h1>
                <p className="text-zinc-400 text-sm">{t('dashboard.subtitle')}</p>
             </header>

             <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-4 gap-4 mb-8">
                {(() => {
                  // Format storage size
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

      </main>

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
