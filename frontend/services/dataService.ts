import { getSupabaseClient, isSupabaseConfigured } from '../supabaseClient';
import { Video } from '../types';
import { getAuthHeaders } from './parserService';

const TABLE_NAME = 'videos';
const VIEW_NAME = 'videos_with_tags';  // View that includes tags array

const getApiUrl = (): string => {
  // @ts-ignore
  if (typeof import.meta !== 'undefined' && 'VITE_API_URL' in import.meta.env) {
    // @ts-ignore
    return import.meta.env.VITE_API_URL || '';
  }
  return 'http://localhost:8080';
};

/**
 * Frontend config interface (for Supabase URL and Anon Key)
 */
export interface FrontendConfig {
  supabase_url: string | null;
  supabase_anon_key: string | null;
  default_download_path: string | null;
}

/**
 * Fetch frontend config from backend YAML
 */
export const fetchFrontendConfig = async (): Promise<FrontendConfig | null> => {
  try {
    const response = await fetch(`${getApiUrl()}/api/v1/config`);
    if (!response.ok) {
      console.error('Failed to fetch frontend config:', response.status);
      return null;
    }
    return await response.json();
  } catch (error) {
    console.error('Error fetching frontend config:', error);
    return null;
  }
};

/**
 * Save frontend config to backend YAML
 */
export const saveFrontendConfig = async (config: {
  supabase_url?: string;
  supabase_anon_key?: string;
  default_download_path?: string;
}): Promise<FrontendConfig | null> => {
  try {
    const response = await fetch(`${getApiUrl()}/api/v1/config`, {
      method: 'PUT',
      headers: {
        'Content-Type': 'application/json'
      },
      body: JSON.stringify(config)
    });

    if (!response.ok) {
      const error = await response.json().catch(() => ({ detail: 'Failed to save config' }));
      throw new Error(error.detail || 'Failed to save config');
    }

    return await response.json();
  } catch (error) {
    console.error('Error saving frontend config:', error);
    throw error;
  }
};

/**
 * Get video download URL via backend API
 */
export const getDownloadUrl = (platformId: string): string => {
  return `${getApiUrl()}/api/v1/videos/download/${platformId}`;
};

/**
 * Get cover download URL
 */
export const getCoverDownloadUrl = (platformId: string): string => {
  return `${getApiUrl()}/api/v1/videos/download/${platformId}/cover`;
};

/**
 * Fetch a single video by platform_id
 */
export const fetchVideoByPlatformId = async (platformId: string): Promise<Video | null> => {
  const supabase = getSupabaseClient();
  if (!isSupabaseConfigured() || !supabase) {
    return null;
  }

  try {
    const { data: { user } } = await supabase.auth.getUser();
    if (!user) return null;

    const { data, error } = await supabase
      .from(VIEW_NAME)
      .select('*')
      .eq('platform_id', platformId)
      .eq('user_id', user.id)
      .maybeSingle();

    if (error) {
      console.error('Failed to fetch video:', error);
      return null;
    }

    return data as Video | null;
  } catch (e) {
    console.error('Error fetching video by platform_id:', e);
    return null;
  }
};

// Keep old name as alias
export const fetchVideoByAwemeId = fetchVideoByPlatformId;

/** Pagination config */
const PAGE_SIZE = 100;
const LOCAL_CACHE_SIZE = 500;

export interface PaginatedResult<T> {
  data: T[];
  totalCount: number;
  hasMore: boolean;
  page: number;
}

/**
 * Fetch video library (paginated)
 */
export const fetchLibraryPaginated = async (
  page: number = 0,
  pageSize: number = PAGE_SIZE
): Promise<PaginatedResult<Video>> => {
  const supabase = getSupabaseClient();
  if (!isSupabaseConfigured() || !supabase) {
    throw new Error("Supabase is not configured");
  }

  try {
    const { data: { user } } = await supabase.auth.getUser();
    if (!user) {
      throw new Error("User not authenticated");
    }

    const from = page * pageSize;
    const to = from + pageSize - 1;

    const { data, error, count } = await supabase
      .from(VIEW_NAME)
      .select('*', { count: 'exact' })
      .eq('user_id', user.id)
      .order('created_at', { ascending: false })
      .range(from, to);

    if (error) throw error;

    const totalCount = count || 0;
    const hasMore = (page + 1) * pageSize < totalCount;

    return {
      data: (data as Video[]) || [],
      totalCount,
      hasMore,
      page,
    };
  } catch (err: any) {
    throw err;
  }
};

