# User System Upgrade Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Upgrade MediaHub with user registration, team collaboration, shared collections, notifications, and i18n support.

**Architecture:** Add global header with user dropdown, notification panel, and language switcher. Extend Supabase with 6 new tables for teams, collections, and notifications. Use react-i18next for internationalization.

**Tech Stack:** React 19, TypeScript, Supabase (Auth + Realtime), react-i18next, Lucide icons

---

## Phase 0: Database Migration

### Task 0.1: Create Teams Table

**Files:**
- Create: `supabase/migrations/003_teams.sql`

**Step 1: Write migration SQL**

```sql
-- 003_teams.sql
-- Teams table for collaboration

CREATE TABLE teams (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  name VARCHAR(100) NOT NULL,
  owner_id UUID REFERENCES auth.users(id) NOT NULL,
  invite_code VARCHAR(20) UNIQUE NOT NULL,
  created_at TIMESTAMPTZ DEFAULT NOW()
);

-- Generate unique invite code function
CREATE OR REPLACE FUNCTION generate_invite_code()
RETURNS TEXT AS $$
DECLARE
  chars TEXT := 'ABCDEFGHJKLMNPQRSTUVWXYZ23456789';
  result TEXT := '';
  i INT;
BEGIN
  FOR i IN 1..8 LOOP
    result := result || substr(chars, floor(random() * length(chars) + 1)::int, 1);
  END LOOP;
  RETURN result;
END;
$$ LANGUAGE plpgsql;

-- Auto-generate invite code on insert
CREATE OR REPLACE FUNCTION set_invite_code()
RETURNS TRIGGER AS $$
BEGIN
  IF NEW.invite_code IS NULL THEN
    NEW.invite_code := generate_invite_code();
  END IF;
  RETURN NEW;
END;
$$ LANGUAGE plpgsql;

CREATE TRIGGER teams_invite_code_trigger
  BEFORE INSERT ON teams
  FOR EACH ROW
  EXECUTE FUNCTION set_invite_code();

-- RLS
ALTER TABLE teams ENABLE ROW LEVEL SECURITY;

CREATE POLICY "Users can view teams they belong to" ON teams
  FOR SELECT USING (
    id IN (SELECT team_id FROM team_members WHERE user_id = auth.uid())
    OR owner_id = auth.uid()
  );

CREATE POLICY "Users can create teams" ON teams
  FOR INSERT WITH CHECK (owner_id = auth.uid());

CREATE POLICY "Owners can update their teams" ON teams
  FOR UPDATE USING (owner_id = auth.uid());

CREATE POLICY "Owners can delete their teams" ON teams
  FOR DELETE USING (owner_id = auth.uid());
```

**Step 2: Commit**

```bash
git add supabase/migrations/003_teams.sql
git commit -m "feat(db): add teams table with RLS policies"
```

---

### Task 0.2: Create Team Members Table

**Files:**
- Create: `supabase/migrations/004_team_members.sql`

**Step 1: Write migration SQL**

```sql
-- 004_team_members.sql
-- Team membership tracking

CREATE TABLE team_members (
  team_id UUID REFERENCES teams(id) ON DELETE CASCADE,
  user_id UUID REFERENCES auth.users(id) ON DELETE CASCADE,
  role VARCHAR(20) DEFAULT 'member' CHECK (role IN ('owner', 'member')),
  joined_at TIMESTAMPTZ DEFAULT NOW(),
  PRIMARY KEY (team_id, user_id)
);

-- RLS
ALTER TABLE team_members ENABLE ROW LEVEL SECURITY;

CREATE POLICY "Members can view team members" ON team_members
  FOR SELECT USING (
    team_id IN (SELECT team_id FROM team_members WHERE user_id = auth.uid())
  );

CREATE POLICY "Owners can add members" ON team_members
  FOR INSERT WITH CHECK (
    team_id IN (SELECT id FROM teams WHERE owner_id = auth.uid())
    OR user_id = auth.uid()  -- Users can add themselves via invite
  );

CREATE POLICY "Owners can remove members" ON team_members
  FOR DELETE USING (
    team_id IN (SELECT id FROM teams WHERE owner_id = auth.uid())
    OR user_id = auth.uid()  -- Users can leave
  );

-- Auto-add owner as member when team is created
CREATE OR REPLACE FUNCTION add_owner_as_member()
RETURNS TRIGGER AS $$
BEGIN
  INSERT INTO team_members (team_id, user_id, role)
  VALUES (NEW.id, NEW.owner_id, 'owner');
  RETURN NEW;
END;
$$ LANGUAGE plpgsql SECURITY DEFINER;

CREATE TRIGGER teams_add_owner_trigger
  AFTER INSERT ON teams
  FOR EACH ROW
  EXECUTE FUNCTION add_owner_as_member();
```

**Step 2: Commit**

```bash
git add supabase/migrations/004_team_members.sql
git commit -m "feat(db): add team_members table with auto-owner trigger"
```

---

### Task 0.3: Create Collections Table

**Files:**
- Create: `supabase/migrations/005_collections.sql`

**Step 1: Write migration SQL**

```sql
-- 005_collections.sql
-- Video collections (private and shared)

CREATE TABLE collections (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  name VARCHAR(100) NOT NULL,
  owner_id UUID REFERENCES auth.users(id) NOT NULL,
  team_id UUID REFERENCES teams(id) ON DELETE SET NULL,  -- NULL = private
  created_at TIMESTAMPTZ DEFAULT NOW()
);

-- RLS
ALTER TABLE collections ENABLE ROW LEVEL SECURITY;

-- View: own collections OR team collections
CREATE POLICY "Users can view their collections" ON collections
  FOR SELECT USING (
    owner_id = auth.uid() OR
    team_id IN (SELECT team_id FROM team_members WHERE user_id = auth.uid())
  );

-- Create: any user can create
CREATE POLICY "Users can create collections" ON collections
  FOR INSERT WITH CHECK (owner_id = auth.uid());

-- Update: owner only for private, team members for shared
CREATE POLICY "Users can update collections" ON collections
  FOR UPDATE USING (
    owner_id = auth.uid() OR
    team_id IN (SELECT team_id FROM team_members WHERE user_id = auth.uid())
  );

-- Delete: owner only
CREATE POLICY "Owners can delete collections" ON collections
  FOR DELETE USING (owner_id = auth.uid());
```

**Step 2: Commit**

```bash
git add supabase/migrations/005_collections.sql
git commit -m "feat(db): add collections table for private and shared collections"
```

---

### Task 0.4: Create Collection Videos Table

**Files:**
- Create: `supabase/migrations/006_collection_videos.sql`

**Step 1: Write migration SQL**

