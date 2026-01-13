// 自动生成的 Supabase 数据库类型定义
// 由 Supabase MCP 生成

export type Json =
  | string
  | number
  | boolean
  | null
  | { [key: string]: Json | undefined }
  | Json[]

export type Database = {
  public: {
    Tables: {
      authors: {
        Row: {
          author_id: string | null
          avatar_url: string | null
          created_at: string | null
          follower_count: number | null
          following_count: number | null
          id: number
          nickname: string
          signature: string | null
          total_favorited: number | null
          updated_at: string | null
        }
        Insert: {
          author_id?: string | null
          avatar_url?: string | null
          created_at?: string | null
          follower_count?: number | null
          following_count?: number | null
          id?: number
          nickname: string
          signature?: string | null
          total_favorited?: number | null
          updated_at?: string | null
        }
        Update: {
          author_id?: string | null
          avatar_url?: string | null
          created_at?: string | null
          follower_count?: number | null
          following_count?: number | null
          id?: number
          nickname?: string
          signature?: string | null
          total_favorited?: number | null
          updated_at?: string | null
        }
        Relationships: []
      }
      collections: {
        Row: {
          created_at: string | null
          description: string | null
          id: number
          name: string
          updated_at: string | null
          user_id: string | null
        }
        Insert: {
          created_at?: string | null
          description?: string | null
          id?: number
          name: string
          updated_at?: string | null
          user_id?: string | null
        }
        Update: {
          created_at?: string | null
          description?: string | null
          id?: number
          name?: string
          updated_at?: string | null
          user_id?: string | null
        }
        Relationships: []
      }
      douyin_videos: {
        Row: {
          author: string | null
          author_id: number | null
          aweme_id: string
          aweme_type: string | null
          cover_url: string | null
          created_at: string | null
          download_duration: number | null
          download_path: string | null
          download_time: string | null
          error_message: string | null
          id: number
          image_download_urls: Json | null
          music_download_status: Database["public"]["Enums"]["download_status"] | null
          music_download_urls: Json | null
          music_name: string | null
          need_download_music: boolean | null
          need_download_video: boolean | null
          updated_at: string | null
          user_id: string | null
          video_categories: string | null
          video_collect_count: number | null
          video_comment_count: number | null
          video_created_time: string | null
          video_datasize: string | null
          video_desc: string | null
          video_digg_count: number | null
          video_download_status: Database["public"]["Enums"]["download_status"] | null
          video_download_urls: Json | null
          video_duration: string | null
          video_hashtag_name: string | null
          video_original_url: string
          video_resolution: string | null
          video_share_count: number | null
          video_title: string | null
        }
        Insert: {
          author?: string | null
          author_id?: number | null
          aweme_id: string
          aweme_type?: string | null
          cover_url?: string | null
          created_at?: string | null
          download_duration?: number | null
          download_path?: string | null
          download_time?: string | null
          error_message?: string | null
          id?: number
          image_download_urls?: Json | null
          music_download_status?: Database["public"]["Enums"]["download_status"] | null
          music_download_urls?: Json | null
          music_name?: string | null
          need_download_music?: boolean | null
          need_download_video?: boolean | null
          updated_at?: string | null
          user_id?: string | null
          video_categories?: string | null
          video_collect_count?: number | null
          video_comment_count?: number | null
          video_created_time?: string | null
          video_datasize?: string | null
          video_desc?: string | null
          video_digg_count?: number | null
          video_download_status?: Database["public"]["Enums"]["download_status"] | null
          video_download_urls?: Json | null
          video_duration?: string | null
          video_hashtag_name?: string | null
          video_original_url: string
          video_resolution?: string | null
          video_share_count?: number | null
          video_title?: string | null
        }
        Update: {
          author?: string | null
          author_id?: number | null
          aweme_id?: string
          aweme_type?: string | null
          cover_url?: string | null
          created_at?: string | null
          download_duration?: number | null
          download_path?: string | null
          download_time?: string | null
          error_message?: string | null
          id?: number
          image_download_urls?: Json | null
          music_download_status?: Database["public"]["Enums"]["download_status"] | null
          music_download_urls?: Json | null
          music_name?: string | null
          need_download_music?: boolean | null
          need_download_video?: boolean | null
          updated_at?: string | null
          user_id?: string | null
          video_categories?: string | null
          video_collect_count?: number | null
          video_comment_count?: number | null
          video_created_time?: string | null
          video_datasize?: string | null
          video_desc?: string | null
          video_digg_count?: number | null
          video_download_status?: Database["public"]["Enums"]["download_status"] | null
          video_download_urls?: Json | null
          video_duration?: string | null
          video_hashtag_name?: string | null
          video_original_url?: string
          video_resolution?: string | null
          video_share_count?: number | null
          video_title?: string | null
        }
        Relationships: [
          {
            foreignKeyName: "douyin_videos_author_id_fkey"
            columns: ["author_id"]
            isOneToOne: false
            referencedRelation: "authors"
            referencedColumns: ["id"]
          }
        ]
      }
      user_profiles: {
        Row: {
          avatar_url: string | null
          created_at: string | null
          id: string
          role: Database["public"]["Enums"]["user_role"] | null
          updated_at: string | null
          username: string | null
        }
        Insert: {
          avatar_url?: string | null
          created_at?: string | null
          id: string
          role?: Database["public"]["Enums"]["user_role"] | null
          updated_at?: string | null
          username?: string | null
        }
        Update: {
          avatar_url?: string | null
          created_at?: string | null
          id?: string
          role?: Database["public"]["Enums"]["user_role"] | null
          updated_at?: string | null
          username?: string | null
        }
        Relationships: []
      }
      video_collections: {
        Row: {
          added_at: string | null
          collection_id: number
          video_id: number
        }
        Insert: {
          added_at?: string | null
          collection_id: number
          video_id: number
        }
        Update: {
          added_at?: string | null
          collection_id?: number
          video_id?: number
        }
        Relationships: [
          {
            foreignKeyName: "video_collections_collection_id_fkey"
            columns: ["collection_id"]
            isOneToOne: false
            referencedRelation: "collections"
            referencedColumns: ["id"]
          },
          {
            foreignKeyName: "video_collections_video_id_fkey"
            columns: ["video_id"]
            isOneToOne: false
            referencedRelation: "douyin_videos"
            referencedColumns: ["id"]
          }
        ]
      }
    }
    Views: {
      author_statistics: {
        Row: {
          author: string | null
          avg_likes: number | null
          total_comments: number | null
          total_likes: number | null
          video_count: number | null
        }
        Relationships: []
      }
      daily_statistics: {
        Row: {
          date: string | null
          videos_added: number | null
          videos_downloaded: number | null
        }
        Relationships: []
      }
      video_statistics: {
        Row: {
          downloaded: number | null
          downloading: number | null
          failed: number | null
          image_collections: number | null
          image_texts: number | null
          pending: number | null
          skipped: number | null
          special_videos: number | null
          standard_videos: number | null
          total_comments: number | null
          total_likes: number | null
          total_shares: number | null
          total_videos: number | null
          unique_authors: number | null
        }
        Relationships: []
      }
    }
    Functions: {
      update_daily_statistics: { Args: Record<string, never>; Returns: undefined }
    }
    Enums: {
      aweme_type_enum: "0" | "2" | "4" | "61" | "68" | "150" | "157"
      download_status: "pending" | "downloading" | "completed" | "failed" | "skipped"
      user_role: "admin" | "user" | "test"
    }
    CompositeTypes: {
      [_ in never]: never
    }
  }
}