/**
 * Fetch video library (loads first LOCAL_CACHE_SIZE for local search)
 */
export const fetchLibrary = async (): Promise<Video[]> => {
  const supabase = getSupabaseClient();
  if (!isSupabaseConfigured() || !supabase) {
    throw new Error("Supabase is not configured");
  }

  try {
    const { data: { user } } = await supabase.auth.getUser();
    if (!user) {
      throw new Error("User not authenticated");
    }

    const { data, error } = await supabase
      .from(VIEW_NAME)
      .select('*')
      .eq('user_id', user.id)
      .order('created_at', { ascending: false })
      .limit(LOCAL_CACHE_SIZE);

    if (error) throw error;
    return (data as Video[]) || [];
  } catch (err: any) {
    throw err;
  }
};

/**
 * Fetch total video count
 */
export const fetchLibraryCount = async (): Promise<number> => {
  const supabase = getSupabaseClient();
  if (!isSupabaseConfigured() || !supabase) {
    throw new Error("Supabase is not configured");
  }

  try {
    const { data: { user } } = await supabase.auth.getUser();
    if (!user) {
      throw new Error("User not authenticated");
    }

    const { count, error } = await supabase
      .from(TABLE_NAME)
      .select('*', { count: 'exact', head: true })
      .eq('user_id', user.id);

    if (error) throw error;
    return count || 0;
  } catch (err: any) {
    throw err;
  }
};

export const saveItem = async (item: Video): Promise<Video> => {
  const supabase = getSupabaseClient();
  if (!isSupabaseConfigured() || !supabase) {
    throw new Error("Supabase is not configured");
  }

  const payload = {
    ...item,
    published_at: item.published_at || new Date().toISOString(),
    tags: item.tags || [],
    video_download_urls: item.video_download_urls || [],
    image_download_urls: item.image_download_urls || [],
  };

  const { data, error } = await supabase
    .from(TABLE_NAME)
    .upsert(payload, { onConflict: 'platform_id,source_platform' })
    .select()
    .single();

  if (error) throw error;
  return data as Video;
};

export const updateItem = async (id: string, updates: Partial<Video>): Promise<Video> => {
  const supabase = getSupabaseClient();
  if (!isSupabaseConfigured() || !supabase) {
    throw new Error("Supabase is not configured");
  }

  const { data: { user } } = await supabase.auth.getUser();
  if (!user) {
    throw new Error("User not authenticated");
  }

  const { data, error } = await supabase
    .from(TABLE_NAME)
    .update(updates)
    .eq('platform_id', id)
    .eq('user_id', user.id)
    .select()
    .single();

  if (error) throw error;
  return data as Video;
};

export interface DeleteResult {
  success: boolean;
  message: string;
  files_deleted: string[];
}

export const deleteItem = async (id: string, deleteFiles: boolean = false): Promise<DeleteResult> => {
  const supabase = getSupabaseClient();
  if (!isSupabaseConfigured() || !supabase) {
    throw new Error("Supabase is not configured");
  }

  const { data: { session } } = await supabase.auth.getSession();
  if (!session?.access_token) {
    throw new Error("User not authenticated");
  }

  const response = await fetch(
    `${getApiUrl()}/api/v1/videos/${id}?delete_files=${deleteFiles}`,
    {
      method: 'DELETE',
      headers: {
        'Authorization': `Bearer ${session.access_token}`
      }
    }
  );

  if (!response.ok) {
    const error = await response.json().catch(() => ({ detail: 'Delete failed' }));
    throw new Error(error.detail || 'Delete failed');
  }

  return await response.json();
};

/**
 * User log interface
 */
export interface UserLog {
  id: string;
  user_id: string;
  action: string;
  message: string;
  status: 'success' | 'error' | 'warning' | 'info' | 'pending';
  platform_id?: string;
  details?: Record<string, unknown>;
  created_at: string;
}