```sql
-- 006_collection_videos.sql
-- Many-to-many: collections <-> videos

CREATE TABLE collection_videos (
  collection_id UUID REFERENCES collections(id) ON DELETE CASCADE,
  video_aweme_id VARCHAR(50) REFERENCES douyin_videos(aweme_id) ON DELETE CASCADE,
  added_by UUID REFERENCES auth.users(id),
  added_at TIMESTAMPTZ DEFAULT NOW(),
  PRIMARY KEY (collection_id, video_aweme_id)
);

-- RLS
ALTER TABLE collection_videos ENABLE ROW LEVEL SECURITY;

-- View/manage: same as collection access
CREATE POLICY "Collection members can view videos" ON collection_videos
  FOR SELECT USING (
    collection_id IN (
      SELECT id FROM collections WHERE
        owner_id = auth.uid() OR
        team_id IN (SELECT team_id FROM team_members WHERE user_id = auth.uid())
    )
  );

CREATE POLICY "Collection members can add videos" ON collection_videos
  FOR INSERT WITH CHECK (
    collection_id IN (
      SELECT id FROM collections WHERE
        owner_id = auth.uid() OR
        team_id IN (SELECT team_id FROM team_members WHERE user_id = auth.uid())
    )
  );

CREATE POLICY "Collection members can remove videos" ON collection_videos
  FOR DELETE USING (
    collection_id IN (
      SELECT id FROM collections WHERE
        owner_id = auth.uid() OR
        team_id IN (SELECT team_id FROM team_members WHERE user_id = auth.uid())
    )
  );
```

**Step 2: Commit**

```bash
git add supabase/migrations/006_collection_videos.sql
git commit -m "feat(db): add collection_videos junction table"
```

---

### Task 0.5: Create Notifications Tables

**Files:**
- Create: `supabase/migrations/007_notifications.sql`

**Step 1: Write migration SQL**

```sql
-- 007_notifications.sql
-- System and team notifications

CREATE TABLE notifications (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  type VARCHAR(20) NOT NULL CHECK (type IN ('system', 'team')),
  title VARCHAR(200) NOT NULL,
  content TEXT,
  team_id UUID REFERENCES teams(id) ON DELETE CASCADE,  -- NULL for system
  created_by UUID REFERENCES auth.users(id),
  created_at TIMESTAMPTZ DEFAULT NOW()
);

CREATE TABLE user_notifications (
  user_id UUID REFERENCES auth.users(id) ON DELETE CASCADE,
  notification_id UUID REFERENCES notifications(id) ON DELETE CASCADE,
  read_at TIMESTAMPTZ,  -- NULL = unread
  PRIMARY KEY (user_id, notification_id)
);

-- RLS for notifications
ALTER TABLE notifications ENABLE ROW LEVEL SECURITY;

CREATE POLICY "Users can view relevant notifications" ON notifications
  FOR SELECT USING (
    type = 'system' OR
    team_id IN (SELECT team_id FROM team_members WHERE user_id = auth.uid())
  );

-- Only admins can create system notifications (via service role)
-- Team owners can create team notifications
CREATE POLICY "Team owners can create team notifications" ON notifications
  FOR INSERT WITH CHECK (
    type = 'team' AND
    team_id IN (SELECT id FROM teams WHERE owner_id = auth.uid())
  );

-- RLS for user_notifications
ALTER TABLE user_notifications ENABLE ROW LEVEL SECURITY;

CREATE POLICY "Users can view their notification status" ON user_notifications
  FOR SELECT USING (user_id = auth.uid());

CREATE POLICY "Users can update their notification status" ON user_notifications
  FOR ALL USING (user_id = auth.uid());

-- Auto-create user_notification entries for team notifications
CREATE OR REPLACE FUNCTION notify_team_members()
RETURNS TRIGGER AS $$
BEGIN
  IF NEW.type = 'team' AND NEW.team_id IS NOT NULL THEN
    INSERT INTO user_notifications (user_id, notification_id)
    SELECT user_id, NEW.id FROM team_members WHERE team_id = NEW.team_id;
  END IF;
  RETURN NEW;
END;
$$ LANGUAGE plpgsql SECURITY DEFINER;

CREATE TRIGGER notifications_team_trigger
  AFTER INSERT ON notifications
  FOR EACH ROW
  EXECUTE FUNCTION notify_team_members();

-- Index for faster queries
CREATE INDEX idx_user_notifications_unread ON user_notifications(user_id) WHERE read_at IS NULL;
CREATE INDEX idx_notifications_team ON notifications(team_id) WHERE team_id IS NOT NULL;
CREATE INDEX idx_notifications_created ON notifications(created_at DESC);
```

**Step 2: Commit**

```bash
git add supabase/migrations/007_notifications.sql
git commit -m "feat(db): add notifications and user_notifications tables"
```

---

## Phase 1: Internationalization (i18n)

### Task 1.1: Install react-i18next

**Files:**
- Modify: `frontend/package.json`

**Step 1: Install dependencies**

```bash
cd /Volumes/program/project-code/repos/mediahub/.worktrees/user-system-upgrade/frontend
npm install i18next react-i18next
```

**Step 2: Commit**

```bash
git add package.json package-lock.json
git commit -m "feat(i18n): install react-i18next"
```

---

### Task 1.2: Create Translation Files

**Files:**
- Create: `frontend/locales/zh.json`
- Create: `frontend/locales/en.json`

**Step 1: Create Chinese translations**

```json
{
  "nav": {
    "linkParser": "链接解析",
    "myLibrary": "我的媒体库",
    "allVideos": "全部视频",
    "favorites": "我的收藏",
    "sharedCollections": "共享集合",
    "newCollection": "新建集合",
    "dashboard": "数据看板",
    "settings": "设置"
  },
  "user": {
    "profile": "个人资料",
    "account": "账户设置",
    "signOut": "退出登录",
    "joinedTeams": "已加入的团队",
    "createTeam": "创建团队",
    "teamSettings": "团队设置"
  },
  "auth": {
    "login": "登录",
    "register": "注册",
    "email": "邮箱地址",
    "password": "密码",
    "confirmPassword": "确认密码",
    "noAccount": "没有账号？立即注册",
    "hasAccount": "已有账号？立即登录",
    "registerSuccess": "注册成功，请查收验证邮件",
    "passwordMismatch": "两次密码输入不一致"
  },
  "notifications": {
    "title": "通知",
    "markAllRead": "全部已读",
    "system": "系统通知",
    "team": "团队通知",
    "empty": "暂无通知",
    "newMember": "{name} 加入了团队",
    "newVideo": "{name} 添加了新视频到「{collection}」"
  },
  "collections": {
    "create": "新建集合",
    "addTo": "添加到集合",
    "shared": "共享",
    "private": "私人",
    "empty": "集合为空",
    "confirmDelete": "确定删除此集合？"
  },
  "team": {
    "create": "创建团队",
    "name": "团队名称",
    "settings": "团队设置",
    "invite": "邀请成员",
    "copyLink": "复制邀请链接",
    "linkCopied": "链接已复制",
    "members": "成员列表",
    "leave": "离开团队",
    "delete": "删除团队",
    "confirmLeave": "确定离开此团队？",
    "confirmDelete": "确定删除此团队？此操作不可恢复。"
  },
  "common": {
    "save": "保存",
    "cancel": "取消",
    "delete": "删除",
    "edit": "编辑",
    "confirm": "确认",
    "loading": "加载中...",
    "error": "出错了",
    "success": "成功",
    "search": "搜索"
  },
  "parser": {
    "title": "抖音媒体解析器",
    "subtitle": "粘贴分享链接，分析并下载无水印内容",
    "singleLink": "单个链接",
    "batchDownload": "批量下载",
    "placeholder": "粘贴抖音链接 (如 https://v.douyin.com/...)",
    "analyze": "解析",
    "processBatch": "批量处理"
  },
  "library": {
    "title": "我的收藏",
    "subtitle": "管理您保存的下载内容",
    "empty": "暂无内容",
    "searchPlaceholder": "搜索标题、标签、笔记..."
  },
  "dashboard": {
    "title": "数据分析",
    "subtitle": "您的下载习惯实时统计",
    "totalVideos": "总视频数",
    "storageUsed": "已用存储",
    "savedCreators": "创作者数",
    "successRate": "成功率"
  }
}
```

