import { getSupabaseClient, isSupabaseConfigured } from '../supabaseClient';
import { ParsedMedia } from '../types';

const TABLE_NAME = 'parsed_media';
const VIEW_NAME = 'parsed_media';  // View dropped; query base table directly

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
 * Get music/audio download URL
 */
export const getMusicDownloadUrl = (platformId: string): string => {
  return `${getApiUrl()}/api/v1/videos/download/${platformId}/music`;
};

/**
 * Fetch a single video by platform_id
 */
export const fetchVideoByPlatformId = async (platformId: string): Promise<ParsedMedia | null> => {
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

    return data as ParsedMedia | null;
  } catch (e) {
    console.error('Error fetching video by platform_id:', e);
    return null;
  }
};

// Keep old name as alias
export const fetchVideoByAwemeId = fetchVideoByPlatformId;

/**
 * Fetch a single video by id (Snowflake BIGINT)
 */
export const fetchVideoByDisplayId = async (displayId: string): Promise<ParsedMedia | null> => {
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
      .eq('id', displayId)
      .eq('user_id', user.id)
      .maybeSingle();

    if (error) {
      console.error('Failed to fetch video by id:', error);
      return null;
    }

    return data as ParsedMedia | null;
  } catch (e) {
    console.error('Error fetching video by id:', e);
    return null;
  }
};

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
): Promise<PaginatedResult<ParsedMedia>> => {
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
      data: (data as ParsedMedia[]) || [],
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
export const fetchLibrary = async (): Promise<ParsedMedia[]> => {
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
    return (data as ParsedMedia[]) || [];
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

export const saveItem = async (item: ParsedMedia): Promise<ParsedMedia> => {
  const supabase = getSupabaseClient();
  if (!isSupabaseConfigured() || !supabase) {
    throw new Error("Supabase is not configured");
  }

  // Strip computed/joined fields that are not actual DB columns
  const { tags, summary_text, ...dbFields } = item;
  const payload = {
    ...dbFields,
    published_at: item.published_at || new Date().toISOString(),
    video_download_urls: item.video_download_urls || [],
    image_download_urls: item.image_download_urls || [],
    // DB enum uses lowercase values
    video_download_status: item.video_download_status?.toLowerCase() || 'pending',
    music_download_status: item.music_download_status?.toLowerCase() || 'pending',
    cover_download_status: item.cover_download_status?.toLowerCase() || 'pending',
  };

  const { data, error } = await supabase
    .from(TABLE_NAME)
    .upsert(payload, { onConflict: 'platform_id,source_platform' })
    .select()
    .single();

  if (error) throw error;
  return data as ParsedMedia;
};

export const updateItem = async (id: string, updates: Partial<ParsedMedia>): Promise<ParsedMedia> => {
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
  return data as ParsedMedia;
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
 * Fetch dashboard statistics via Supabase RPC (server-side computation)
 */
export const fetchDashboardStats = async (): Promise<DashboardStats> => {
  const supabase = getSupabaseClient();

  // Default empty stats
  const emptyStats: DashboardStats = {
    totalVideos: 0,
    completedDownloads: 0,
    pendingDownloads: 0,
    failedDownloads: 0,
    totalStorageBytes: 0,
    uniqueAuthors: 0,
    mediaDistribution: [
      { name: 'Video', value: 0 },
      { name: 'Images', value: 0 },
      { name: 'Audio', value: 0 },
    ],
    weeklyActivity: [],
    topTags: [],
    recentLogs: [],
  };

  if (!isSupabaseConfigured() || !supabase) {
    return emptyStats;
  }

  try {
    const { data: { user } } = await supabase.auth.getUser();
    if (!user) return emptyStats;

    const { data, error } = await supabase.rpc('get_dashboard_stats', {
      p_user_id: user.id,
    });

    if (error) {
      console.error('RPC get_dashboard_stats failed:', error);
      return emptyStats;
    }

    const d = data as Record<string, any>;

    // Map media distribution from RPC format
    const mediaDist = d.media_distribution?.[0] || {};
    const mediaDistribution = [
      { name: 'Video', value: mediaDist.video || 0 },
      { name: 'Images', value: mediaDist.images || 0 },
      { name: 'Audio', value: mediaDist.audio || 0 },
    ];

    // Map weekly activity
    const weeklyActivity = (d.weekly_activity || []).map((w: any) => ({
      name: w.name,
      downloads: w.downloads,
      shares: 0,
    }));

    // Map top tags
    const topTags = (d.top_tags || []).map((t: any) => ({
      name: t.name,
      count: t.count,
    }));

    // Fetch recent logs separately (not in RPC)
    let recentLogs: DashboardStats['recentLogs'] = [];
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
      console.debug('Failed to fetch user logs:', e);
    }

    return {
      totalVideos: d.total || 0,
      completedDownloads: d.completed || 0,
      pendingDownloads: d.pending || 0,
      failedDownloads: d.failed || 0,
      totalStorageBytes: d.total_storage_bytes || 0,
      uniqueAuthors: d.unique_authors || 0,
      mediaDistribution,
      weeklyActivity,
      topTags,
      recentLogs,
    };
  } catch (e) {
    console.error('Error fetching dashboard stats:', e);
    return emptyStats;
  }
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