/**
 * Fetch user operation logs
 */
export const fetchUserLogs = async (limit: number = 20): Promise<UserLog[]> => {
  const supabase = getSupabaseClient();
  if (!isSupabaseConfigured() || !supabase) {
    return [];
  }

  try {
    const { data: { session } } = await supabase.auth.getSession();
    if (!session?.access_token) {
      return [];
    }

    const response = await fetch(`${getApiUrl()}/api/v1/videos/logs?limit=${limit}`, {
      headers: {
        'Authorization': `Bearer ${session.access_token}`
      }
    });

    if (!response.ok) {
      console.error('Failed to fetch logs:', response.status);
      return [];
    }

    const data = await response.json();
    return data.logs || [];
  } catch (error) {
    console.error('Error fetching logs:', error);
    return [];
  }
};

/**
 * Dashboard statistics interface
 */
export interface DashboardStats {
  totalVideos: number;
  completedDownloads: number;
  pendingDownloads: number;
  failedDownloads: number;
  totalStorageBytes: number;
  uniqueAuthors: number;
  mediaDistribution: { name: string; value: number }[];
  weeklyActivity: { name: string; downloads: number; shares: number }[];
  topTags: { name: string; count: number }[];
  recentLogs: { message: string; time: string; status: 'success' | 'pending' | 'error' }[];
}

/**
 * Fetch dashboard statistics
 */
export const fetchDashboardStats = async (library: Video[]): Promise<DashboardStats> => {
  let backendStats = {
    total: library.length,
    completed: 0,
    pending: 0,
    failed: 0,
    total_storage_bytes: 0,
    unique_authors: 0
  };

  try {
    const apiUrl = getApiUrl();

    const response = await fetch(`${apiUrl}/api/v1/videos/statistics`, {
      method: 'GET',
      headers: getAuthHeaders(),
    });

    if (response.ok) {
      const data = await response.json();
      if (data.success && data.statistics) {
        backendStats = {
          total: data.statistics.total || library.length,
          completed: data.statistics.completed || 0,
          pending: data.statistics.pending || 0,
          failed: data.statistics.failed || 0,
          total_storage_bytes: data.statistics.total_storage_bytes || 0,
          unique_authors: data.statistics.unique_authors || 0
        };
      }
    }
  } catch (e) {
    console.warn('Failed to fetch backend statistics, using local calculation:', e);
  }

  const completedDownloads = backendStats.completed;
  const pendingDownloads = backendStats.pending;
  const failedDownloads = backendStats.failed;
  const totalStorageBytes = backendStats.total_storage_bytes;
  const uniqueAuthors = backendStats.unique_authors;

  // Media type distribution (supports both new string types and legacy numeric)
  const videoCount = library.filter(v => {
    const t = v.media_type;
    return t === 'video' || t === 'special' || t === 'short' || t === 'live_clip' ||
           ['0', '4', '61', 0, 4, 61].includes(t as any);
  }).length;
  const albumCount = library.filter(v => {
    const t = v.media_type;
    return t === 'carousel' || t === 'image_text' ||
           ['2', '68', 2, 68].includes(t as any);
  }).length;
  const audioCount = library.filter(v => v.need_download_music).length;

  const mediaDistribution = [
    { name: 'Video', value: videoCount },
    { name: 'Images', value: albumCount },
    { name: 'Audio', value: audioCount },
  ];

  // Weekly activity (last 7 days)
  const now = new Date();
  const dayNames = ['Sun', 'Mon', 'Tue', 'Wed', 'Thu', 'Fri', 'Sat'];
  const weeklyActivity: { name: string; downloads: number; shares: number }[] = [];

  for (let i = 6; i >= 0; i--) {
    const date = new Date(now);
    date.setDate(date.getDate() - i);
    const dayStart = new Date(date.setHours(0, 0, 0, 0));
    const dayEnd = new Date(date.setHours(23, 59, 59, 999));

    const dayVideos = library.filter(v => {
      if (!v.created_at) return false;
      const createdAt = new Date(v.created_at);
      return createdAt >= dayStart && createdAt <= dayEnd;
    });

    weeklyActivity.push({
      name: dayNames[dayStart.getDay()],
      downloads: dayVideos.length,
      shares: 0,
    });
  }

  // Tag statistics
  let topTags: { name: string; count: number }[] = [];
  try {
    const { fetchTagStatistics } = await import('./tagsService');
    const tagStats = await fetchTagStatistics(10);
    if (tagStats.success && tagStats.top_tags) {
      topTags = tagStats.top_tags.map(t => ({ name: t.name, count: t.count }));
    }
  } catch (e) {
    console.warn('Failed to fetch tag statistics:', e);
  }

  // Recent logs
  let recentLogs: { message: string; time: string; status: 'success' | 'pending' | 'error' }[] = [];

  try {
    const userLogs = await fetchUserLogs(10);
    if (userLogs.length > 0) {
      recentLogs = userLogs.map(log => ({
        message: log.message,
        time: log.created_at ? new Date(log.created_at).toLocaleTimeString('en-US', {
          hour: '2-digit',
          minute: '2-digit',
          hour12: false
        }) : '',
        status: (log.status === 'success' ? 'success' : log.status === 'error' ? 'error' : 'pending') as 'success' | 'pending' | 'error',
      }));
    }
  } catch (e) {
    console.error('Failed to fetch user logs:', e);
  }

  // Fallback to video data if no logs
  if (recentLogs.length === 0) {
    recentLogs = library.slice(0, 5).map(v => ({
      message: `${v.video_download_status === 'COMPLETED' ? 'Download completed' : v.video_download_status === 'FAILED' ? 'Download failed' : 'Processing'}: ${v.title?.substring(0, 20) || v.platform_id?.substring(0, 10)}...`,
      time: v.created_at ? new Date(v.created_at).toLocaleTimeString('en-US', { hour: '2-digit', minute: '2-digit', hour12: false }) : '',
      status: (v.video_download_status === 'COMPLETED' ? 'success' : v.video_download_status === 'FAILED' ? 'error' : 'pending') as 'success' | 'pending' | 'error',
    }));
  }

  return {
    totalVideos: backendStats.total,
    completedDownloads,
    pendingDownloads,
    failedDownloads,
    totalStorageBytes,
    uniqueAuthors,
    mediaDistribution,
    weeklyActivity,
    topTags,
    recentLogs,
  };
};