**Step 2: Create English translations**

```json
{
  "nav": {
    "linkParser": "Link Parser",
    "myLibrary": "My Library",
    "allVideos": "All Videos",
    "favorites": "Favorites",
    "sharedCollections": "Shared Collections",
    "newCollection": "New Collection",
    "dashboard": "Dashboard",
    "settings": "Settings"
  },
  "user": {
    "profile": "Profile",
    "account": "Account",
    "signOut": "Sign Out",
    "joinedTeams": "Joined Teams",
    "createTeam": "Create Team",
    "teamSettings": "Team Settings"
  },
  "auth": {
    "login": "Log In",
    "register": "Sign Up",
    "email": "Email Address",
    "password": "Password",
    "confirmPassword": "Confirm Password",
    "noAccount": "Don't have an account? Sign up",
    "hasAccount": "Already have an account? Log in",
    "registerSuccess": "Registration successful. Please check your email.",
    "passwordMismatch": "Passwords do not match"
  },
  "notifications": {
    "title": "Notifications",
    "markAllRead": "Mark all read",
    "system": "System",
    "team": "Team",
    "empty": "No notifications",
    "newMember": "{name} joined the team",
    "newVideo": "{name} added a video to \"{collection}\""
  },
  "collections": {
    "create": "New Collection",
    "addTo": "Add to Collection",
    "shared": "Shared",
    "private": "Private",
    "empty": "Collection is empty",
    "confirmDelete": "Delete this collection?"
  },
  "team": {
    "create": "Create Team",
    "name": "Team Name",
    "settings": "Team Settings",
    "invite": "Invite Members",
    "copyLink": "Copy Invite Link",
    "linkCopied": "Link copied",
    "members": "Members",
    "leave": "Leave Team",
    "delete": "Delete Team",
    "confirmLeave": "Leave this team?",
    "confirmDelete": "Delete this team? This cannot be undone."
  },
  "common": {
    "save": "Save",
    "cancel": "Cancel",
    "delete": "Delete",
    "edit": "Edit",
    "confirm": "Confirm",
    "loading": "Loading...",
    "error": "Error",
    "success": "Success",
    "search": "Search"
  },
  "parser": {
    "title": "Douyin Media Parser",
    "subtitle": "Paste shared links to analyze and download content watermark-free",
    "singleLink": "Single Link",
    "batchDownload": "Batch Download",
    "placeholder": "Paste Douyin link (e.g., https://v.douyin.com/...)",
    "analyze": "Analyze",
    "processBatch": "Process Batch"
  },
  "library": {
    "title": "Your Collection",
    "subtitle": "Manage your saved downloads",
    "empty": "No content yet",
    "searchPlaceholder": "Search title, tags, notes..."
  },
  "dashboard": {
    "title": "Analytics",
    "subtitle": "Real-time statistics of your downloading habits",
    "totalVideos": "Total Videos",
    "storageUsed": "Storage Used",
    "savedCreators": "Saved Creators",
    "successRate": "Success Rate"
  }
}
```

**Step 3: Commit**

```bash
git add frontend/locales/zh.json frontend/locales/en.json
git commit -m "feat(i18n): add Chinese and English translation files"
```

---

### Task 1.3: Create i18n Configuration

**Files:**
- Create: `frontend/i18n.ts`
- Modify: `frontend/index.tsx`

**Step 1: Create i18n config**

```typescript
// frontend/i18n.ts
import i18n from 'i18next';
import { initReactI18next } from 'react-i18next';
import en from './locales/en.json';
import zh from './locales/zh.json';

const savedLang = localStorage.getItem('language') || 'zh';

i18n.use(initReactI18next).init({
  resources: {
    en: { translation: en },
    zh: { translation: zh },
  },
  lng: savedLang,
  fallbackLng: 'en',
  interpolation: {
    escapeValue: false,
  },
});

export const changeLanguage = (lang: 'en' | 'zh') => {
  i18n.changeLanguage(lang);
  localStorage.setItem('language', lang);
};

export const getCurrentLanguage = () => i18n.language as 'en' | 'zh';

export default i18n;
```

**Step 2: Import i18n in index.tsx**

Add at the top of `frontend/index.tsx`:

```typescript
import './i18n';
```

**Step 3: Commit**

```bash
git add frontend/i18n.ts frontend/index.tsx
git commit -m "feat(i18n): configure i18next with language persistence"
```

---

### Task 1.4: Create Language Switcher Component

**Files:**
- Create: `frontend/components/LanguageSwitcher.tsx`

**Step 1: Write component**

```typescript
// frontend/components/LanguageSwitcher.tsx
import React, { useState, useRef, useEffect } from 'react';
import { Globe, ChevronDown } from 'lucide-react';
import { useTranslation } from 'react-i18next';
import { changeLanguage, getCurrentLanguage } from '../i18n';

interface LanguageSwitcherProps {
  variant?: 'dropdown' | 'inline';
}

export const LanguageSwitcher: React.FC<LanguageSwitcherProps> = ({ variant = 'dropdown' }) => {
  const { i18n } = useTranslation();
  const [isOpen, setIsOpen] = useState(false);
  const dropdownRef = useRef<HTMLDivElement>(null);

  const languages = [
    { code: 'en', label: 'English', short: 'EN' },
    { code: 'zh', label: '中文', short: '中' },
  ];

  const currentLang = languages.find(l => l.code === i18n.language) || languages[0];

  useEffect(() => {
    const handleClickOutside = (e: MouseEvent) => {
      if (dropdownRef.current && !dropdownRef.current.contains(e.target as Node)) {
        setIsOpen(false);
      }
    };
    document.addEventListener('mousedown', handleClickOutside);
    return () => document.removeEventListener('mousedown', handleClickOutside);
  }, []);

  const handleChange = (code: 'en' | 'zh') => {
    changeLanguage(code);
    setIsOpen(false);
  };

  if (variant === 'inline') {
    return (
      <div className="flex items-center gap-2">
        {languages.map(lang => (
          <button
            key={lang.code}
            onClick={() => handleChange(lang.code as 'en' | 'zh')}
            className={`px-3 py-1.5 text-sm rounded-lg transition-colors ${
              i18n.language === lang.code
                ? 'bg-indigo-600 text-white'
                : 'text-zinc-400 hover:text-white hover:bg-zinc-800'
            }`}
          >
            {lang.label}
          </button>
        ))}
      </div>
    );
  }

  return (
    <div className="relative" ref={dropdownRef}>
      <button
        onClick={() => setIsOpen(!isOpen)}
        className="flex items-center gap-2 px-3 py-2 rounded-lg text-zinc-400 hover:text-white hover:bg-zinc-800/50 transition-colors"
      >
        <Globe size={18} />
        <span className="text-sm font-medium">{currentLang.short}</span>
        <ChevronDown size={14} className={`transition-transform ${isOpen ? 'rotate-180' : ''}`} />
      </button>

      {isOpen && (
        <div className="absolute top-full right-0 mt-2 bg-zinc-900 border border-zinc-800 rounded-xl shadow-xl py-1 min-w-[120px] z-50 animate-in fade-in slide-in-from-top-2 duration-200">
          {languages.map(lang => (
            <button
              key={lang.code}
              onClick={() => handleChange(lang.code as 'en' | 'zh')}
              className={`w-full px-4 py-2 text-left text-sm transition-colors ${
                i18n.language === lang.code
                  ? 'text-indigo-400 bg-indigo-500/10'
                  : 'text-zinc-300 hover:bg-zinc-800'
              }`}
            >
              {lang.label}
            </button>
          ))}
        </div>
      )}
    </div>
  );
};
```

