export interface UserProfile {
  id: string
  username: string | null
  avatar_url: string | null
  role: 'admin' | 'user' | 'test'
  is_banned: boolean
  created_at: string
  updated_at: string
}

export interface Team {
  id: string
  name: string
  owner_id: string
  invite_code: string
  created_at: string
  member_count?: number
}

export interface TeamMember {
  team_id: string
  user_id: string
  role: 'owner' | 'member'
  joined_at: string
  user?: UserProfile
}

export interface Video {
  id: string
  aweme_id: string
  video_title: string
  author: string
  download_status: 'pending' | 'downloading' | 'completed' | 'failed' | 'skipped'
  user_id: string
  team_id: string | null
  created_at: string
}

export interface Tag {
  id: string
  name: string
  color: string | null
  icon: string | null
  type: 'system' | 'user' | 'time'
  user_id: string | null
  created_at: string
}

export interface AuditLog {
  id: string
  admin_id: string
  action: string
  target_type: string
  target_id: string
  details: Record<string, unknown>
  ip_address: string
  created_at: string
}

export interface ApiKey {
  key_id: string
  name: string
  user_id: string
  scopes: string[]
  status: 'active' | 'revoked' | 'expired'
  last_used_at: string | null
  expires_at: string | null
  created_at: string
}
