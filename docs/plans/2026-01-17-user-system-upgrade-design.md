# 用户系统升级设计

> 创建日期: 2026-01-17

## 概述

升级 MediaHub 用户系统，新增以下功能：
- 顶部导航栏重构（用户头像右上角、通知、语言切换）
- 用户注册功能
- 团队协作模式
- 共享集合
- 国际化（中/英双语）
- 通知系统

## 整体架构

```
┌─────────────────────────────────────────────────────────────────┐
│  顶部导航栏                        [🌐 EN ▼] [🔔 3] [头像 ▼]    │
├─────────────────────────────────────────────────────────────────┤
│        │                                                        │
│ 侧边栏  │              主内容区域                                │
│        │                                                        │
│ Parser │                                                        │
│ Library│                                                        │
│  ├全部  │                                                        │
│  ├收藏  │                                                        │
│  └集合  │                                                        │
│ Dashboard                                                       │
│ Settings                                                        │
│        │                                                        │
└─────────────────────────────────────────────────────────────────┘
```

## 一、顶部导航栏

### 布局

```
┌─────────────────────────────────────────────────────────────────┐
│  [Logo MediaHub]                    [🌐 EN ▼] [🔔 3] [头像 ▼]  │
└─────────────────────────────────────────────────────────────────┘
```

### 组件说明

| 元素 | 功能 |
|------|------|
| Logo | 点击返回首页 |
| 语言切换 | 下拉菜单，EN / 中文 |
| 通知铃铛 | Lucide Bell 图标，红点徽章显示未读数 |
| 用户头像 | 点击滑出用户卡片 |

### 响应式设计

- **桌面端**：顶栏固定显示所有元素
- **移动端**：语言切换移入用户卡片，顶栏仅显示通知和头像

## 二、用户卡片（滑出菜单）

点击头像后从右侧滑出：

```
┌────────────────────────────┐
│  ┌──┐  用户名              │
│  │头│  user@email.com      │
│  └──┘                      │
├────────────────────────────┤
│  JOINED TEAMS              │
│  🔵 我的团队         ⚙ ✓   │
│  + 创建团队                │
├────────────────────────────┤
│  👤 个人资料               │
│  ⚙️ 账户设置               │
│  🌐 语言：中文 ▸           │  ← 仅移动端显示
├────────────────────────────┤
│  ↪ 退出登录                │
└────────────────────────────┘
```

### 交互细节

- 点击头像：卡片从右侧滑入（300ms ease-out）
- 点击卡片外区域或按 ESC：滑出关闭
- 团队列表：勾号表示当前激活团队
- 团队右侧齿轮：团队设置（仅 owner 可见）

## 三、侧边栏结构

```
┌─────────────────────────┐
│  ✦ MediaHub             │
├─────────────────────────┤
│  🔍 Link Parser         │
│                         │
│  📚 My Library     ▼    │  ← 可展开
│     ├─ 全部视频          │
│     ├─ 我的收藏          │
│     └─ 共享集合          │
│        ├─ 旅行素材       │
│        └─ + 新建集合     │
│                         │
│  📊 Dashboard           │
│  ⚙️ Settings            │
└─────────────────────────┘
```

### 集合类型

| 类型 | 说明 |
|------|------|
| 全部视频 | 用户所有视频 |
| 我的收藏 | 用户标记收藏的视频 |
| 共享集合 | 关联团队，团队成员可协作 |

## 四、数据库设计

### 4.1 teams（团队表）

```sql
CREATE TABLE teams (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  name VARCHAR(100) NOT NULL,
  owner_id UUID REFERENCES auth.users(id) NOT NULL,
  invite_code VARCHAR(20) UNIQUE NOT NULL,
  created_at TIMESTAMPTZ DEFAULT NOW()
);

-- RLS 策略
ALTER TABLE teams ENABLE ROW LEVEL SECURITY;

-- 成员可查看所属团队
CREATE POLICY "Members can view their teams" ON teams
  FOR SELECT USING (
    id IN (SELECT team_id FROM team_members WHERE user_id = auth.uid())
  );

-- 仅 owner 可更新
CREATE POLICY "Owner can update team" ON teams
  FOR UPDATE USING (owner_id = auth.uid());
```

### 4.2 team_members（团队成员表）