**Step 2: Commit**

```bash
git add frontend/components/LanguageSwitcher.tsx
git commit -m "feat(i18n): add LanguageSwitcher dropdown component"
```

---

## Phase 2: Registration & Auth Updates

### Task 2.1: Update AuthOverlay with Registration

**Files:**
- Modify: `frontend/components/AuthOverlay.tsx`

**Step 1: Add registration form to AuthOverlay**

Replace the entire AuthOverlay.tsx with updated version that includes:
- Login/Register tab toggle
- Registration form with email, password, confirm password
- Password validation
- Supabase signUp integration

**Full code provided in implementation - key additions:**

```typescript
// Add state for auth mode
const [authMode, setAuthMode] = useState<'login' | 'register'>('login');
const [confirmPassword, setConfirmPassword] = useState('');
const [error, setError] = useState<string | null>(null);

// Add registration handler
const handleRegister = async () => {
  if (password !== confirmPassword) {
    setError(t('auth.passwordMismatch'));
    return;
  }

  const supabase = getSupabaseClient();
  if (supabase) {
    const { data, error } = await supabase.auth.signUp({ email, password });
    if (error) {
      setError(error.message);
      return;
    }
    alert(t('auth.registerSuccess'));
    setAuthMode('login');
  }
};
```

**Step 2: Commit**

```bash
git add frontend/components/AuthOverlay.tsx
git commit -m "feat(auth): add registration form to AuthOverlay"
```

---

## Phase 3: Header & User Dropdown

### Task 3.1: Create Header Component

**Files:**
- Create: `frontend/components/Header.tsx`

**Step 1: Write Header component**

```typescript
// frontend/components/Header.tsx
import React from 'react';
import { Bell, User } from 'lucide-react';
import { useTranslation } from 'react-i18next';
import { LanguageSwitcher } from './LanguageSwitcher';

interface HeaderProps {
  userProfile: {
    name: string;
    email: string;
    avatarUrl?: string;
  };
  unreadCount: number;
  onNotificationClick: () => void;
  onUserClick: () => void;
}

export const Header: React.FC<HeaderProps> = ({
  userProfile,
  unreadCount,
  onNotificationClick,
  onUserClick,
}) => {
  const { t } = useTranslation();

  return (
    <header className="hidden md:flex h-16 items-center justify-end gap-4 px-6 border-b border-zinc-800 bg-zinc-950/80 backdrop-blur-sm fixed top-0 right-0 left-64 z-30">
      {/* Language Switcher */}
      <LanguageSwitcher />

      {/* Notification Bell */}
      <button
        onClick={onNotificationClick}
        className="relative p-2 rounded-lg text-zinc-400 hover:text-white hover:bg-zinc-800/50 transition-colors"
      >
        <Bell size={20} />
        {unreadCount > 0 && (
          <span className="absolute -top-0.5 -right-0.5 min-w-[18px] h-[18px] bg-red-500 text-white text-xs font-bold rounded-full flex items-center justify-center px-1">
            {unreadCount > 99 ? '99+' : unreadCount}
          </span>
        )}
      </button>

      {/* User Avatar */}
      <button
        onClick={onUserClick}
        className="flex items-center gap-3 pl-3 pr-1 py-1 rounded-full hover:bg-zinc-800/50 transition-colors"
      >
        <span className="text-sm text-zinc-300 font-medium hidden lg:block">
          {userProfile.name}
        </span>
        <div className="w-9 h-9 rounded-full bg-gradient-to-br from-indigo-500 to-purple-600 flex items-center justify-center overflow-hidden border-2 border-zinc-700">
          {userProfile.avatarUrl ? (
            <img src={userProfile.avatarUrl} alt="Avatar" className="w-full h-full object-cover" />
          ) : (
            <User size={18} className="text-white" />
          )}
        </div>
      </button>
    </header>
  );
};
```

**Step 2: Commit**

```bash
git add frontend/components/Header.tsx
git commit -m "feat(ui): add Header component with notifications and user avatar"
```

---

### Task 3.2: Create UserDropdown Component

**Files:**
- Create: `frontend/components/UserDropdown.tsx`

**Step 1: Write UserDropdown component**

