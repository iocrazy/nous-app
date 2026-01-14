import { supabase, isSupabaseConfigured } from '../supabaseClient';
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

export const fetchLibrary = async (): Promise<DouyinBase[]> => {
  if (!isSupabaseConfigured() || !supabase) {
    throw new Error("Supabase is not configured");
  }

  try {
    // 获取当前用户 ID
    const { data: { user } } = await supabase.auth.getUser();
    if (!user) {
      throw new Error("User not authenticated");
    }

    const { data, error } = await supabase
      .from(TABLE_NAME)
      .select('*')
      .eq('user_id', user.id)  // 只获取当前用户的数据
      .order('video_created_time', { ascending: false });

    if (error) throw error;
    return (data as DouyinBase[]) || [];
  } catch (err: any) {
    // Re-throw with a slightly more informative message if it's a known shape, or just propagate
    throw err;
  }
};

export const saveItem = async (item: DouyinBase): Promise<DouyinBase> => {
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

export const deleteItem = async (id: string): Promise<void> => {
  if (!isSupabaseConfigured() || !supabase) {
    throw new Error("Supabase is not configured");
  }

  // 获取当前用户 ID
  const { data: { user } } = await supabase.auth.getUser();
  if (!user) {
    throw new Error("User not authenticated");
  }

  const { error } = await supabase
    .from(TABLE_NAME)
    .delete()
    .eq('aweme_id', id)
    .eq('user_id', user.id);  // 只能删除自己的数据

  if (error) throw error;
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
 */
export const fetchDashboardStats = async (library: DouyinBase[]): Promise<DashboardStats> => {
  // 计算下载状态统计
  const completedDownloads = library.filter(v => v.video_download_status === 'COMPLETED').length;
  const pendingDownloads = library.filter(v => v.video_download_status === 'PENDING' || v.video_download_status === 'PROCESSING').length;
  const failedDownloads = library.filter(v => v.video_download_status === 'FAILED').length;

  // 计算存储大小
  const totalStorageBytes = library.reduce((sum, v) => {
    const size = parseInt(v.video_datasize || '0', 10);
    return sum + (isNaN(size) ? 0 : size);
  }, 0);

  // 统计唯一创作者
  const uniqueAuthors = new Set(library.map(v => v.author).filter(Boolean)).size;

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
    totalVideos: library.length,
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