// ============================================
// 便捷类型别名
// ============================================

// 表类型
export type DouyinVideo = Database["public"]["Tables"]["douyin_videos"]["Row"]
export type DouyinVideoInsert = Database["public"]["Tables"]["douyin_videos"]["Insert"]
export type DouyinVideoUpdate = Database["public"]["Tables"]["douyin_videos"]["Update"]

export type Author = Database["public"]["Tables"]["authors"]["Row"]
export type AuthorInsert = Database["public"]["Tables"]["authors"]["Insert"]
export type AuthorUpdate = Database["public"]["Tables"]["authors"]["Update"]

export type Collection = Database["public"]["Tables"]["collections"]["Row"]
export type CollectionInsert = Database["public"]["Tables"]["collections"]["Insert"]
export type CollectionUpdate = Database["public"]["Tables"]["collections"]["Update"]

export type UserProfile = Database["public"]["Tables"]["user_profiles"]["Row"]
export type UserProfileInsert = Database["public"]["Tables"]["user_profiles"]["Insert"]
export type UserProfileUpdate = Database["public"]["Tables"]["user_profiles"]["Update"]

export type VideoCollection = Database["public"]["Tables"]["video_collections"]["Row"]

// 视图类型
export type VideoStatistics = Database["public"]["Views"]["video_statistics"]["Row"]
export type DailyStatistics = Database["public"]["Views"]["daily_statistics"]["Row"]
export type AuthorStatistics = Database["public"]["Views"]["author_statistics"]["Row"]

// 枚举类型
export type DownloadStatus = Database["public"]["Enums"]["download_status"]
export type UserRole = Database["public"]["Enums"]["user_role"]
export type AwemeTypeEnum = Database["public"]["Enums"]["aweme_type_enum"]

// ============================================
// 枚举常量
// ============================================

export const DOWNLOAD_STATUS = {
  PENDING: "pending",
  DOWNLOADING: "downloading",
  COMPLETED: "completed",
  FAILED: "failed",
  SKIPPED: "skipped",
} as const

export const USER_ROLE = {
  ADMIN: "admin",
  USER: "user",
  TEST: "test",
} as const

export const AWEME_TYPE = {
  STANDARD_VIDEO: "0",      // 标准视频
  IMAGE_COLLECTION: "2",    // 图片轮播/图集
  SPECIAL_VIDEO_4: "4",     // 特殊视频
  SPECIAL_VIDEO_61: "61",   // 特殊视频变体
  IMAGE_TEXT: "68",         // 图文
  OTHER_150: "150",         // 其他类型
  OTHER_157: "157",         // 其他类型
} as const

// 视频类型描述映射
export const AWEME_TYPE_LABELS: Record<string, string> = {
  "0": "视频",
  "2": "图集",
  "4": "特殊视频",
  "61": "特殊视频",
  "68": "图文",
  "150": "其他",
  "157": "其他",
}

// 下载状态描述映射
export const DOWNLOAD_STATUS_LABELS: Record<DownloadStatus, string> = {
  pending: "待下载",
  downloading: "下载中",
  completed: "已完成",
  failed: "失败",
  skipped: "已跳过",
}

// 下载状态颜色映射
export const DOWNLOAD_STATUS_COLORS: Record<DownloadStatus, string> = {
  pending: "yellow",
  downloading: "blue",
  completed: "green",
  failed: "red",
  skipped: "gray",
}