```typescript
// frontend/components/UserDropdown.tsx
import React, { useEffect, useRef } from 'react';
import { User, Settings, LogOut, Users, Plus, ChevronRight, Check } from 'lucide-react';
import { useTranslation } from 'react-i18next';
import { LanguageSwitcher } from './LanguageSwitcher';

interface Team {
  id: string;
  name: string;
  isOwner: boolean;
}

interface UserDropdownProps {
  isOpen: boolean;
  onClose: () => void;
  user: {
    name: string;
    email: string;
    avatarUrl?: string;
  };
  teams: Team[];
  activeTeamId: string | null;
  onTeamSelect: (teamId: string | null) => void;
  onCreateTeam: () => void;
  onTeamSettings: (teamId: string) => void;
  onProfile: () => void;
  onAccount: () => void;
  onLogout: () => void;
}

export const UserDropdown: React.FC<UserDropdownProps> = ({
  isOpen,
  onClose,
  user,
  teams,
  activeTeamId,
  onTeamSelect,
  onCreateTeam,
  onTeamSettings,
  onProfile,
  onAccount,
  onLogout,
}) => {
  const { t } = useTranslation();
  const dropdownRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    const handleClickOutside = (e: MouseEvent) => {
      if (dropdownRef.current && !dropdownRef.current.contains(e.target as Node)) {
        onClose();
      }
    };
    const handleEsc = (e: KeyboardEvent) => {
      if (e.key === 'Escape') onClose();
    };

    if (isOpen) {
      document.addEventListener('mousedown', handleClickOutside);
      document.addEventListener('keydown', handleEsc);
    }
    return () => {
      document.removeEventListener('mousedown', handleClickOutside);
      document.removeEventListener('keydown', handleEsc);
    };
  }, [isOpen, onClose]);

  if (!isOpen) return null;

  return (
    <>
      {/* Backdrop */}
      <div className="fixed inset-0 bg-black/20 z-40" />

      {/* Dropdown Panel */}
      <div
        ref={dropdownRef}
        className="fixed top-0 right-0 h-full w-72 bg-zinc-900 border-l border-zinc-800 shadow-2xl z-50 animate-in slide-in-from-right duration-300"
      >
        {/* User Info */}
        <div className="p-5 border-b border-zinc-800">
          <div className="flex items-center gap-3">
            <div className="w-12 h-12 rounded-full bg-gradient-to-br from-indigo-500 to-purple-600 flex items-center justify-center overflow-hidden">
              {user.avatarUrl ? (
                <img src={user.avatarUrl} alt="Avatar" className="w-full h-full object-cover" />
              ) : (
                <User size={24} className="text-white" />
              )}
            </div>
            <div className="flex-1 min-w-0">
              <p className="font-semibold text-white truncate">{user.name}</p>
              <p className="text-sm text-zinc-500 truncate">{user.email}</p>
            </div>
          </div>
        </div>

        {/* Teams Section */}
        <div className="p-3 border-b border-zinc-800">
          <p className="px-2 text-xs font-semibold text-zinc-500 uppercase tracking-wider mb-2">
            {t('user.joinedTeams')}
          </p>
          <div className="space-y-1">
            {teams.map(team => (
              <div
                key={team.id}
                className="flex items-center justify-between px-2 py-2 rounded-lg hover:bg-zinc-800/50 transition-colors group"
              >
                <button
                  onClick={() => onTeamSelect(team.id)}
                  className="flex items-center gap-2 flex-1 min-w-0"
                >
                  <div className="w-7 h-7 rounded-lg bg-indigo-600/20 flex items-center justify-center text-indigo-400 text-sm font-bold">
                    {team.name.charAt(0).toUpperCase()}
                  </div>
                  <span className="text-sm text-zinc-300 truncate">{team.name}</span>
                </button>
                <div className="flex items-center gap-1">
                  {team.isOwner && (
                    <button
                      onClick={() => onTeamSettings(team.id)}
                      className="p-1 rounded text-zinc-500 hover:text-white hover:bg-zinc-700 opacity-0 group-hover:opacity-100 transition-all"
                    >
                      <Settings size={14} />
                    </button>
                  )}
                  {activeTeamId === team.id && (
                    <Check size={16} className="text-indigo-400" />
                  )}
                </div>
              </div>
            ))}
            <button
              onClick={onCreateTeam}
              className="flex items-center gap-2 w-full px-2 py-2 rounded-lg text-indigo-400 hover:bg-indigo-500/10 transition-colors"
            >
              <Plus size={18} />
              <span className="text-sm font-medium">{t('user.createTeam')}</span>
            </button>
          </div>
        </div>

        {/* Menu Items */}
        <div className="p-3 space-y-1">
          <button
            onClick={onProfile}
            className="flex items-center gap-3 w-full px-3 py-2.5 rounded-lg text-zinc-300 hover:bg-zinc-800/50 transition-colors"
          >
            <User size={18} className="text-zinc-500" />
            <span className="text-sm">{t('user.profile')}</span>
          </button>
          <button
            onClick={onAccount}
            className="flex items-center gap-3 w-full px-3 py-2.5 rounded-lg text-zinc-300 hover:bg-zinc-800/50 transition-colors"
          >
            <Settings size={18} className="text-zinc-500" />
            <span className="text-sm">{t('user.account')}</span>
          </button>

          {/* Language Switcher (Mobile) */}
          <div className="md:hidden px-3 py-2.5">
            <LanguageSwitcher variant="inline" />
          </div>
        </div>

        {/* Logout */}
        <div className="absolute bottom-0 left-0 right-0 p-3 border-t border-zinc-800">
          <button
            onClick={onLogout}
            className="flex items-center gap-3 w-full px-3 py-2.5 rounded-lg text-red-400 hover:bg-red-500/10 transition-colors"
          >
            <LogOut size={18} />
            <span className="text-sm font-medium">{t('user.signOut')}</span>
          </button>
        </div>
      </div>
    </>
  );
};
```

**Step 2: Commit**

```bash
git add frontend/components/UserDropdown.tsx
git commit -m "feat(ui): add UserDropdown slide-out panel with teams"
```

---

### Task 3.3: Create NotificationPanel Component

**Files:**
- Create: `frontend/components/NotificationPanel.tsx`

**Step 1: Write NotificationPanel component**

```typescript
// frontend/components/NotificationPanel.tsx
import React, { useEffect, useRef } from 'react';
import { X, Bell, Users, CheckCheck } from 'lucide-react';
import { useTranslation } from 'react-i18next';

interface Notification {
  id: string;
  type: 'system' | 'team';
  title: string;
  content?: string;
  createdAt: string;
  read: boolean;
}

interface NotificationPanelProps {
  isOpen: boolean;
  onClose: () => void;
  notifications: Notification[];
  onMarkRead: (id: string) => void;
  onMarkAllRead: () => void;
}

export const NotificationPanel: React.FC<NotificationPanelProps> = ({
  isOpen,
  onClose,
  notifications,
  onMarkRead,
  onMarkAllRead,
}) => {
  const { t } = useTranslation();
  const panelRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    const handleClickOutside = (e: MouseEvent) => {
      if (panelRef.current && !panelRef.current.contains(e.target as Node)) {
        onClose();
      }
    };
    const handleEsc = (e: KeyboardEvent) => {
      if (e.key === 'Escape') onClose();
    };

    if (isOpen) {
      document.addEventListener('mousedown', handleClickOutside);
      document.addEventListener('keydown', handleEsc);
    }
    return () => {
      document.removeEventListener('mousedown', handleClickOutside);
      document.removeEventListener('keydown', handleEsc);
    };
  }, [isOpen, onClose]);

  if (!isOpen) return null;

  const systemNotifications = notifications.filter(n => n.type === 'system');
  const teamNotifications = notifications.filter(n => n.type === 'team');
  const hasUnread = notifications.some(n => !n.read);

  const formatTime = (dateStr: string) => {
    const date = new Date(dateStr);
    const now = new Date();
    const diffMs = now.getTime() - date.getTime();
    const diffMins = Math.floor(diffMs / 60000);
    const diffHours = Math.floor(diffMs / 3600000);
    const diffDays = Math.floor(diffMs / 86400000);

    if (diffMins < 60) return `${diffMins}m`;
    if (diffHours < 24) return `${diffHours}h`;
    if (diffDays < 7) return `${diffDays}d`;
    return date.toLocaleDateString();
  };

  const NotificationItem = ({ item }: { item: Notification }) => (
    <button
      onClick={() => onMarkRead(item.id)}
      className={`w-full text-left p-3 rounded-lg transition-colors ${
        item.read ? 'bg-transparent hover:bg-zinc-800/30' : 'bg-zinc-800/50 hover:bg-zinc-800'
      }`}
    >
      <div className="flex items-start justify-between gap-2">
        <div className="flex-1 min-w-0">
          <p className={`text-sm font-medium truncate ${item.read ? 'text-zinc-400' : 'text-zinc-200'}`}>
            {item.title}
          </p>
          {item.content && (
            <p className="text-xs text-zinc-500 truncate mt-0.5">{item.content}</p>
          )}
        </div>
        <div className="flex items-center gap-2 flex-shrink-0">
          <span className="text-xs text-zinc-600">{formatTime(item.createdAt)}</span>
          {!item.read && <div className="w-2 h-2 rounded-full bg-indigo-500" />}
        </div>
      </div>
    </button>
  );

  return (
    <div
      ref={panelRef}
      className="absolute top-full right-0 mt-2 w-80 bg-zinc-900 border border-zinc-800 rounded-xl shadow-2xl z-50 animate-in fade-in slide-in-from-top-2 duration-200 overflow-hidden"
    >
      {/* Header */}
      <div className="flex items-center justify-between px-4 py-3 border-b border-zinc-800">
        <h3 className="font-semibold text-zinc-200">{t('notifications.title')}</h3>
        {hasUnread && (
          <button
            onClick={onMarkAllRead}
            className="flex items-center gap-1 text-xs text-indigo-400 hover:text-indigo-300 transition-colors"
          >
            <CheckCheck size={14} />
            <span>{t('notifications.markAllRead')}</span>
          </button>
        )}
      </div>

      {/* Content */}
      <div className="max-h-[400px] overflow-y-auto">
        {notifications.length === 0 ? (
          <div className="flex flex-col items-center justify-center py-10 text-zinc-500">
            <Bell size={32} className="mb-2 opacity-50" />
            <p className="text-sm">{t('notifications.empty')}</p>
          </div>
        ) : (
          <div className="p-2 space-y-3">
            {/* System Notifications */}
            {systemNotifications.length > 0 && (
              <div>
                <p className="px-2 py-1 text-xs font-semibold text-zinc-500 uppercase tracking-wider flex items-center gap-1">
                  <Bell size={12} />
                  {t('notifications.system')}
                </p>
                <div className="space-y-1">
                  {systemNotifications.map(n => (
                    <NotificationItem key={n.id} item={n} />
                  ))}
                </div>
              </div>
            )}

            {/* Team Notifications */}
            {teamNotifications.length > 0 && (
              <div>
                <p className="px-2 py-1 text-xs font-semibold text-zinc-500 uppercase tracking-wider flex items-center gap-1">
                  <Users size={12} />
                  {t('notifications.team')}
                </p>
                <div className="space-y-1">
                  {teamNotifications.map(n => (
                    <NotificationItem key={n.id} item={n} />
                  ))}
                </div>
              </div>
            )}
          </div>
        )}
      </div>
    </div>
  );
};
```