```sql
CREATE TABLE team_members (
  team_id UUID REFERENCES teams(id) ON DELETE CASCADE,
  user_id UUID REFERENCES auth.users(id) ON DELETE CASCADE,
  role VARCHAR(20) DEFAULT 'member' CHECK (role IN ('owner', 'member')),
  joined_at TIMESTAMPTZ DEFAULT NOW(),
  PRIMARY KEY (team_id, user_id)
);

ALTER TABLE team_members ENABLE ROW LEVEL SECURITY;

-- 成员可查看同团队成员
CREATE POLICY "Members can view team members" ON team_members
  FOR SELECT USING (
    team_id IN (SELECT team_id FROM team_members WHERE user_id = auth.uid())
  );
```

### 4.3 collections（集合表）

```sql
CREATE TABLE collections (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  name VARCHAR(100) NOT NULL,
  owner_id UUID REFERENCES auth.users(id) NOT NULL,
  team_id UUID REFERENCES teams(id) ON DELETE SET NULL,  -- NULL 表示私人集合
  created_at TIMESTAMPTZ DEFAULT NOW()
);

ALTER TABLE collections ENABLE ROW LEVEL SECURITY;

-- 私人集合：仅所有者可见
-- 共享集合：团队成员可见
CREATE POLICY "Users can view their collections" ON collections
  FOR SELECT USING (
    owner_id = auth.uid() OR
    team_id IN (SELECT team_id FROM team_members WHERE user_id = auth.uid())
  );

-- 私人集合：仅所有者可增删改
-- 共享集合：团队成员可增删改
CREATE POLICY "Users can manage collections" ON collections
  FOR ALL USING (
    owner_id = auth.uid() OR
    team_id IN (SELECT team_id FROM team_members WHERE user_id = auth.uid())
  );
```

### 4.4 collection_videos（集合-视频关联表）

```sql
CREATE TABLE collection_videos (
  collection_id UUID REFERENCES collections(id) ON DELETE CASCADE,
  video_aweme_id VARCHAR(50) REFERENCES douyin_videos(aweme_id) ON DELETE CASCADE,
  added_by UUID REFERENCES auth.users(id),
  added_at TIMESTAMPTZ DEFAULT NOW(),
  PRIMARY KEY (collection_id, video_aweme_id)
);

ALTER TABLE collection_videos ENABLE ROW LEVEL SECURITY;

-- 集合成员可查看和管理
CREATE POLICY "Collection members can manage videos" ON collection_videos
  FOR ALL USING (
    collection_id IN (
      SELECT id FROM collections WHERE
        owner_id = auth.uid() OR
        team_id IN (SELECT team_id FROM team_members WHERE user_id = auth.uid())
    )
  );
```

### 4.5 notifications（通知表）

```sql
CREATE TABLE notifications (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  type VARCHAR(20) NOT NULL CHECK (type IN ('system', 'team')),
  title VARCHAR(200) NOT NULL,
  content TEXT,
  team_id UUID REFERENCES teams(id) ON DELETE CASCADE,  -- 系统通知为 NULL
  created_by UUID REFERENCES auth.users(id),
  created_at TIMESTAMPTZ DEFAULT NOW()
);

ALTER TABLE notifications ENABLE ROW LEVEL SECURITY;

-- 系统通知：所有人可见
-- 团队通知：团队成员可见
CREATE POLICY "Users can view notifications" ON notifications
  FOR SELECT USING (
    type = 'system' OR
    team_id IN (SELECT team_id FROM team_members WHERE user_id = auth.uid())
  );
```

### 4.6 user_notifications（用户通知状态）

```sql
CREATE TABLE user_notifications (
  user_id UUID REFERENCES auth.users(id) ON DELETE CASCADE,
  notification_id UUID REFERENCES notifications(id) ON DELETE CASCADE,
  read_at TIMESTAMPTZ,  -- NULL 表示未读
  PRIMARY KEY (user_id, notification_id)
);

ALTER TABLE user_notifications ENABLE ROW LEVEL SECURITY;

CREATE POLICY "Users can manage their notifications" ON user_notifications
  FOR ALL USING (user_id = auth.uid());
```

## 五、通知系统

### 通知面板 UI

```
┌─────────────────────────────────┐
│  通知                    全部已读 │
├─────────────────────────────────┤
│  🔵 系统通知                     │
│  ┌─────────────────────────────┐│
│  │ v2.0 版本更新上线            ││
│  │ 新增团队协作功能...   3小时前 ││
│  └─────────────────────────────┘│
│                                 │
│  👥 团队通知                     │
│  ┌─────────────────────────────┐│
│  │ 张三 添加了新视频到「旅行素材」││
│  │ 棉服也能穿出时尚感...  5分钟前 ││
│  └─────────────────────────────┘│
└─────────────────────────────────┘
```