/**
 * User settings interface
 */
export interface UserSettingsData {
  id?: string;
  user_id: string;
  download_path: string;
  settings_json?: Record<string, unknown>;
  created_at?: string;
  updated_at?: string;
}

/**
 * Fetch user settings
 */
export const fetchUserSettings = async (): Promise<UserSettingsData | null> => {
  const supabase = getSupabaseClient();
  if (!isSupabaseConfigured() || !supabase) {
    return null;
  }

  try {
    const { data: { session } } = await supabase.auth.getSession();
    if (!session?.access_token) {
      return null;
    }

    const response = await fetch(`${getApiUrl()}/api/v1/settings`, {
      headers: {
        'Authorization': `Bearer ${session.access_token}`
      }
    });

    if (!response.ok) {
      console.error('Failed to fetch settings:', response.status);
      return null;
    }

    return await response.json();
  } catch (error) {
    console.error('Error fetching settings:', error);
    return null;
  }
};

/**
 * Save user settings
 */
export const saveUserSettings = async (settings: {
  download_path?: string;
  settings_json?: Record<string, unknown>;
}): Promise<UserSettingsData | null> => {
  const supabase = getSupabaseClient();
  if (!isSupabaseConfigured() || !supabase) {
    throw new Error("Supabase is not configured");
  }

  const { data: { session } } = await supabase.auth.getSession();
  if (!session?.access_token) {
    throw new Error("User not authenticated");
  }

  const response = await fetch(`${getApiUrl()}/api/v1/settings`, {
    method: 'PUT',
    headers: {
      'Authorization': `Bearer ${session.access_token}`,
      'Content-Type': 'application/json'
    },
    body: JSON.stringify(settings)
  });

  if (!response.ok) {
    const error = await response.json().catch(() => ({ detail: 'Failed to save settings' }));
    throw new Error(error.detail || 'Failed to save settings');
  }

  return await response.json();
};