**Step 2: Commit**

```bash
git add frontend/components/NotificationPanel.tsx
git commit -m "feat(ui): add NotificationPanel with system and team sections"
```

---

## Phase 4: Collection System

### Task 4.1: Create CollectionPicker Component

**Files:**
- Create: `frontend/components/CollectionPicker.tsx`

**Step 1: Write CollectionPicker component**

```typescript
// frontend/components/CollectionPicker.tsx
import React, { useState, useEffect, useRef } from 'react';
import { X, Plus, Check, Users, Lock } from 'lucide-react';
import { useTranslation } from 'react-i18next';

interface Collection {
  id: string;
  name: string;
  isShared: boolean;
  videoCount: number;
}

interface CollectionPickerProps {
  isOpen: boolean;
  onClose: () => void;
  collections: Collection[];
  selectedIds: string[];
  onToggle: (collectionId: string) => void;
  onCreate: (name: string, isShared: boolean) => void;
}

export const CollectionPicker: React.FC<CollectionPickerProps> = ({
  isOpen,
  onClose,
  collections,
  selectedIds,
  onToggle,
  onCreate,
}) => {
  const { t } = useTranslation();
  const [isCreating, setIsCreating] = useState(false);
  const [newName, setNewName] = useState('');
  const [newIsShared, setNewIsShared] = useState(false);
  const panelRef = useRef<HTMLDivElement>(null);
  const inputRef = useRef<HTMLInputElement>(null);

  useEffect(() => {
    const handleClickOutside = (e: MouseEvent) => {
      if (panelRef.current && !panelRef.current.contains(e.target as Node)) {
        onClose();
      }
    };
    if (isOpen) {
      document.addEventListener('mousedown', handleClickOutside);
    }
    return () => document.removeEventListener('mousedown', handleClickOutside);
  }, [isOpen, onClose]);

  useEffect(() => {
    if (isCreating && inputRef.current) {
      inputRef.current.focus();
    }
  }, [isCreating]);

  if (!isOpen) return null;

  const handleCreate = () => {
    if (newName.trim()) {
      onCreate(newName.trim(), newIsShared);
      setNewName('');
      setNewIsShared(false);
      setIsCreating(false);
    }
  };

  return (
    <div
      ref={panelRef}
      className="absolute bottom-full right-0 mb-2 w-64 bg-zinc-900 border border-zinc-800 rounded-xl shadow-2xl z-50 animate-in fade-in slide-in-from-bottom-2 duration-200 overflow-hidden"
    >
      {/* Header */}
      <div className="flex items-center justify-between px-4 py-3 border-b border-zinc-800">
        <h3 className="font-medium text-zinc-200 text-sm">{t('collections.addTo')}</h3>
        <button onClick={onClose} className="text-zinc-500 hover:text-zinc-300">
          <X size={16} />
        </button>
      </div>

      {/* Collections List */}
      <div className="max-h-[240px] overflow-y-auto p-2 space-y-1">
        {collections.map(collection => (
          <button
            key={collection.id}
            onClick={() => onToggle(collection.id)}
            className="flex items-center justify-between w-full px-3 py-2 rounded-lg hover:bg-zinc-800/50 transition-colors group"
          >
            <div className="flex items-center gap-2 min-w-0">
              <div className={`w-5 h-5 rounded border flex items-center justify-center transition-colors ${
                selectedIds.includes(collection.id)
                  ? 'bg-indigo-500 border-indigo-500'
                  : 'border-zinc-600 group-hover:border-zinc-500'
              }`}>
                {selectedIds.includes(collection.id) && <Check size={12} className="text-white" />}
              </div>
              <span className="text-sm text-zinc-300 truncate">{collection.name}</span>
              {collection.isShared ? (
                <Users size={12} className="text-indigo-400 flex-shrink-0" />
              ) : (
                <Lock size={12} className="text-zinc-600 flex-shrink-0" />
              )}
            </div>
            <span className="text-xs text-zinc-600">{collection.videoCount}</span>
          </button>
        ))}
      </div>

      {/* Create New */}
      <div className="border-t border-zinc-800 p-2">
        {isCreating ? (
          <div className="space-y-2">
            <input
              ref={inputRef}
              type="text"
              value={newName}
              onChange={(e) => setNewName(e.target.value)}
              onKeyDown={(e) => e.key === 'Enter' && handleCreate()}
              placeholder={t('team.name')}
              className="w-full px-3 py-2 bg-zinc-800 border border-zinc-700 rounded-lg text-sm text-zinc-200 placeholder-zinc-500 outline-none focus:border-indigo-500"
            />
            <div className="flex items-center justify-between">
              <label className="flex items-center gap-2 text-xs text-zinc-400 cursor-pointer">
                <input
                  type="checkbox"
                  checked={newIsShared}
                  onChange={(e) => setNewIsShared(e.target.checked)}
                  className="rounded border-zinc-600"
                />
                <Users size={12} />
                {t('collections.shared')}
              </label>
              <div className="flex gap-2">
                <button
                  onClick={() => setIsCreating(false)}
                  className="px-2 py-1 text-xs text-zinc-500 hover:text-zinc-300"
                >
                  {t('common.cancel')}
                </button>
                <button
                  onClick={handleCreate}
                  disabled={!newName.trim()}
                  className="px-3 py-1 text-xs bg-indigo-600 text-white rounded-lg hover:bg-indigo-500 disabled:opacity-50"
                >
                  {t('common.save')}
                </button>
              </div>
            </div>
          </div>
        ) : (
          <button
            onClick={() => setIsCreating(true)}
            className="flex items-center gap-2 w-full px-3 py-2 rounded-lg text-indigo-400 hover:bg-indigo-500/10 transition-colors"
          >
            <Plus size={16} />
            <span className="text-sm font-medium">{t('collections.create')}</span>
          </button>
        )}
      </div>
    </div>
  );
};
```

