
import React, { useState, useEffect, useRef } from 'react';
import { 
  LayoutDashboard, Search, Library, Settings, LogOut, 
  Link as LinkIcon, AlertCircle, Loader2, Sparkles, User, Database,
  LayoutGrid, LayoutList, ChevronDown, FolderOpen, Key, Smartphone, X,
  CloudOff, RefreshCw, Terminal, Activity, CheckCircle2,
  ListVideo, Wifi, HardDrive, ArrowLeft, Check, Music, Video, Image as ImageIcon, Tag,
  Layers, Download
} from 'lucide-react';
import { getSupabaseClient, isSupabaseConfigured, reinitializeSupabaseClient, getSupabaseCredentials } from './supabaseClient';
import { DouyinBase, ViewState, UserProfile, UserSettings } from './types';
import { parseShareLink, parseBatchLinks, FetchResponse } from './services/parserService';
import { fetchLibrary, saveItem, updateItem, deleteItem, fetchDashboardStats, DashboardStats, fetchUserSettings, saveUserSettings, fetchFrontendConfig, saveFrontendConfig } from './services/dataService';
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
  status 
}: { 
  logs: LogEntry[], 
  progress: number, 
  status: string 
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
                 <ListVideo size={14} className="text-indigo-400" />
                 <span className="font-mono text-xs font-medium">Active</span>
              </div>
           </div>
           <div className="flex flex-col items-center border-l border-zinc-800/50">
              <span className="text-[10px] text-zinc-500 uppercase tracking-widest mb-1">Network</span>
              <div className="flex items-center gap-1.5 text-zinc-300">
                 <Wifi size={14} className="text-emerald-400" />
                 <span className="font-mono text-xs font-medium">12.5 MB/s</span>
              </div>
           </div>
           <div className="flex flex-col items-center border-l border-zinc-800/50">
              <span className="text-[10px] text-zinc-500 uppercase tracking-widest mb-1">Storage</span>
              <div className="flex items-center gap-1.5 text-zinc-300">
                 <HardDrive size={14} className="text-purple-400" />
                 <span className="font-mono text-xs font-medium">Write OK</span>
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
  const [view, setView] = useState<ViewState>('parser');
  const [settingsTab, setSettingsTab] = useState<'general' | 'api'>('general');
  const [urlInput, setUrlInput] = useState('');
  
  // Parser Configuration State
  const [parserMode, setParserMode] = useState<'single' | 'batch'>('single');
  const [batchInput, setBatchInput] = useState('');
  const [downloadOptions, setDownloadOptions] = useState({
    video: true,
    audio: false,
    cover: true
  });
  const [customTags, setCustomTags] = useState('');
  
  // Parser & Task State
  const [isParsing, setIsParsing] = useState(false);
  const [taskStatus, setTaskStatus] = useState('Idle');
  const [taskProgress, setTaskProgress] = useState(0);
  const [socketLogs, setSocketLogs] = useState<LogEntry[]>([]);
  
  const [currentResult, setCurrentResult] = useState<DouyinBase | null>(null);
  const [batchResults, setBatchResults] = useState<DouyinBase[]>([]);
  
  // Library State
  const [library, setLibrary] = useState<DouyinBase[]>([]);
  const [isLoadingLibrary, setIsLoadingLibrary] = useState(false);
  const [libraryError, setLibraryError] = useState<string | null>(null);
  const [selectedLibraryItem, setSelectedLibraryItem] = useState<DouyinBase | null>(null); // For detail view
  
  const [libraryViewMode, setLibraryViewMode] = useState<'grid' | 'list' | 'feed'>('grid');
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
    downloadPath: '/home/user/downloads/douyin',
    supabaseUrl: '',
    supabaseAnonKey: ''
  });

  const [isProfileModalOpen, setIsProfileModalOpen] = useState(false);

  const [error, setError] = useState<string | null>(null);

  // Auth state
  const [isAuthenticated, setIsAuthenticated] = useState(false);
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

  // Check for existing session after config is loaded
  useEffect(() => {
    if (!isConfigLoaded) return;

    const checkSession = async () => {
      const supabase = getSupabaseClient();
      if (isSupabaseConfigured() && supabase) {
        // 更新 userSettings 中的 Supabase 凭据（从当前配置获取）
        const credentials = getSupabaseCredentials();
        setUserSettings(prev => ({
          ...prev,
          supabaseUrl: credentials.url || prev.supabaseUrl,
          supabaseAnonKey: credentials.anonKey || prev.supabaseAnonKey,
        }));

        const { data: { session } } = await supabase.auth.getSession();
        if (session?.user) {
          setUserProfile(prev => ({
            ...prev,
            name: session.user.email?.split('@')[0] || 'User',
            email: session.user.email || '',
          }));
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
        }
      }
    };
    checkSession();
  }, [isConfigLoaded]);

  // Fetch Library Data on Mount
  useEffect(() => {
    if (isAuthenticated) {
      loadLibraryData();
    }
  }, [isAuthenticated]);

  // Supabase Realtime 订阅 - 自动同步数据库变化
  useEffect(() => {
    const supabase = getSupabaseClient();
    if (!isAuthenticated || !isSupabaseConfigured() || !supabase) {
      return;
    }

    const channel = supabase
      .channel('douyin_videos_realtime')
      .on(
        'postgres_changes',
        {
          event: '*',
          schema: 'public',
          table: 'douyin_videos',
        },
        async (payload) => {
          console.log('Realtime update:', payload.eventType, payload);

          // 获取当前用户 ID
          const { data: { user } } = await supabase.auth.getUser();
          if (!user) return;

          const newRecord = payload.new as DouyinBase;
          const oldRecord = payload.old as DouyinBase;

          // 只处理当前用户的数据
          if (payload.eventType === 'INSERT' && newRecord.user_id === user.id) {
            setLibrary(prev => {
              // 避免重复添加
              if (prev.find(item => item.aweme_id === newRecord.aweme_id)) {
                return prev;
              }
              return [newRecord, ...prev];
            });
          } else if (payload.eventType === 'UPDATE' && newRecord.user_id === user.id) {
            setLibrary(prev =>
              prev.map(item =>
                item.aweme_id === newRecord.aweme_id ? newRecord : item
              )
            );
            // 如果当前选中的项被更新，也更新它
            setSelectedLibraryItem(prev =>
              prev?.aweme_id === newRecord.aweme_id ? newRecord : prev
            );
            // 如果当前解析结果被更新，也更新它
            setCurrentResult(prev =>
              prev?.aweme_id === newRecord.aweme_id ? newRecord : prev
            );
          } else if (payload.eventType === 'DELETE' && oldRecord?.user_id === user.id) {
            setLibrary(prev =>
              prev.filter(item => item.aweme_id !== oldRecord.aweme_id)
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

  const handleLogin = (user: { email: string; id: string }) => {
    setUserProfile(prev => ({
      ...prev,
      name: user.email.split('@')[0] || 'User',
      email: user.email,
    }));
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
    setUserProfile({
      name: 'Demo User',
      email: 'demo@example.com',
      avatarUrl: '',
      plan: 'Free Plan'
    });
    // When logging out, we go back to landing page.
    // Ensure modal is closed
    setShowAuthModal(false);
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
    try {
      let data: DouyinBase[];
      if (isSupabaseConfigured()) {
        data = await fetchLibrary();
        setLibrary(data);
        if (data.length === 0) {
           console.log("Supabase connected but returned no data. You may need to create the 'douyin_videos' table.");
        }
      } else {
        // Fallback to mock if not configured
        data = MOCK_LIBRARY;
        setLibrary(data);
      }
      // Calculate dashboard stats from library data
      const stats = await fetchDashboardStats(data);
      setDashboardStats(stats);
    } catch (err: any) {
      console.error("Failed to load library:", err);
      // Fallback to mock on error (e.g. table doesn't exist yet)
      setLibrary(MOCK_LIBRARY);
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
        video_categories: customTags || undefined,
      });

      addLog("Backend received the request", 'success');
      setTaskProgress(50);

      if (response.success) {
        addLog(`Video parsed: ${response.video_title || response.aweme_id}`, 'success');
        addLog(`Author: ${response.author || 'Unknown'}`, 'info');
        addLog("Download task submitted to background queue", 'info');
        setTaskProgress(80);

        // Backend handles download in background, refresh library to get data
        setTaskStatus('Processing');
        addLog("Refreshing library data...", 'info');

        // Wait a moment for backend to save initial data
        await new Promise(r => setTimeout(r, 1000));
        await loadLibraryData();

        setTaskProgress(100);
        setTaskStatus('Completed');
        addLog("Task completed successfully!", 'success');

        // Find the newly added item in library
        const newItem = library.find(item => item.aweme_id === response.aweme_id);
        if (newItem) {
          setCurrentResult(newItem);
        } else {
          // Create result from response data (now includes full parsed data)
          setCurrentResult({
            aweme_id: response.aweme_id,
            video_title: response.video_title,
            author: response.author,
            aweme_type: response.aweme_type,
            video_original_url: response.video_original_url || urlInput,
            // URLs
            video_download_urls: response.video_download_urls || [],
            cover_urls: response.cover_urls || [],
            image_download_urls: response.image_download_urls || [],
            // Stats
            video_digg_count: response.video_digg_count || 0,
            video_comment_count: response.video_comment_count || 0,
            video_share_count: response.video_share_count || 0,
            video_collect_count: response.video_collect_count || 0,
            // Video info
            video_duration: response.video_duration || "0",
            video_created_time: response.video_created_time,
            video_desc: response.video_desc,
            video_categories: response.video_categories,
            video_resolution: response.video_resolution,
            video_download_status: response.video_download_status as DownloadStatus || DownloadStatus.PENDING,
          } as DouyinBase);
        }
      } else {
        throw new Error(response.message || "解析失败");
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
        video_categories: customTags || undefined,
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
      const batchData: DouyinBase[] = [];
      response.results.forEach((result: any) => {
        addLog(`  ✓ ${result.aweme_id}: ${result.status}`, 'success');
        if (result.data) {
          batchData.push(result.data as DouyinBase);
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

  const handleSaveToLibrary = async (item: DouyinBase, silent = false) => {
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
        if (prev.find(i => i.aweme_id === item.aweme_id)) return prev;
        return [newItem, ...prev];
      });

      if (isSupabaseConfigured()) {
        await saveItem(newItem);
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

  const handleUpdateLibraryItem = async (id: string, updates: Partial<DouyinBase>) => {
    // Optimistic update
    setLibrary(prev => prev.map(item =>
      item.aweme_id === id ? { ...item, ...updates } : item
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
        setLibrary(prev => prev.filter(item => item.aweme_id !== id));
        // Clear selection if this item was selected
        if (selectedLibraryItem?.aweme_id === id) {
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
  const filteredLibrary = library
    .filter(item => {
      if (!searchQuery) return true;
      const lowerQuery = searchQuery.toLowerCase();
      return (
        item.video_title?.toLowerCase().includes(lowerQuery) ||
        item.author?.toLowerCase().includes(lowerQuery) ||
        item.notes?.toLowerCase().includes(lowerQuery) ||
        item.tags?.some(tag => tag.toLowerCase().includes(lowerQuery)) ||
        item.video_categories?.toLowerCase().includes(lowerQuery) ||
        item.video_desc?.toLowerCase().includes(lowerQuery)
      );
    })
    .sort((a, b) => {
      // 按 created_at 降序排序（最新在前）
      const aTime = a.created_at ? new Date(a.created_at).getTime() : 0;
      const bTime = b.created_at ? new Date(b.created_at).getTime() : 0;
      return bTime - aTime;
    });

  // Calculate main content classes based on view to handle mobile padding
  const mainContentClass = `flex-1 md:ml-64 w-full ${
    view === 'library' 
      ? 'p-0 md:p-8 pb-20 md:pb-8' // No padding on mobile library to allow edge-to-edge feed
      : 'p-4 md:p-8 pb-24 md:pb-8'
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
    <div className="flex min-h-screen bg-black text-zinc-100 font-sans selection:bg-indigo-500/30">
      
      {/* User Profile Modal */}
      <UserProfileModal 
        isOpen={isProfileModalOpen} 
        onClose={() => setIsProfileModalOpen(false)}
        user={userProfile}
        onSave={(updated) => setUserProfile(updated)}
        onLogout={handleLogout}
      />

      {/* Mobile Nav */}
      <div className="md:hidden fixed bottom-0 left-0 right-0 bg-zinc-950/90 backdrop-blur-xl border-t border-zinc-800 flex justify-around p-4 z-40 pb-6">
        
        {/* Mobile Popup Menu for View Selection */}
        {isMobileMenuOpen && view === 'library' && !selectedLibraryItem && (
           <div className="absolute bottom-20 left-1/2 -translate-x-1/2 bg-zinc-800/90 backdrop-blur-md border border-zinc-700 p-1.5 rounded-xl shadow-2xl flex gap-1 z-50 animate-in slide-in-from-bottom-2 fade-in duration-200">
              <button 
                onClick={() => { setLibraryViewMode('list'); setIsMobileMenuOpen(false); }}
                className={`p-2 rounded-lg transition-all ${libraryViewMode === 'list' ? 'bg-zinc-200 text-zinc-900 shadow-sm' : 'text-zinc-400 hover:text-zinc-200 hover:bg-zinc-700/50'}`}
                title="Table View"
              >
                <LayoutList size={20} />
              </button>
              <button 
                onClick={() => { setLibraryViewMode('grid'); setIsMobileMenuOpen(false); }}
                className={`p-2 rounded-lg transition-all ${libraryViewMode === 'grid' ? 'bg-zinc-200 text-zinc-900 shadow-sm' : 'text-zinc-400 hover:text-zinc-200 hover:bg-zinc-700/50'}`}
                title="Grid View"
              >
                <LayoutGrid size={20} />
              </button>
              <button 
                onClick={() => { setLibraryViewMode('feed'); setIsMobileMenuOpen(false); }}
                className={`p-2 rounded-lg transition-all ${libraryViewMode === 'feed' ? 'bg-zinc-200 text-zinc-900 shadow-sm' : 'text-zinc-400 hover:text-zinc-200 hover:bg-zinc-700/50'}`}
                title="Feed View"
              >
                <Smartphone size={20} />
              </button>
              {/* Little triangle arrow pointing down */}
              <div className="absolute -bottom-1.5 left-1/2 -translate-x-1/2 w-3 h-3 bg-zinc-800 border-r border-b border-zinc-700 rotate-45 transform"></div>
           </div>
        )}

        {/* 1. Parser */}
        <button 
          onClick={() => handleMobileNavClick('parser')} 
          className={`flex flex-col items-center gap-1 transition-colors ${view === 'parser' ? 'text-indigo-400' : 'text-zinc-500 hover:text-zinc-300'}`}
        >
          <Search size={24}/>
        </button>
        
        {/* 2. Library */}
        <button 
          onClick={handleMobileLibraryClick} 
          className={`flex flex-col items-center gap-1 transition-colors relative ${view === 'library' ? 'text-indigo-400' : 'text-zinc-500 hover:text-zinc-300'}`}
        >
          <Library size={24}/>
          {/* Active Indicator dot */}
          {view === 'library' && isMobileMenuOpen && (
             <span className="absolute -top-1 right-0 w-2 h-2 bg-indigo-500 rounded-full animate-pulse"></span>
          )}
        </button>
        
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
            label="Link Parser" 
            active={view === 'parser'} 
            onClick={() => setView('parser')} 
          />
          <SidebarItem 
            icon={Library} 
            label="My Library" 
            active={view === 'library'} 
            onClick={() => setView('library')} 
          />
          <SidebarItem 
            icon={LayoutDashboard} 
            label="Dashboard" 
            active={view === 'dashboard'} 
            onClick={() => setView('dashboard')} 
          />
          
          <div className="space-y-1">
             <SidebarItem 
              icon={Settings} 
              label="Settings" 
              active={view === 'settings'} 
              onClick={() => {
                setView('settings');
                // Default to general when clicking parent
                if (settingsTab === 'api') setSettingsTab('general');
              }}
              hasSubmenu
              isOpen={view === 'settings'}
            />
            
            {/* Settings Sub-menu */}
            {view === 'settings' && (
              <div className="ml-9 border-l border-zinc-800 space-y-1 animate-in slide-in-from-left-2 duration-200">
                <button 
                  onClick={() => setSettingsTab('general')}
                  className={`w-full text-left px-4 py-2 text-sm rounded-r-lg transition-colors ${
                    settingsTab === 'general' 
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
                  onClick={() => setSettingsTab('api')}
                  className={`w-full text-left px-4 py-2 text-sm rounded-r-lg transition-colors ${
                    settingsTab === 'api' 
                      ? 'text-indigo-400 bg-indigo-500/5' 
                      : 'text-zinc-500 hover:text-zinc-300'
                  }`}
                >
                  <div className="flex items-center gap-2">
                    <Key size={14} />
                    <span>API Management</span>
                  </div>
                </button>
              </div>
            )}
          </div>
        </nav>

        <div className="mt-auto pt-6 border-t border-zinc-800">
           {/* Clickable User Profile Area */}
           <button 
             onClick={() => setIsProfileModalOpen(true)}
             className="flex items-center gap-3 px-2 py-2 mb-2 w-full hover:bg-zinc-900 rounded-lg transition-colors group text-left"
           >
              <div className="w-8 h-8 rounded-full bg-zinc-800 flex items-center justify-center text-zinc-400 overflow-hidden border border-zinc-700">
                {userProfile.avatarUrl ? (
                   <img src={userProfile.avatarUrl} alt="Avatar" className="w-full h-full object-cover" />
                ) : (
                   <User size={16} />
                )}
              </div>
              <div className="flex-1 overflow-hidden">
                <p className="text-sm font-medium truncate text-zinc-200 group-hover:text-white transition-colors">{userProfile.name}</p>
                <p className="text-xs text-zinc-500">{userProfile.plan}</p>
              </div>
           </button>
           
           <button 
              onClick={handleLogout}
              className="flex items-center gap-2 text-xs text-zinc-500 hover:text-zinc-300 px-2 transition-colors w-full"
           >
             <LogOut size={14} />
             <span>Sign Out</span>
           </button>
        </div>
      </aside>

      {/* Main Content */}
      <main className={mainContentClass}>
        
        {/* VIEW: PARSER */}
        {view === 'parser' && (
          <div className="max-w-3xl mx-auto space-y-8 animate-in fade-in slide-in-from-bottom-4 duration-500">
            <div className="text-center space-y-2 mb-6">
              <h1 className="text-3xl md:text-4xl font-bold bg-clip-text text-transparent bg-gradient-to-r from-indigo-400 to-purple-400">
                Douyin Media Parser
              </h1>
              <p className="text-zinc-400">
                Paste shared links to analyze and download content watermark-free.
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
                    <span>Single Link</span>
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
                    <span>Batch Download</span>
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
                      placeholder="Paste Douyin link here (e.g., https://v.douyin.com/...)" 
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
                      {isParsing && taskProgress < 100 ? <Loader2 className="animate-spin w-4 h-4" /> : 'Analyze'}
                    </button>
                  </div>
                ) : (
                  <div className="flex flex-col">
                    <textarea 
                      placeholder={`Paste multiple Douyin links here (one per line)...\nExample:\nhttps://v.douyin.com/...\nhttps://v.douyin.com/...`}
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
                        {isParsing && taskProgress < 100 ? <Loader2 className="animate-spin w-4 h-4" /> : 'Process Batch'}
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
                  <Video size={16} />
                  <span className="text-sm font-medium">Download Video/Img</span>
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
                  <span className="text-sm font-medium">Download Audio</span>
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
                  <span className="text-sm font-medium">Download Cover</span>
                </div>
              </button>
            </div>

            {/* Custom Tags Input */}
            <div className="relative">
              <div className="absolute left-4 top-1/2 -translate-y-1/2 text-zinc-500 pointer-events-none">
                <span className="text-xs font-semibold uppercase tracking-wider text-zinc-600">Tags</span>
              </div>
              <input 
                value={customTags}
                onChange={e => setCustomTags(e.target.value)}
                placeholder="e.g. Food, Travel, Tutorial (Optional)"
                className="w-full bg-zinc-900/50 border border-zinc-800 rounded-xl pl-16 pr-10 py-3.5 text-zinc-200 focus:border-indigo-500 outline-none transition-colors text-sm"
              />
              <Tag className="absolute right-4 top-1/2 -translate-y-1/2 text-zinc-600" size={16} />
            </div>

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
              />
            )}

            {/* Final Result Card - Shows when complete */}
            {parserMode === 'single' && currentResult && taskProgress === 100 && (
              <div className="mt-8 animate-in fade-in zoom-in-95 duration-300">
                 <div className="flex items-center justify-between mb-4 px-1">
                    <h3 className="text-lg font-semibold text-zinc-300">Analysis Result</h3>
                    <div className="flex items-center gap-2">
                       <span className="text-xs text-green-400 flex items-center gap-1">
                          <CheckCircle2 size={12} />
                          Download Complete
                       </span>
                    </div>
                 </div>
                 <MediaCard data={currentResult} onSave={(item) => handleSaveToLibrary(item)} onUpdate={handleUpdateLibraryItem} />
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
                          key={`${item.aweme_id}-${idx}`} 
                          data={item} 
                          onClick={() => {
                             // Switch to library detail view mock-up if needed, 
                             // for now just log since we are in parser mode
                             console.log("Clicked batch item:", item.video_title);
                          }}
                        />
                      ))}
                  </div>
               </div>
            )}

            {!currentResult && batchResults.length === 0 && !isParsing && taskProgress === 0 && (
              <div className="grid grid-cols-1 md:grid-cols-3 gap-4 mt-8">
                 <div className="p-6 rounded-2xl bg-zinc-900/50 border border-zinc-800/50 hover:border-zinc-700 transition-colors text-center">
                    <div className="w-10 h-10 bg-indigo-900/30 rounded-lg flex items-center justify-center mx-auto mb-3 text-indigo-400">
                      <Database size={20} />
                    </div>
                    <h4 className="font-semibold text-zinc-200 mb-1">Cloud Storage</h4>
                    <p className="text-xs text-zinc-500">Automatically save parsed metadata to Supabase.</p>
                 </div>
                 <div className="p-6 rounded-2xl bg-zinc-900/50 border border-zinc-800/50 hover:border-zinc-700 transition-colors text-center">
                    <div className="w-10 h-10 bg-purple-900/30 rounded-lg flex items-center justify-center mx-auto mb-3 text-purple-400">
                      <Terminal size={20} />
                    </div>
                    <h4 className="font-semibold text-zinc-200 mb-1">Live Monitor</h4>
                    <p className="text-xs text-zinc-500">Real-time WebSocket task tracking and logging.</p>
                 </div>
                 <div className="p-6 rounded-2xl bg-zinc-900/50 border border-zinc-800/50 hover:border-zinc-700 transition-colors text-center">
                    <div className="w-10 h-10 bg-pink-900/30 rounded-lg flex items-center justify-center mx-auto mb-3 text-pink-400">
                      <LinkIcon size={20} />
                    </div>
                    <h4 className="font-semibold text-zinc-200 mb-1">Multi-Format</h4>
                    <p className="text-xs text-zinc-500">Support for Videos, Image Atlases, and Audio.</p>
                 </div>
              </div>
            )}
          </div>
        )}
        
        {/* VIEW: LIBRARY */}
        {view === 'library' && (
          <div className="animate-in fade-in slide-in-from-bottom-4 duration-500 h-full flex flex-col">
            
            {selectedLibraryItem ? (
              // DETAIL VIEW
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
                    <MediaCard data={selectedLibraryItem} onSave={(item) => handleSaveToLibrary(item)} onUpdate={handleUpdateLibraryItem} onDelete={handleDeleteLibraryItem} />
                 </div>
              </div>
            ) : (
              // LIST/GRID VIEW
              <>
                {/* Desktop Header - HIDDEN ON MOBILE */}
                <header className="hidden md:flex flex-col md:flex-row md:items-center justify-between gap-4 mb-6">
                  <div>
                    <h1 className="text-2xl font-bold text-white">Your Collection</h1>
                    <p className="text-zinc-400 text-sm">Manage your saved downloads.</p>
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

                     {/* Search */}
                     <div className="relative flex-1 md:w-64">
                        <Search className="absolute left-3 top-1/2 -translate-y-1/2 text-zinc-500 w-4 h-4" />
                        <input 
                          type="text" 
                          placeholder="Search title, tags, notes..." 
                          value={searchQuery}
                          onChange={(e) => setSearchQuery(e.target.value)}
                          className="w-full bg-zinc-900 border border-zinc-800 text-zinc-200 text-sm rounded-lg pl-9 pr-3 py-2 outline-none focus:border-indigo-500 transition-colors"
                        />
                     </div>

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

                {/* Mobile Header - Overlay style */}
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

                {/* Library Content */}
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
                             // UPDATED: Use CSS Grid for responsive layout
                             // Auto-fill columns with minimum 200px width
                             <div className="grid grid-cols-2 md:grid-cols-3 lg:grid-cols-4 xl:grid-cols-5 2xl:grid-cols-6 gap-3 w-full pb-20">
                                {filteredLibrary.map((item, idx) => (
                                  <CompactMediaCard
                                    key={`${item.aweme_id}-${idx}`}
                                    data={item}
                                    onClick={() => setSelectedLibraryItem(item)}
                                  />
                                ))}
                             </div>
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
              </>
            )}
          </div>
        )}

        {/* VIEW: DASHBOARD */}
        {view === 'dashboard' && (
          <div className="space-y-8 animate-in fade-in slide-in-from-bottom-4 duration-500">
             <header className="mb-8">
                <h1 className="text-2xl font-bold text-white">Analytics</h1>
                <p className="text-zinc-400 text-sm">Real-time statistics of your downloading habits.</p>
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
                      label: "Total Videos",
                      val: stats?.totalVideos?.toLocaleString() || "0",
                      change: `${stats?.completedDownloads || 0} done`,
                      color: "text-indigo-400"
                    },
                    {
                      label: "Storage Used",
                      val: formatStorage(stats?.totalStorageBytes || 0),
                      change: `${stats?.pendingDownloads || 0} pending`,
                      color: "text-purple-400"
                    },
                    {
                      label: "Saved Creators",
                      val: (stats?.uniqueAuthors || 0).toString(),
                      change: `${stats?.failedDownloads || 0} failed`,
                      color: "text-pink-400"
                    },
                    {
                      label: "Success Rate",
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

             <div className="bg-zinc-900 border border-zinc-800 rounded-xl p-6">
                <h3 className="text-lg font-semibold mb-4">Recent System Logs</h3>
                <div className="space-y-3">
                   {(dashboardStats?.recentLogs || []).length > 0 ? (
                     dashboardStats?.recentLogs.map((log, i) => (
                       <div key={i} className="flex items-center justify-between text-sm py-2 border-b border-zinc-800/50 last:border-0">
                         <span className="text-zinc-400 flex items-center gap-2">
                           <div className={`w-1.5 h-1.5 rounded-full ${
                             log.status === 'success' ? 'bg-green-500' :
                             log.status === 'error' ? 'bg-red-500' : 'bg-yellow-500'
                           }`}></div>
                           {log.message}
                         </span>
                         <span className="text-zinc-600 font-mono text-xs">{log.time}</span>
                       </div>
                     ))
                   ) : (
                     <div className="text-zinc-500 text-sm text-center py-4">No recent activity</div>
                   )}
                </div>
             </div>
          </div>
        )}
        
        {/* VIEW: SETTINGS */}
        {view === 'settings' && (
           <SettingsView 
             settings={userSettings}
             onUpdateSettings={handleUpdateSettings}
             activeTab={settingsTab}
           />
        )}

      </main>
    </div>
  );
}
