import { getSupabaseClient, isSupabaseConfigured } from '../supabaseClient';
import { DouyinBase } from '../types';

const TABLE_NAME = 'douyin_videos';

// 获取 API URL
const getApiUrl = (): string => {
  // @ts-ignore
  if (typeof import.meta !== 'undefined' && import.meta.env?.VITE_API_URL) {
    // @ts-ignore
    return import.meta.env.VITE_API_URL;
  }
  return 'http://localhost:8080';
};

/**
 * 前端配置接口（用于 Supabase URL 和 Anon Key）
 */
export interface FrontendConfig {
  supabase_url: string | null;
  supabase_anon_key: string | null;
  default_download_path: string | null;
}

/**
 * 从后端 YAML 配置文件获取前端配置
 * 优先级: YAML 配置 > .env 环境变量
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
 * 保存前端配置到后端 YAML 文件
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
      const error = await response.json().catch(() => ({ detail: '保存配置失败' }));
      throw new Error(error.detail || '保存配置失败');
    }

    return await response.json();
  } catch (error) {
    console.error('Error saving frontend config:', error);
    throw error;
  }
};

/**
 * 获取视频下载 URL（通过后端 API 下载，设置正确的 Content-Disposition 头）
 */
export const getDownloadUrl = (awemeId: string): string => {
  return `${getApiUrl()}/api/v1/douyin/download/${awemeId}`;
};

/**
 * 获取封面下载 URL
 */
export const getCoverDownloadUrl = (awemeId: string): string => {
  return `${getApiUrl()}/api/v1/douyin/download/${awemeId}/cover`;
};

/** 分页配置 */
const PAGE_SIZE = 100;  // 每页加载数量
const LOCAL_CACHE_SIZE = 500;  // 本地缓存用于快速搜索

export interface PaginatedResult<T> {
  data: T[];
  totalCount: number;
  hasMore: boolean;
  page: number;
}

/**
 * 获取视频库（分页版本，适合大数据量）
 * @param page 页码（从 0 开始）
 * @param pageSize 每页数量
 */
export const fetchLibraryPaginated = async (
  page: number = 0,
  pageSize: number = PAGE_SIZE
): Promise<PaginatedResult<DouyinBase>> => {
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

    // 使用 count: 'exact' 获取总数
    const { data, error, count } = await supabase
      .from(TABLE_NAME)
      .select('*', { count: 'exact' })
      .eq('user_id', user.id)
      .order('created_at', { ascending: false })
      .range(from, to);

    if (error) throw error;

    const totalCount = count || 0;
    const hasMore = (page + 1) * pageSize < totalCount;

    return {
      data: (data as DouyinBase[]) || [],
      totalCount,
      hasMore,
      page,
    };
  } catch (err: any) {
    throw err;
  }
};

/**
 * 获取视频库（兼容旧版本，加载前 LOCAL_CACHE_SIZE 条用于本地搜索）
 */
export const fetchLibrary = async (): Promise<DouyinBase[]> => {
  const supabase = getSupabaseClient();
  if (!isSupabaseConfigured() || !supabase) {
    throw new Error("Supabase is not configured");
  }

  try {
    const { data: { user } } = await supabase.auth.getUser();
    if (!user) {
      throw new Error("User not authenticated");
    }

    // 只加载最近 500 条用于本地缓存和快速搜索
    const { data, error } = await supabase
      .from(TABLE_NAME)
      .select('*')
      .eq('user_id', user.id)
      .order('created_at', { ascending: false })
      .limit(LOCAL_CACHE_SIZE);

    if (error) throw error;
    return (data as DouyinBase[]) || [];
  } catch (err: any) {
    throw err;
  }
};

/**
 * 获取视频总数
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

export const saveItem = async (item: DouyinBase): Promise<DouyinBase> => {
  const supabase = getSupabaseClient();
  if (!isSupabaseConfigured() || !supabase) {
    throw new Error("Supabase is not configured");
  }

  // Clean the object to ensure compatibility with JSON columns if necessary
  // and remove any UI-specific temporary flags if they exist
  const payload = {
    ...item,
    // Ensure dates are ISO strings if they are Date objects
    video_created_time: item.video_created_time || new Date().toISOString(),
    // Default to empty arrays for arrays if undefined
    tags: item.tags || [],
    video_download_urls: item.video_download_urls || [],
    image_download_urls: item.image_download_urls || [],
  };

  const { data, error } = await supabase
    .from(TABLE_NAME)
    .upsert(payload, { onConflict: 'aweme_id' })
    .select()
    .single();

  if (error) throw error;
  return data as DouyinBase;
};

export const updateItem = async (id: string, updates: Partial<DouyinBase>): Promise<DouyinBase> => {
  const supabase = getSupabaseClient();
  if (!isSupabaseConfigured() || !supabase) {
    throw new Error("Supabase is not configured");
  }

  // 获取当前用户 ID
  const { data: { user } } = await supabase.auth.getUser();
  if (!user) {
    throw new Error("User not authenticated");
  }

  const { data, error } = await supabase
    .from(TABLE_NAME)
    .update(updates)
    .eq('aweme_id', id)
    .eq('user_id', user.id)  // 只能更新自己的数据
    .select()
    .single();

  if (error) throw error;
  return data as DouyinBase;
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

  // 获取认证 token
  const { data: { session } } = await supabase.auth.getSession();
  if (!session?.access_token) {
    throw new Error("User not authenticated");
  }

  // 调用后端 API 删除（支持删除本地文件）
  const response = await fetch(
    `${getApiUrl()}/api/v1/douyin/videos/${id}?delete_files=${deleteFiles}`,
    {
      method: 'DELETE',
      headers: {
        'Authorization': `Bearer ${session.access_token}`
      }
    }
  );

  if (!response.ok) {
    const error = await response.json().catch(() => ({ detail: '删除失败' }));
    throw new Error(error.detail || '删除失败');
  }

  return await response.json();
};

/**
 * 用户日志接口
 */