**Step 2: Commit**

```bash
git add frontend/components/CollectionPicker.tsx
git commit -m "feat(ui): add CollectionPicker component for video management"
```

---

## Phase 5: Integration

### Task 5.1: Update Types

**Files:**
- Modify: `frontend/types.ts`

**Step 1: Add new type definitions**

Add to `frontend/types.ts`:

```typescript
// Team types
export interface Team {
  id: string;
  name: string;
  owner_id: string;
  invite_code: string;
  created_at: string;
}

export interface TeamMember {
  team_id: string;
  user_id: string;
  role: 'owner' | 'member';
  joined_at: string;
  // Joined from auth.users
  email?: string;
  name?: string;
}

// Collection types
export interface Collection {
  id: string;
  name: string;
  owner_id: string;
  team_id: string | null;
  created_at: string;
  // Computed
  video_count?: number;
  is_shared?: boolean;
}

export interface CollectionVideo {
  collection_id: string;
  video_aweme_id: string;
  added_by: string;
  added_at: string;
}

// Notification types
export interface Notification {
  id: string;
  type: 'system' | 'team';
  title: string;
  content: string | null;
  team_id: string | null;
  created_by: string | null;
  created_at: string;
}

export interface UserNotification {
  user_id: string;
  notification_id: string;
  read_at: string | null;
  // Joined
  notification?: Notification;
}
```

**Step 2: Commit**

```bash
git add frontend/types.ts
git commit -m "feat(types): add Team, Collection, and Notification types"
```

---

### Task 5.2: Create Service Files

**Files:**
- Create: `frontend/services/teamService.ts`
- Create: `frontend/services/collectionService.ts`
- Create: `frontend/services/notificationService.ts`

**Step 1: Create teamService.ts**

```typescript
// frontend/services/teamService.ts
import { getSupabaseClient } from '../supabaseClient';
import { Team, TeamMember } from '../types';

export const fetchMyTeams = async (): Promise<Team[]> => {
  const supabase = getSupabaseClient();
  if (!supabase) return [];

  const { data: memberships } = await supabase
    .from('team_members')
    .select('team_id');

  if (!memberships?.length) return [];

  const teamIds = memberships.map(m => m.team_id);
  const { data, error } = await supabase
    .from('teams')
    .select('*')
    .in('id', teamIds)
    .order('created_at', { ascending: false });

  if (error) throw error;
  return data || [];
};

export const createTeam = async (name: string): Promise<Team> => {
  const supabase = getSupabaseClient();
  if (!supabase) throw new Error('Supabase not configured');

  const { data: { user } } = await supabase.auth.getUser();
  if (!user) throw new Error('Not authenticated');

  const { data, error } = await supabase
    .from('teams')
    .insert({ name, owner_id: user.id })
    .select()
    .single();

  if (error) throw error;
  return data;
};

export const joinTeamByCode = async (inviteCode: string): Promise<Team> => {
  const supabase = getSupabaseClient();
  if (!supabase) throw new Error('Supabase not configured');

  const { data: { user } } = await supabase.auth.getUser();
  if (!user) throw new Error('Not authenticated');

  // Find team by invite code
  const { data: team, error: teamError } = await supabase
    .from('teams')
    .select('*')
    .eq('invite_code', inviteCode.toUpperCase())
    .single();

  if (teamError || !team) throw new Error('Invalid invite code');

  // Add member
  const { error: memberError } = await supabase
    .from('team_members')
    .insert({ team_id: team.id, user_id: user.id, role: 'member' });

  if (memberError) {
    if (memberError.code === '23505') throw new Error('Already a member');
    throw memberError;
  }

  return team;
};

export const leaveTeam = async (teamId: string): Promise<void> => {
  const supabase = getSupabaseClient();
  if (!supabase) throw new Error('Supabase not configured');

  const { data: { user } } = await supabase.auth.getUser();
  if (!user) throw new Error('Not authenticated');

  const { error } = await supabase
    .from('team_members')
    .delete()
    .eq('team_id', teamId)
    .eq('user_id', user.id);

  if (error) throw error;
};

export const deleteTeam = async (teamId: string): Promise<void> => {
  const supabase = getSupabaseClient();
  if (!supabase) throw new Error('Supabase not configured');

  const { error } = await supabase
    .from('teams')
    .delete()
    .eq('id', teamId);

  if (error) throw error;
};

export const fetchTeamMembers = async (teamId: string): Promise<TeamMember[]> => {
  const supabase = getSupabaseClient();
  if (!supabase) return [];

  const { data, error } = await supabase
    .from('team_members')
    .select('*')
    .eq('team_id', teamId)
    .order('joined_at', { ascending: true });

  if (error) throw error;
  return data || [];
};
```

**Step 2: Create collectionService.ts**

```typescript
// frontend/services/collectionService.ts
import { getSupabaseClient } from '../supabaseClient';
import { Collection } from '../types';

export const fetchMyCollections = async (): Promise<Collection[]> => {
  const supabase = getSupabaseClient();
  if (!supabase) return [];

  const { data: { user } } = await supabase.auth.getUser();
  if (!user) return [];

  // Get user's team IDs
  const { data: memberships } = await supabase
    .from('team_members')
    .select('team_id')
    .eq('user_id', user.id);

  const teamIds = memberships?.map(m => m.team_id) || [];

  // Fetch collections (own + team)
  let query = supabase
    .from('collections')
    .select('*, collection_videos(count)')
    .order('created_at', { ascending: false });

  if (teamIds.length > 0) {
    query = query.or(`owner_id.eq.${user.id},team_id.in.(${teamIds.join(',')})`);
  } else {
    query = query.eq('owner_id', user.id);
  }

  const { data, error } = await query;
  if (error) throw error;

  return (data || []).map(c => ({
    ...c,
    video_count: c.collection_videos?.[0]?.count || 0,
    is_shared: !!c.team_id,
  }));
};

export const createCollection = async (name: string, teamId?: string): Promise<Collection> => {
  const supabase = getSupabaseClient();
  if (!supabase) throw new Error('Supabase not configured');

  const { data: { user } } = await supabase.auth.getUser();
  if (!user) throw new Error('Not authenticated');

  const { data, error } = await supabase
    .from('collections')
    .insert({
      name,
      owner_id: user.id,
      team_id: teamId || null,
    })
    .select()
    .single();

  if (error) throw error;
  return { ...data, video_count: 0, is_shared: !!teamId };
};

export const deleteCollection = async (collectionId: string): Promise<void> => {
  const supabase = getSupabaseClient();
  if (!supabase) throw new Error('Supabase not configured');

  const { error } = await supabase
    .from('collections')
    .delete()
    .eq('id', collectionId);

  if (error) throw error;
};

export const addVideoToCollection = async (collectionId: string, videoAwemeId: string): Promise<void> => {
  const supabase = getSupabaseClient();
  if (!supabase) throw new Error('Supabase not configured');

  const { data: { user } } = await supabase.auth.getUser();
  if (!user) throw new Error('Not authenticated');

  const { error } = await supabase
    .from('collection_videos')
    .insert({
      collection_id: collectionId,
      video_aweme_id: videoAwemeId,
      added_by: user.id,
    });

  if (error && error.code !== '23505') throw error; // Ignore duplicate
};

export const removeVideoFromCollection = async (collectionId: string, videoAwemeId: string): Promise<void> => {
  const supabase = getSupabaseClient();
  if (!supabase) throw new Error('Supabase not configured');

  const { error } = await supabase
    .from('collection_videos')
    .delete()
    .eq('collection_id', collectionId)
    .eq('video_aweme_id', videoAwemeId);

  if (error) throw error;
};

export const fetchVideoCollections = async (videoAwemeId: string): Promise<string[]> => {
  const supabase = getSupabaseClient();
  if (!supabase) return [];

  const { data, error } = await supabase
    .from('collection_videos')
    .select('collection_id')
    .eq('video_aweme_id', videoAwemeId);

  if (error) throw error;
  return (data || []).map(cv => cv.collection_id);
};
```