### 通知触发场景

| 类型 | 触发事件 |
|------|----------|
| system | 管理员后台发布公告 |
| team | 新成员加入团队 |
| team | 有人往共享集合添加视频 |
| team | 团队设置变更（名称、邀请码重置等） |

### 实现方式

1. 使用 Supabase Realtime 订阅 `notifications` 表
2. 新通知实时推送，顶栏铃铛显示红点
3. 点击单条通知标记已读
4. 点击"全部已读"批量标记

## 六、注册功能

### 注册表单

```
┌────────────────────────────────┐
│          创建账号               │
├────────────────────────────────┤
│  邮箱地址                       │
│  ┌────────────────────────────┐│
│  │ your@email.com             ││
│  └────────────────────────────┘│
│                                │
│  密码                          │
│  ┌────────────────────────────┐│
│  │ ••••••••              👁   ││
│  └────────────────────────────┘│
│                                │
│  确认密码                       │
│  ┌────────────────────────────┐│
│  │ ••••••••              👁   ││
│  └────────────────────────────┘│
│                                │
│  ┌────────────────────────────┐│
│  │          注 册              ││
│  └────────────────────────────┘│
│                                │
│  已有账号？立即登录              │
└────────────────────────────────┘
```

### 注册流程

1. 用户填写邮箱 + 密码 + 确认密码
2. 前端校验密码一致性
3. 调用 Supabase Auth `signUp()`
4. Supabase 发送验证邮件
5. 用户点击邮件链接完成验证
6. 自动登录并跳转主界面

### 团队邀请流程

1. 团队管理员在团队设置中点击"复制邀请链接"
2. 生成链接格式：`https://your-domain.com/invite/{invite_code}`
3. 新用户点击链接：
   - 未登录 → 显示注册/登录页面，登录后自动加入团队
   - 已登录 → 直接加入团队
4. 加入成功后跳转团队页面

## 七、国际化（i18n）

### 技术方案

使用 `react-i18next`

### 文件结构

```
frontend/
├── locales/
│   ├── en.json      # 英文
│   └── zh.json      # 中文
├── i18n.ts          # i18n 配置
```

### i18n.ts 配置

```typescript
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

export default i18n;
```

### 翻译文件示例

**zh.json:**
```json
{
  "nav": {
    "linkParser": "链接解析",
    "myLibrary": "我的媒体库",
    "allVideos": "全部视频",
    "favorites": "我的收藏",
    "sharedCollections": "共享集合",
    "dashboard": "数据看板",
    "settings": "设置"
  },
  "user": {
    "profile": "个人资料",
    "account": "账户设置",
    "signOut": "退出登录",
    "joinedTeams": "已加入的团队",
    "createTeam": "创建团队"
  },
  "auth": {
    "login": "登录",
    "register": "注册",
    "email": "邮箱地址",
    "password": "密码",
    "confirmPassword": "确认密码",
    "noAccount": "没有账号？立即注册",
    "hasAccount": "已有账号？立即登录"
  },
  "notifications": {
    "title": "通知",
    "markAllRead": "全部已读",
    "system": "系统通知",
    "team": "团队通知",
    "empty": "暂无通知"
  },
  "collections": {
    "create": "新建集合",
    "addTo": "添加到集合",
    "shared": "共享"
  },
  "team": {
    "create": "创建团队",
    "settings": "团队设置",
    "invite": "邀请成员",
    "copyLink": "复制邀请链接",
    "members": "成员列表"
  }
}
```

**en.json:**
```json
{
  "nav": {
    "linkParser": "Link Parser",
    "myLibrary": "My Library",
    "allVideos": "All Videos",
    "favorites": "Favorites",
    "sharedCollections": "Shared Collections",
    "dashboard": "Dashboard",
    "settings": "Settings"
  },
  "user": {
    "profile": "Profile",
    "account": "Account",
    "signOut": "Sign Out",
    "joinedTeams": "Joined Teams",
    "createTeam": "Create Team"
  },
  "auth": {
    "login": "Log In",
    "register": "Sign Up",
    "email": "Email Address",
    "password": "Password",
    "confirmPassword": "Confirm Password",
    "noAccount": "Don't have an account? Sign up",
    "hasAccount": "Already have an account? Log in"
  },
  "notifications": {
    "title": "Notifications",
    "markAllRead": "Mark all read",
    "system": "System",
    "team": "Team",
    "empty": "No notifications"
  },
  "collections": {
    "create": "New Collection",
    "addTo": "Add to Collection",
    "shared": "Shared"
  },
  "team": {
    "create": "Create Team",
    "settings": "Team Settings",
    "invite": "Invite Members",
    "copyLink": "Copy Invite Link",
    "members": "Members"
  }
}
```