export interface UserLog {
  id: string;
  user_id: string;
  action: string;
  message: string;
  status: 'success' | 'error' | 'warning' | 'info' | 'pending';
  aweme_id?: string;
  details?: Record<string, unknown>;
  created_at: string;
}

/**
 * 获取用户操作日志
 */
export const fetchUserLogs = async (limit: number = 20): Promise<UserLog[]> => {
  const supabase = getSupabaseClient();
  if (!isSupabaseConfigured() || !supabase) {
    return [];
  }

  try {
    // 获取认证 token
    const { data: { session } } = await supabase.auth.getSession();
    if (!session?.access_token) {
      return [];
    }

    const response = await fetch(`${getApiUrl()}/api/v1/douyin/logs?limit=${limit}`, {
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
 * Dashboard 统计数据接口
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
 * 获取 Dashboard 统计数据
 * Uses backend API for accurate total counts (across all videos, not just current page)
 * Uses local library data for distribution/activity stats (works with loaded data)
 */
export const fetchDashboardStats = async (library: DouyinBase[]): Promise<DashboardStats> => {
  // Fetch accurate stats from backend API (calculates across ALL user videos)
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
    const token = localStorage.getItem('supabase_access_token');
    const apiKey = localStorage.getItem('douyin_api_key');

    const headers: Record<string, string> = {
      'Content-Type': 'application/json',
    };
    if (token) headers['Authorization'] = `Bearer ${token}`;
    if (apiKey) headers['X-API-Key'] = apiKey;

    const response = await fetch(`${apiUrl}/api/v1/douyin/statistics`, {
      method: 'GET',
      headers,
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

  // Use backend stats for accurate counts across all videos
  const completedDownloads = backendStats.completed;
  const pendingDownloads = backendStats.pending;
  const failedDownloads = backendStats.failed;
  const totalStorageBytes = backendStats.total_storage_bytes;
  const uniqueAuthors = backendStats.unique_authors;

  // 媒体类型分布
  const videoCount = library.filter(v => ['0', '4', '61', 0, 4, 61].includes(v.aweme_type as any)).length;
  const albumCount = library.filter(v => ['2', '68', 2, 68].includes(v.aweme_type as any)).length;
  const audioCount = library.filter(v => v.need_download_music).length;

  const mediaDistribution = [
    { name: 'Video', value: videoCount },
    { name: 'Images', value: albumCount },
    { name: 'Audio', value: audioCount },
  ];

  // 按周统计（最近7天）
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
      shares: 0, // TODO: 分享功能待实现，统计生成短链接的分享次数
    });
  }

  // 统计标签（仅使用 video_categories）
  const tagCounts: Record<string, number> = {};
  library.forEach(v => {
    // 只使用 video_categories 字段
    const tags = v.video_categories?.split(',').map(t => t.trim()).filter(Boolean) || [];
    tags.forEach(tag => {
      tagCounts[tag] = (tagCounts[tag] || 0) + 1;
    });
  });

  const topTags = Object.entries(tagCounts)
    .sort((a, b) => b[1] - a[1])
    .slice(0, 10)
    .map(([name, count]) => ({ name, count }));

  // 最近日志（从 API 获取真实日志，如果失败则使用视频数据作为备用）
  let recentLogs: { message: string; time: string; status: 'success' | 'pending' | 'error' }[] = [];

  try {
    const userLogs = await fetchUserLogs(10);
    if (userLogs.length > 0) {
      recentLogs = userLogs.map(log => ({
        message: log.message,
        time: log.created_at ? new Date(log.created_at).toLocaleTimeString('zh-CN', {
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

  // 如果没有日志，使用视频数据作为备用
  if (recentLogs.length === 0) {
    recentLogs = library.slice(0, 5).map(v => ({
      message: `${v.video_download_status === 'COMPLETED' ? '下载完成' : v.video_download_status === 'FAILED' ? '下载失败' : '处理中'}: ${v.video_title?.substring(0, 20) || v.aweme_id?.substring(0, 10)}...`,
      time: v.created_at ? new Date(v.created_at).toLocaleTimeString('zh-CN', { hour: '2-digit', minute: '2-digit', hour12: false }) : '',
      status: (v.video_download_status === 'COMPLETED' ? 'success' : v.video_download_status === 'FAILED' ? 'error' : 'pending') as 'success' | 'pending' | 'error',
    }));
  }

  return {
    totalVideos: backendStats.total,  // Use backend count for accurate total across all videos
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
 * 用户设置接口
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
 * 获取用户设置
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
 * 保存用户设置
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
    const error = await response.json().catch(() => ({ detail: '保存设置失败' }));
    throw new Error(error.detail || '保存设置失败');
  }

  return await response.json();
};