**Step 3: Create notificationService.ts**

```typescript
// frontend/services/notificationService.ts
import { getSupabaseClient } from '../supabaseClient';
import { Notification, UserNotification } from '../types';

export interface NotificationWithRead extends Notification {
  read: boolean;
}

export const fetchNotifications = async (): Promise<NotificationWithRead[]> => {
  const supabase = getSupabaseClient();
  if (!supabase) return [];

  const { data: { user } } = await supabase.auth.getUser();
  if (!user) return [];

  // Get user's team IDs
  const { data: memberships } = await supabase
    .from('team_members')
    .select('team_id')
    .eq('user_id', user.id);

  const teamIds = memberships?.map(m => m.team_id) || [];

  // Get notifications (system + team)
  let query = supabase
    .from('notifications')
    .select('*')
    .order('created_at', { ascending: false })
    .limit(50);

  if (teamIds.length > 0) {
    query = query.or(`type.eq.system,team_id.in.(${teamIds.join(',')})`);
  } else {
    query = query.eq('type', 'system');
  }

  const { data: notifications, error } = await query;
  if (error) throw error;
  if (!notifications?.length) return [];

  // Get read status
  const notificationIds = notifications.map(n => n.id);
  const { data: readStatus } = await supabase
    .from('user_notifications')
    .select('notification_id, read_at')
    .eq('user_id', user.id)
    .in('notification_id', notificationIds);

  const readMap = new Map(readStatus?.map(r => [r.notification_id, !!r.read_at]) || []);

  return notifications.map(n => ({
    ...n,
    read: readMap.get(n.id) || false,
  }));
};

export const markAsRead = async (notificationId: string): Promise<void> => {
  const supabase = getSupabaseClient();
  if (!supabase) throw new Error('Supabase not configured');

  const { data: { user } } = await supabase.auth.getUser();
  if (!user) throw new Error('Not authenticated');

  await supabase
    .from('user_notifications')
    .upsert({
      user_id: user.id,
      notification_id: notificationId,
      read_at: new Date().toISOString(),
    });
};

export const markAllAsRead = async (): Promise<void> => {
  const supabase = getSupabaseClient();
  if (!supabase) throw new Error('Supabase not configured');

  const { data: { user } } = await supabase.auth.getUser();
  if (!user) throw new Error('Not authenticated');

  // Get all unread notification IDs for this user
  const notifications = await fetchNotifications();
  const unreadIds = notifications.filter(n => !n.read).map(n => n.id);

  if (unreadIds.length === 0) return;

  const upserts = unreadIds.map(id => ({
    user_id: user.id,
    notification_id: id,
    read_at: new Date().toISOString(),
  }));

  await supabase.from('user_notifications').upsert(upserts);
};

export const getUnreadCount = async (): Promise<number> => {
  const notifications = await fetchNotifications();
  return notifications.filter(n => !n.read).length;
};
```

**Step 4: Commit**

```bash
git add frontend/services/teamService.ts frontend/services/collectionService.ts frontend/services/notificationService.ts
git commit -m "feat(services): add team, collection, and notification services"
```

---

### Task 5.3: Integrate Components into App.tsx

**Files:**
- Modify: `frontend/App.tsx`

**Step 1: Import new components and integrate**

This task involves significant changes to App.tsx:
1. Import Header, UserDropdown, NotificationPanel, CollectionPicker
2. Add state for teams, collections, notifications
3. Add Header to authenticated layout
4. Wire up all event handlers
5. Update sidebar with expandable collections

**Key integration points:**

```typescript
// Add imports
import { Header } from './components/Header';
import { UserDropdown } from './components/UserDropdown';
import { NotificationPanel } from './components/NotificationPanel';
import { useTranslation } from 'react-i18next';

// Add state
const [teams, setTeams] = useState<Team[]>([]);
const [activeTeamId, setActiveTeamId] = useState<string | null>(null);
const [notifications, setNotifications] = useState<NotificationWithRead[]>([]);
const [isUserDropdownOpen, setIsUserDropdownOpen] = useState(false);
const [isNotificationPanelOpen, setIsNotificationPanelOpen] = useState(false);

// Add Header to layout (after sidebar)
<Header
  userProfile={userProfile}
  unreadCount={notifications.filter(n => !n.read).length}
  onNotificationClick={() => setIsNotificationPanelOpen(!isNotificationPanelOpen)}
  onUserClick={() => setIsUserDropdownOpen(true)}
/>

// Add UserDropdown
<UserDropdown
  isOpen={isUserDropdownOpen}
  onClose={() => setIsUserDropdownOpen(false)}
  user={userProfile}
  teams={teams.map(t => ({ ...t, isOwner: t.owner_id === currentUserId }))}
  activeTeamId={activeTeamId}
  onTeamSelect={setActiveTeamId}
  onCreateTeam={handleCreateTeam}
  onTeamSettings={handleTeamSettings}
  onProfile={() => setIsProfileModalOpen(true)}
  onAccount={() => setView('settings')}
  onLogout={handleLogout}
/>

// Add NotificationPanel (inside Header area)
<NotificationPanel
  isOpen={isNotificationPanelOpen}
  onClose={() => setIsNotificationPanelOpen(false)}
  notifications={notifications}
  onMarkRead={handleMarkNotificationRead}
  onMarkAllRead={handleMarkAllNotificationsRead}
/>
```

**Step 2: Commit**

```bash
git add frontend/App.tsx
git commit -m "feat(app): integrate Header, UserDropdown, and NotificationPanel"
```

---

## Summary

| Phase | Tasks | Description |
|-------|-------|-------------|
| 0 | 0.1-0.5 | Database migrations (6 tables) |
| 1 | 1.1-1.4 | i18n setup with react-i18next |
| 2 | 2.1 | Registration form in AuthOverlay |
| 3 | 3.1-3.3 | Header, UserDropdown, NotificationPanel |
| 4 | 4.1 | CollectionPicker component |
| 5 | 5.1-5.3 | Types, services, App.tsx integration |

**Total Tasks:** 12 main tasks

**Estimated commits:** ~15-18 commits