### 语言切换逻辑

```typescript
const changeLanguage = (lang: 'en' | 'zh') => {
  i18n.changeLanguage(lang);
  localStorage.setItem('language', lang);
};
```

## 八、视频加入集合

### UI 位置

在视频详情页右下角，Download Video 按钮旁边添加 📁 图标按钮：

```
┌─────────────────────────────────────────────┐
│  [Download Video]   [📁] [🔄] [🗑️]         │
└─────────────────────────────────────────────┘
```

### 集合选择器弹窗

```
┌─────────────────────────────┐
│  添加到集合                  │
├─────────────────────────────┤
│  ☑️ 我的收藏                 │
│  ☐ 旅行素材 (共享)           │
│  ☐ 教程合集 (共享)           │
├─────────────────────────────┤
│  + 新建集合                  │
└─────────────────────────────┘
```

### 交互逻辑

- 支持多选：一个视频可加入多个集合
- 勾选/取消勾选：实时保存到数据库
- 共享集合标记 `(共享)` 便于区分
- 底部"新建集合"可快速创建

## 九、新增前端文件结构

```
frontend/
├── locales/
│   ├── en.json
│   └── zh.json
├── i18n.ts
├── components/
│   ├── Header.tsx              # 新增：顶部导航栏
│   ├── UserDropdown.tsx        # 新增：用户卡片滑出菜单
│   ├── NotificationPanel.tsx   # 新增：通知面板
│   ├── LanguageSwitcher.tsx    # 新增：语言切换器
│   ├── CollectionPicker.tsx    # 新增：集合选择器弹窗
│   ├── TeamSettings.tsx        # 新增：团队设置页
│   ├── InvitePage.tsx          # 新增：邀请链接落地页
│   └── AuthOverlay.tsx         # 修改：添加注册表单
├── hooks/
│   └── useNotifications.ts     # 新增：通知订阅 hook
├── services/
│   ├── teamService.ts          # 新增：团队相关 API
│   ├── collectionService.ts    # 新增：集合相关 API
│   └── notificationService.ts  # 新增：通知相关 API
└── types.ts                    # 修改：添加新类型定义
```

## 十、实现优先级

| 阶段 | 功能 | 说明 |
|------|------|------|
| P0 | 数据库迁移 | 创建 6 张新表 |
| P0 | 注册功能 | 基础用户增长 |
| P1 | 顶部导航栏 + 用户卡片 | UI 重构 |
| P1 | 国际化 | react-i18next 集成 |
| P2 | 团队功能 | 创建/加入/邀请 |
| P2 | 共享集合 | 集合 CRUD + 视频关联 |
| P3 | 通知系统 | Realtime 订阅 + 通知面板 |

## 附录：API 端点设计

### 团队相关

| 端点 | 方法 | 说明 |
|------|------|------|
| `/teams` | POST | 创建团队 |
| `/teams` | GET | 获取我的团队列表 |
| `/teams/{id}` | GET | 获取团队详情 |
| `/teams/{id}` | PUT | 更新团队信息 |
| `/teams/{id}` | DELETE | 删除团队 |
| `/teams/{id}/members` | GET | 获取团队成员 |
| `/teams/{id}/members/{user_id}` | DELETE | 移除成员 |
| `/teams/join/{invite_code}` | POST | 通过邀请码加入团队 |

### 集合相关

| 端点 | 方法 | 说明 |
|------|------|------|
| `/collections` | POST | 创建集合 |
| `/collections` | GET | 获取我的集合列表 |
| `/collections/{id}` | GET | 获取集合详情（含视频列表） |
| `/collections/{id}` | PUT | 更新集合信息 |
| `/collections/{id}` | DELETE | 删除集合 |
| `/collections/{id}/videos` | POST | 添加视频到集合 |
| `/collections/{id}/videos/{aweme_id}` | DELETE | 从集合移除视频 |

### 通知相关

| 端点 | 方法 | 说明 |
|------|------|------|
| `/notifications` | GET | 获取我的通知列表 |
| `/notifications/{id}/read` | POST | 标记单条已读 |
| `/notifications/read-all` | POST | 标记全部已读 |
| `/notifications/unread-count` | GET | 获取未读数量 |
