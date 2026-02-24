# 项目管理功能详细开发计划 v2

> 对标：分秒帧（Mediatrack）项目管理模块
> 日期：2026-02-17（基于最新代码 efecd54）
> 估算总工时：~60h（4 Sprint）
> Migration 编号从 068 开始（060-067 已占用）

---

## 一、现状分析

### 1.1 现有项目管理代码

```
frontend/
  pages/ProjectsPage.tsx              (133行) — 路由入口，Tab: 文件/任务
  components/ProjectsListView.tsx     (229行) — 项目列表，grid/table，全部/已收藏
  components/ProjectCard.tsx           (85行) — 项目卡片
  components/ProjectFilesView.tsx     (230行) — 项目文件列表
  components/FileCard.tsx             (113行) — 文件卡片
  components/FileInfoPanel.tsx        (152行) — 文件详情面板
  components/CreateProjectModal.tsx   (190行) — 创建项目弹窗
  components/VideoReviewPage.tsx      (496行) — 视频审阅
  components/KanbanBoard.tsx          (647行) — 看板任务
  components/LinkVideoModal.tsx       (~100行) — 关联视频弹窗
  services/projectsService.ts        (230行) — 17个API方法
  services/projectTasksService.ts    (120行) — 任务看板API
```

### 1.2 可复用的已有组件（资源库已实现）

```
components/ReviewStatusBadge.tsx      — 审阅状态标签 ✅ 直接复用
components/ReviewStatusDropdown.tsx   — 状态下拉选择 ✅ 直接复用
components/ResourceReviewPanel.tsx    — 审阅面板 ✅ 参考复用
components/ResourceAnnotationOverlay.tsx — 批注覆盖层 ✅ 参考复用
components/ResourceVersionCompare.tsx — 版本对比 ✅ 参考复用
components/VersionManagerModal.tsx    — 版本管理弹窗 ✅ 参考复用
components/KeyboardShortcutsDialog.tsx — 快捷键对话框 ✅ 直接复用
components/DuplicateFileAlert.tsx     — 重复文件提示 ✅ 直接复用
components/SidebarFolderTree.tsx      — 文件夹树 ✅ 参考复用
components/FolderCard.tsx             — 文件夹卡片 ✅ 参考复用
components/FolderPickerModal.tsx      — 文件夹选择弹窗 ✅ 参考复用
hooks/useFileKeyboard.ts             — 键盘快捷键 ✅ 参考复用
```

### 1.3 数据库现状

```sql
-- 已有表（042-043, 046）
projects           — id, name, description, owner_id, team_id, project_type, project_group, is_starred
project_files      — id, project_id, filename, file_type, mime_type, file_path, media_id(原video_id),
                     review_status, current_version, is_trashed, folder_id(待添加)
file_versions      — id, file_id, version_number, filename, file_path, ...
review_comments    — id, file_id, version_id, author_id, content, timestamp_seconds, drawing_data
shares             — id, resource_id, project_file_id, share_type, share_code, password, ...
share_views        — id, share_id, viewer_id, view_count

-- Migration 编号已用到 067，项目管理从 068 开始
```

### 1.4 后端 API 现状

```
GET    /api/v1/projects                              → 列表
POST   /api/v1/projects                              → 创建
GET    /api/v1/projects/{id}                          → 详情
PUT    /api/v1/projects/{id}                          → 更新
DELETE /api/v1/projects/{id}                          → 删除
GET    /api/v1/projects/{id}/files                    → 文件列表
POST   /api/v1/projects/{id}/files/upload             → 上传
POST   /api/v1/projects/{id}/files/link-media         → 关联媒体(原link-video)
GET    /api/v1/projects/{id}/files/{fid}              → 文件详情
PUT    /api/v1/projects/{id}/files/{fid}              → 更新文件
DELETE /api/v1/projects/{id}/files/{fid}              → 删除文件
GET    /api/v1/projects/{id}/files/{fid}/versions     → 版本列表
POST   /api/v1/projects/{id}/files/{fid}/versions     → 上传新版本
GET    /api/v1/projects/{id}/files/{fid}/comments     → 评论列表
POST   /api/v1/projects/{id}/files/{fid}/comments     → 添加评论
DELETE /api/v1/projects/{id}/files/{fid}/comments/{c} → 删除评论
PUT    /api/v1/projects/{id}/files/{fid}/review-status → 审阅状态
```

---

## 二、分秒帧功能全景（实测）

### 2.1 项目列表页

**侧边栏（左侧 ~200px）：**
```
┌─────────────────────┐
│ 🔗 项目列表    [+]   │  ← 高亮链接 + 快捷新建
│                     │
│ ▾ ⭐ 星标项目        │  ← 可折叠
│   ● 2分钟了解分秒帧  │  ← 红色圆点=未读
│                     │
│ ⚙ 项目分组          │
│ ─────────────────   │
│ ▸ 🌐 团队内项目 (2)  │  ← 可折叠，含计数
│ ▸ 🌐 团队外项目      │
└─────────────────────┘
```

**主区域：**
```
┌──────────────────────────────────────────────────────────────┐
│ 项目列表                                    🔍 搜索项目  [◀] │
├──────────────────────────────────────────────────────────────┤
│ 全部项目  内部项目  外部项目                                    │
│ ─────── (下划线)                                             │
├──────────────────────────────────────────────────────────────┤
│ 共2个项目         自定义排序 ▼    🔽筛选    [新建项目]          │
├──────────────────────────────────────────────────────────────┤
│ 项目名称    │ 负责人 │ 归属     │ 分组 │ 活跃时间 ▼ │ 星标│ ⋮ │
│ [项] 项目1  │ 大鱼   │ 团队内   │  -   │ 02-15 20:52│ ☆  │ ⋮ │
│ [2] 2分钟.. │ 大鱼   │ 团队内   │  -   │ 02-11 21:57│ ★  │ ⋮ │
└──────────────────────────────────────────────────────────────┘
```

**⋮ 操作菜单：**
```
┌─────────────────────┐
│ [项] 项目 1          │  ← badge + 项目名
├─────────────────────┤
│ ⚙ 项目设置          │
│ 👤 项目成员          │
├─────────────────────┤
│ 🔖 标记颜色     ▸   │  ← 子菜单8色
│ ○ 项目状态    ⚡升级 │
│ ⭐ 星标              │
│ 🔴 删除项目          │
└─────────────────────┘
```

**新建项目弹窗：**
```
┌──────────────────────────────┐
│ 创建项目                 [✕] │
├──────────────────────────────┤
│ 项目头像   [上传]            │
│                              │
│ 项目名称                0/30 │
│ [请输入项目名称_________]    │
│                              │
│ 项目公告                0/100│
│ [帮助新加入项目的成      ]   │
│ [员快速了解项目          ]   │
│                              │
│ 文件状态流程管理             │
│ [默认流程            ▼]     │
│                              │
│ 添加至项目分组               │
│ [暂无分组            ▼]     │
│                              │
│ [    创建并添加成员    ]     │ ← 紫色全宽
└──────────────────────────────┘
```

### 2.2 项目内部页

```
┌──────────────────────────────────────────────────────────────┐
│ 2分钟了解分秒帧  [设置状态 ▼]        [👤+] [头像] 🔍搜索  [◀]│
├──────────────────────────────────────────────────────────────┤
│ 文件   分享 ③   回收站 ①                                     │
│ ─────                                                        │
├──────────────────────────────────────────────────────────────┤
│ ☐全选  取消  [设置状态▼]            ← 批量操作栏(选中时出现)  │
├──────────────────────────────────────────────────────────────┤
│ 共5项   更新时间▼   🔽   🔲🔳   [收集] [上传▼] [新建▼]       │
├──────────────────────────────────────────────────────────────┤
│ ┌─────────┐ ┌─────────┐ ┌─────────┐ ┌─────────┐ ┌────────┐ │
│ │ ○ 视频  │ │  W      │ │  P      │ │  X      │ │📁 素材 │ │
│ │ 缩略图  │ │  ord    │ │  PT     │ │  LS     │ │ 预览图 │ │
│ │▶02:19 ●4│ │         │ │         │ │         │ │    +2  │ │
│ │─────────│ │─────────│ │─────────│ │─────────│ │────────│ │
│ │ 文件名  │ │ 文件名  │ │ 文件名  │ │ 文件名  │ │ 素材   │ │
│ │ 时间  ⋮ │ │ 时间  ⋮ │ │ 时间  ⋮ │ │ 时间  ⋮ │ │ 时间 ⋮ │ │
│ └─────────┘ └─────────┘ └─────────┘ └─────────┘ └────────┘ │
└──────────────────────────────────────────────────────────────┘
```

**上传 ▼ 下拉：** 上传文件 / 上传文件夹
**新建 ▼ 下拉：** 新建文件夹
**文件 hover：** 左上☐复选框 + 右上⋮更多

**文件右键/⋮菜单：**
```
┌──────────────────┐
│ ⬇ 下载           │
│ ✏ 重命名         │
│ 📂 移动到...      │
│ 📋 复制到...      │
├──────────────────┤
│ ○ 设置状态   ▸   │
│ 📝 版本历史      │
│ ℹ 文件信息       │
├──────────────────┤
│ 🔗 分享          │
│ 🔴 删除          │
└──────────────────┘
```

---

## 三、Gap 分析

### 🔴 P0 — 核心缺失

| # | 功能 | 当前状态 | 需要做 |
|---|------|---------|--------|
| 1 | 项目列表侧边栏 | ❌ 完全没有 | 新建 ProjectsSidebar.tsx |
| 2 | 项目内文件夹 | ❌ | DB + API + 前端 |
| 3 | 项目内搜索/排序/筛选 | ❌ | 改 ProjectFilesView |
| 4 | 项目⋮操作菜单 | ❌ | 新建 ProjectContextMenu |
| 5 | 文件批量选择+操作 | ❌ | 改 ProjectFilesView + FileCard |
| 6 | Tab: 分享/回收站 | ❌ 只有文件/任务 | 改 ProjectsPage + 新建2组件 |
| 7 | 文件右键菜单 | ❌ | 新建 ProjectFileContextMenu |

### 🟡 P1 — 重要

| # | 功能 |
|---|------|
| 8 | 列表 Tab (全部/内部/外部) |
| 9 | 列表搜索+排序 |
| 10 | 新建弹窗完善（公告/字数/分组） |
| 11 | 项目成员管理 |
| 12 | 文件状态标签（复用 ReviewStatusBadge） |
| 13 | 上传下拉（文件/文件夹）|
| 14 | 新建下拉（新建文件夹）|
| 15 | 项目设置页 |

### 🟢 P2 — 后续迭代

| # | 功能 |
|---|------|
| 16 | 收集功能 |
| 17 | 标记颜色 |
| 18 | 项目头像 |
| 19 | 项目状态流程自定义 |
| 20 | 项目分组 CRUD |

---

## 四、Sprint 1 — 项目列表页升级（~16h）

### Task 1.1 新建 `ProjectsSidebar.tsx`（4h）

**新建文件：** `frontend/components/ProjectsSidebar.tsx`（~280行）

**Props：**
```tsx
interface ProjectsSidebarProps {
  projects: Project[];
  starredProjects: Project[];              // 已星标
  selectedProjectId: string | null;        // 当前选中
  onProjectSelect: (project: Project) => void;
  onCreateProject: () => void;
  collapsed: boolean;
  onToggleCollapse: () => void;
}
```

**完整组件结构：**
```tsx
import React, { useState } from 'react';
import { Link, useLocation } from 'react-router-dom';
import { useTranslation } from 'react-i18next';
import {
  LayoutGrid, Plus, Star, Settings, ChevronDown, ChevronRight,
  Globe, Users, PanelLeftClose, PanelLeftOpen
} from 'lucide-react';
import { Project } from '../types';

// ── CollapsibleSection ──────────────────────────────────────
interface CollapsibleSectionProps {
  title: string;
  icon: React.ReactNode;
  count?: number;
  defaultOpen?: boolean;
  children: React.ReactNode;
}
const CollapsibleSection: React.FC<CollapsibleSectionProps> = ({
  title, icon, count, defaultOpen = false, children
}) => {
  const [open, setOpen] = useState(defaultOpen);
  return (
    <div>
      <button
        onClick={() => setOpen(!open)}
        className="w-full flex items-center gap-2 px-3 py-1.5 text-xs text-zinc-400
                   hover:text-zinc-200 hover:bg-zinc-800/50 rounded-md transition-colors"
      >
        {open ? <ChevronDown size={12} /> : <ChevronRight size={12} />}
        {icon}
        <span className="flex-1 text-left truncate">{title}</span>
        {count != null && (
          <span className="text-[10px] text-zinc-600">({count})</span>
        )}
      </button>
      {open && <div className="ml-4 mt-0.5 space-y-0.5">{children}</div>}
    </div>
  );
};

// ── SidebarProjectItem ──────────────────────────────────────
const SidebarProjectItem: React.FC<{
  project: Project;
  active: boolean;
  onClick: () => void;
  hasUnread?: boolean;
}> = ({ project, active, onClick, hasUnread }) => (
  <button
    onClick={onClick}
    className={`w-full flex items-center gap-2 px-2 py-1 text-xs rounded-md
                transition-colors truncate
                ${active
                  ? 'bg-indigo-500/20 text-indigo-300'
                  : 'text-zinc-400 hover:text-zinc-200 hover:bg-zinc-800/50'}`}
  >
    {hasUnread && <span className="w-1.5 h-1.5 rounded-full bg-red-500 flex-shrink-0" />}
    <span className="truncate">{project.name}</span>
  </button>
);

// ── Main Sidebar ────────────────────────────────────────────
export const ProjectsSidebar: React.FC<ProjectsSidebarProps> = ({
  projects, starredProjects, selectedProjectId,
  onProjectSelect, onCreateProject, collapsed, onToggleCollapse
}) => {
  const { t } = useTranslation();

  const internalProjects = projects.filter(p => p.project_type !== 'external');
  const externalProjects = projects.filter(p => p.project_type === 'external');

  if (collapsed) {
    return (
      <div className="w-10 border-r border-zinc-800 flex flex-col items-center py-3">
        <button onClick={onToggleCollapse} className="p-1.5 text-zinc-500 hover:text-zinc-300">
          <PanelLeftOpen size={16} />
        </button>
      </div>
    );
  }

  return (
    <aside className="w-52 border-r border-zinc-800 flex flex-col h-full bg-zinc-900/30">
      {/* Header */}
      <div className="flex items-center justify-between px-3 py-3 border-b border-zinc-800/50">
        <span className="flex items-center gap-2 text-sm font-medium text-zinc-200">
          <LayoutGrid size={14} />
          {t('projects.sidebar.projectList')}
        </span>
        <div className="flex items-center gap-1">
          <button onClick={onCreateProject}
            className="p-1 text-zinc-500 hover:text-zinc-300 hover:bg-zinc-800 rounded">
            <Plus size={14} />
          </button>
          <button onClick={onToggleCollapse}
            className="p-1 text-zinc-500 hover:text-zinc-300 hover:bg-zinc-800 rounded">
            <PanelLeftClose size={14} />
          </button>
        </div>
      </div>

      {/* Content */}
      <div className="flex-1 overflow-y-auto py-2 px-1 space-y-1">
        {/* Starred */}
        <CollapsibleSection
          title={t('projects.sidebar.starredProjects')}
          icon={<Star size={12} className="text-yellow-500" />}
          defaultOpen={starredProjects.length > 0}
        >
          {starredProjects.length === 0 ? (
            <p className="text-[10px] text-zinc-600 px-2 py-1">
              {t('projects.sidebar.noStarred')}
            </p>
          ) : (
            starredProjects.map(p => (
              <SidebarProjectItem
                key={p.id} project={p}
                active={p.id === selectedProjectId}
                onClick={() => onProjectSelect(p)}
              />
            ))
          )}
        </CollapsibleSection>

        {/* Groups placeholder */}
        <div className="flex items-center gap-2 px-3 py-1.5 text-xs text-zinc-500">
          <Settings size={12} />
          <span>{t('projects.sidebar.projectGroups')}</span>
        </div>

        <div className="border-t border-zinc-800/50 my-2" />

        {/* Internal */}
        <CollapsibleSection
          title={t('projects.sidebar.internalProjects')}
          icon={<Users size={12} />}
          count={internalProjects.length}
          defaultOpen
        >
          {internalProjects.map(p => (
            <SidebarProjectItem
              key={p.id} project={p}
              active={p.id === selectedProjectId}
              onClick={() => onProjectSelect(p)}
            />
          ))}
        </CollapsibleSection>

        {/* External */}
        <CollapsibleSection
          title={t('projects.sidebar.externalProjects')}
          icon={<Globe size={12} />}
          count={externalProjects.length}
        >
          {externalProjects.map(p => (
            <SidebarProjectItem
              key={p.id} project={p}
              active={p.id === selectedProjectId}
              onClick={() => onProjectSelect(p)}
            />
          ))}
        </CollapsibleSection>
      </div>
    </aside>
  );
};
```

**Commit:** `feat(projects): add ProjectsSidebar with starred/grouped navigation`

---

### Task 1.2 改造 `ProjectsListView.tsx`（4h）

**修改文件：** `frontend/components/ProjectsListView.tsx`

**变更清单：**

**① Props 扩展** — 接收 sidebar 控制：
```tsx
interface ProjectsListViewProps {
  onProjectSelect: (project: Project) => void;
  onCreateProject: () => void;
  sidebarCollapsed: boolean;            // 新增
  onToggleSidebar: () => void;          // 新增
}
```

**② 布局改为 flex** — 在 ProjectsPage.tsx 中：
```tsx
// ProjectsPage.tsx 中组合
<div className="flex h-full">
  <ProjectsSidebar
    projects={projects}
    starredProjects={projects.filter(p => p.is_starred)}
    selectedProjectId={null}
    onProjectSelect={handleProjectSelect}
    onCreateProject={() => setIsCreateProjectModalOpen(true)}
    collapsed={sidebarCollapsed}
    onToggleCollapse={() => setSidebarCollapsed(!sidebarCollapsed)}
  />
  <div className="flex-1 min-w-0 px-6 py-4">
    <ProjectsListView ... />
  </div>
</div>
```

**③ Tab 改为下划线式：** 全部项目 | 内部项目 | 外部项目
```tsx
const [filter, setFilter] = useState<'all' | 'internal' | 'external'>('all');

// Tab UI
<div className="flex gap-6 border-b border-zinc-800 mb-4">
  {(['all', 'internal', 'external'] as const).map(tab => (
    <button key={tab} onClick={() => setFilter(tab)}
      className={`pb-2 text-sm font-medium border-b-2 transition-colors ${
        filter === tab
          ? 'border-indigo-500 text-white'
          : 'border-transparent text-zinc-500 hover:text-zinc-300'
      }`}
    >
      {t(`projects.tab.${tab}`)}
    </button>
  ))}
</div>
```

**④ 搜索框 + 排序下拉：**
```tsx
const [searchQuery, setSearchQuery] = useState('');
const [sortBy, setSortBy] = useState<'updated_at' | 'created_at' | 'name'>('updated_at');
const [sortDir, setSortDir] = useState<'desc' | 'asc'>('desc');

// 搜索
<div className="relative">
  <Search size={14} className="absolute left-2.5 top-1/2 -translate-y-1/2 text-zinc-500" />
  <input
    value={searchQuery}
    onChange={e => setSearchQuery(e.target.value)}
    placeholder={t('projects.searchProjects')}
    className="w-44 pl-8 pr-3 py-1.5 text-xs bg-zinc-800/60 border border-zinc-700/50
               rounded-lg text-zinc-200 placeholder-zinc-500 focus:outline-none
               focus:border-indigo-500"
  />
</div>

// 排序
<select value={sortBy} onChange={e => setSortBy(e.target.value as any)}
  className="text-xs bg-zinc-800 border border-zinc-700/50 rounded-lg px-2 py-1.5 text-zinc-300">
  <option value="updated_at">{t('projects.sortUpdatedAt')}</option>
  <option value="created_at">{t('projects.sortCreatedAt')}</option>
  <option value="name">{t('projects.sortName')}</option>
</select>
```

**⑤ 过滤+排序逻辑：**
```tsx
const filteredProjects = useMemo(() => {
  let items = projects;
  // Tab filter
  if (filter !== 'all') items = items.filter(p => p.project_type === filter);
  // Search
  if (searchQuery.trim()) {
    const q = searchQuery.trim().toLowerCase();
    items = items.filter(p => p.name.toLowerCase().includes(q));
  }
  // Sort
  items = [...items].sort((a, b) => {
    let cmp = 0;
    if (sortBy === 'name') cmp = a.name.localeCompare(b.name);
    else if (sortBy === 'updated_at') cmp = new Date(a.updated_at).getTime() - new Date(b.updated_at).getTime();
    else cmp = new Date(a.created_at).getTime() - new Date(b.created_at).getTime();
    return sortDir === 'desc' ? -cmp : cmp;
  });
  return items;
}, [projects, filter, searchQuery, sortBy, sortDir]);
```

**⑥ 统计栏：**
```tsx
<div className="flex items-center justify-between mb-4">
  <span className="text-xs text-zinc-500">
    {t('projects.totalProjects', { count: filteredProjects.length })}
  </span>
  <div className="flex items-center gap-2">
    {/* 排序下拉 */}
    {/* 新建按钮 */}
  </div>
</div>
```

**⑦ 表格视图增加列：** 项目负责人（owner_id → 显示邮箱前缀）、项目归属（团队内/外）

**Commit:** `feat(projects): list view tabs (all/internal/external), search, sort`

---

### Task 1.3 项目⋮操作菜单（3h）

**新建文件：** `frontend/components/ProjectContextMenu.tsx`（~130行）

```tsx
import React, { useEffect, useRef } from 'react';
import { useTranslation } from 'react-i18next';
import { Settings, Users, Bookmark, Star, Trash2 } from 'lucide-react';
import { Project } from '../types';

interface ProjectContextMenuProps {
  project: Project;
  position: { x: number; y: number };
  onClose: () => void;
  onSettings: () => void;
  onMembers: () => void;
  onToggleStar: () => void;
  onDelete: () => void;
}

export const ProjectContextMenu: React.FC<ProjectContextMenuProps> = ({
  project, position, onClose, onSettings, onMembers, onToggleStar, onDelete
}) => {
  const { t } = useTranslation();
  const ref = useRef<HTMLDivElement>(null);

  useEffect(() => {
    const handler = (e: MouseEvent) => {
      if (ref.current && !ref.current.contains(e.target as Node)) onClose();
    };
    document.addEventListener('mousedown', handler);
    return () => document.removeEventListener('mousedown', handler);
  }, [onClose]);

  // 动态定位（防溢出）
  const style: React.CSSProperties = {
    position: 'fixed',
    top: Math.min(position.y, window.innerHeight - 280),
    left: Math.min(position.x, window.innerWidth - 220),
    zIndex: 50,
  };

  const typeColors: Record<string, string> = {
    internal: 'bg-blue-500', external: 'bg-orange-500', personal: 'bg-purple-500'
  };

  const MenuItem: React.FC<{
    icon: React.ReactNode; label: string; onClick: () => void;
    danger?: boolean; disabled?: boolean;
  }> = ({ icon, label, onClick, danger, disabled }) => (
    <button
      onClick={() => { onClick(); onClose(); }}
      disabled={disabled}
      className={`w-full flex items-center gap-2.5 px-3 py-2 text-sm rounded-md transition-colors
        ${danger ? 'text-red-400 hover:bg-red-500/10' : 'text-zinc-300 hover:bg-zinc-700/50'}
        ${disabled ? 'opacity-40 cursor-not-allowed' : 'cursor-pointer'}`}
    >
      {icon}
      {label}
    </button>
  );

  return (
    <div ref={ref} style={style}
      className="w-52 bg-zinc-900 border border-zinc-700/60 rounded-xl shadow-2xl
                 py-1.5 animate-in fade-in zoom-in-95 duration-150">
      {/* Header */}
      <div className="px-3 py-2 flex items-center gap-2 border-b border-zinc-800/50 mb-1">
        <span className={`w-6 h-6 rounded-md flex items-center justify-center text-[10px]
                         font-bold text-white ${typeColors[project.project_type] || 'bg-purple-500'}`}>
          {project.name.charAt(0)}
        </span>
        <span className="text-sm text-zinc-200 font-medium truncate">{project.name}</span>
      </div>

      <MenuItem icon={<Settings size={14} />} label={t('projects.contextMenu.settings')} onClick={onSettings} />
      <MenuItem icon={<Users size={14} />} label={t('projects.contextMenu.members')} onClick={onMembers} />

      <div className="border-t border-zinc-800/50 my-1" />

      <MenuItem icon={<Bookmark size={14} />} label={t('projects.contextMenu.colorLabel')}
                onClick={() => {}} disabled />
      <MenuItem
        icon={<Star size={14} className={project.is_starred ? 'text-yellow-400 fill-yellow-400' : ''} />}
        label={project.is_starred ? t('projects.contextMenu.unstar') : t('projects.contextMenu.star')}
        onClick={onToggleStar}
      />
      <MenuItem icon={<Trash2 size={14} />} label={t('projects.contextMenu.delete')}
                onClick={onDelete} danger />
    </div>
  );
};
```

**修改 `ProjectCard.tsx`** — 增加 hover ⋮ 按钮：
```tsx
// 增加 props
onContextMenu?: (e: React.MouseEvent) => void;

// 在卡片右上角（group-hover 显示）
{onContextMenu && (
  <button
    onClick={(e) => { e.stopPropagation(); onContextMenu(e); }}
    className="absolute top-3 right-3 p-1.5 bg-zinc-900/80 hover:bg-zinc-700
               rounded-lg text-zinc-400 hover:text-white transition-colors
               opacity-0 group-hover:opacity-100"
  >
    <MoreVertical size={14} />
  </button>
)}
```

**修改 `ProjectsListView.tsx`** — 表格操作列增加 ⋮：
```tsx
// state
const [contextMenu, setContextMenu] = useState<{project: Project; x: number; y: number} | null>(null);

// 表格行
<td>
  <button onClick={(e) => setContextMenu({
    project, x: e.clientX, y: e.clientY
  })}>
    <MoreVertical size={14} />
  </button>
</td>

// 渲染菜单
{contextMenu && (
  <ProjectContextMenu
    project={contextMenu.project}
    position={{ x: contextMenu.x, y: contextMenu.y }}
    onClose={() => setContextMenu(null)}
    onSettings={() => { /* Sprint 3 */ }}
    onMembers={() => { /* Sprint 3 */ }}
    onToggleStar={() => handleToggleStar(contextMenu.project)}
    onDelete={() => handleDelete(contextMenu.project)}
  />
)}
```

**Commit:** `feat(projects): context menu for project cards and table rows`

---

### Task 1.4 `CreateProjectModal.tsx` 增强（3h）

**修改文件：** `frontend/components/CreateProjectModal.tsx`

**① 新增 state：**
```tsx
const [announcement, setAnnouncement] = useState('');
const [projectGroup, setProjectGroup] = useState('');
```

**② 名称字数限制（30字）：**
```tsx
<div>
  <div className="flex justify-between mb-2">
    <label className="text-sm font-medium text-zinc-300">{t('projects.create.name')}</label>
    <span className="text-xs text-zinc-600">{name.length}/30</span>
  </div>
  <input value={name}
    onChange={(e) => setName(e.target.value.slice(0, 30))}
    placeholder={t('projects.create.namePlaceholder')}
    className="w-full px-4 py-3 bg-zinc-800 border border-zinc-700 rounded-xl ..."
  />
</div>
```

**③ 项目公告（100字）：**
```tsx
<div>
  <div className="flex justify-between mb-2">
    <label className="text-sm font-medium text-zinc-300">{t('projects.create.announcement')}</label>
    <span className="text-xs text-zinc-600">{announcement.length}/100</span>
  </div>
  <textarea value={announcement}
    onChange={(e) => setAnnouncement(e.target.value.slice(0, 100))}
    placeholder={t('projects.create.announcementPlaceholder')}
    rows={3}
    className="w-full px-4 py-3 bg-zinc-800 border border-zinc-700 rounded-xl ... resize-none"
  />
</div>
```

**④ 项目分组输入：**
```tsx
<div>
  <label className="block text-sm font-medium text-zinc-300 mb-2">
    {t('projects.create.group')}
  </label>
  <input value={projectGroup}
    onChange={(e) => setProjectGroup(e.target.value)}
    placeholder={t('projects.create.noGroup')}
    className="w-full px-4 py-3 bg-zinc-800 border border-zinc-700 rounded-xl ..."
  />
</div>
```

**⑤ 提交时传入新字段：**
```tsx
const project = await createProject({
  name: name.trim(),
  description: description.trim() || undefined,
  project_type: projectType,
  team_id: teamId || undefined,
  project_group: projectGroup.trim() || undefined,
  // announcement 需后端支持
});
```

**⑥ 按钮文案：** `{t('projects.create.submit')}` → "创建并添加成员"

**Migration `068_project_announcement.sql`：**
```sql
-- 068_project_announcement.sql
ALTER TABLE projects ADD COLUMN IF NOT EXISTS announcement TEXT;
COMMENT ON COLUMN projects.announcement IS 'Project announcement (max 100 chars)';
```

**后端 Schema 修改** — `backend/app/schemas/projects.py`：
```python
class ProjectCreate(BaseModel):
    name: str
    description: Optional[str] = None
    project_type: str = "personal"
    team_id: Optional[str] = None
    project_group: Optional[str] = None
    announcement: Optional[str] = None      # 新增
```

**Commit:** `feat(projects): enhanced create modal with announcement and char limits`

---

### Task 1.5 i18n 补全（2h）

**修改文件：** `frontend/public/locales/zh.json` + `en.json`

**zh.json 新增：**
```json
{
  "projects": {
    "sidebar": {
      "projectList": "项目列表",
      "starredProjects": "星标项目",
      "noStarred": "暂无星标项目",
      "projectGroups": "项目分组",
      "internalProjects": "团队内项目",
      "externalProjects": "团队外项目"
    },
    "tab": {
      "all": "全部项目",
      "internal": "内部项目",
      "external": "外部项目"
    },
    "tabs": {
      "files": "文件",
      "shares": "分享",
      "trash": "回收站"
    },
    "totalProjects": "共 {{count}} 个项目",
    "searchProjects": "搜索项目",
    "sortUpdatedAt": "活跃时间",
    "sortCreatedAt": "创建时间",
    "sortName": "名称",
    "contextMenu": {
      "settings": "项目设置",
      "members": "项目成员",
      "colorLabel": "标记颜色",
      "star": "星标",
      "unstar": "取消星标",
      "delete": "删除项目"
    },
    "create": {
      "title": "创建项目",
      "name": "项目名称",
      "namePlaceholder": "请输入项目名称",
      "announcement": "项目公告",
      "announcementPlaceholder": "项目公告可以帮助新加入项目的成员快速了解项目",
      "group": "添加至项目分组",
      "noGroup": "暂无分组",
      "submit": "创建并添加成员"
    },
    "deleteConfirm": {
      "title": "确认删除项目",
      "message": "删除项目「{{name}}」后，所有文件和评论将永久删除，此操作不可撤销。",
      "confirm": "确认删除",
      "cancel": "取消"
    }
  }
}
```

**en.json 新增（对应翻译）：**
```json
{
  "projects": {
    "sidebar": {
      "projectList": "Projects",
      "starredProjects": "Starred",
      "noStarred": "No starred projects",
      "projectGroups": "Groups",
      "internalProjects": "Internal Projects",
      "externalProjects": "External Projects"
    },
    "tab": { "all": "All Projects", "internal": "Internal", "external": "External" },
    "tabs": { "files": "Files", "shares": "Shares", "trash": "Trash" },
    "totalProjects": "{{count}} projects",
    "searchProjects": "Search projects",
    "sortUpdatedAt": "Last active",
    "sortCreatedAt": "Created",
    "sortName": "Name",
    "contextMenu": {
      "settings": "Project Settings",
      "members": "Members",
      "colorLabel": "Color Label",
      "star": "Star",
      "unstar": "Unstar",
      "delete": "Delete Project"
    },
    "create": {
      "title": "Create Project",
      "name": "Project Name",
      "namePlaceholder": "Enter project name",
      "announcement": "Announcement",
      "announcementPlaceholder": "Help new members understand this project",
      "group": "Add to Group",
      "noGroup": "No group",
      "submit": "Create & Add Members"
    },
    "deleteConfirm": {
      "title": "Delete Project",
      "message": "Deleting \"{{name}}\" will permanently remove all files and comments. This cannot be undone.",
      "confirm": "Delete",
      "cancel": "Cancel"
    }
  }
}
```

**Commit:** `feat(projects): i18n keys for sidebar, tabs, context menu, create modal`

---

## 五、Sprint 2 — 项目内部页升级（~20h）

### Task 2.1 Tab 改造 — 文件/分享/回收站（3h）

**修改文件：** `frontend/pages/ProjectsPage.tsx`

**Tab 类型扩展：**
```tsx
type ProjectTab = 'files' | 'shares' | 'trash' | 'tasks';
```

**Tab UI（带 badge）：**
```tsx
<div className="flex gap-4 border-b border-zinc-800 mb-4">
  <TabButton tab="files" label={t('projects.tabs.files')} />
  <TabButton tab="shares" label={t('projects.tabs.shares')} count={shareCount} />
  <TabButton tab="trash" label={t('projects.tabs.trash')} count={trashCount} />
</div>

// TabButton 组件
const TabButton: React.FC<{ tab: string; label: string; count?: number }> = ({
  tab, label, count
}) => (
  <button onClick={() => setActiveTab(tab as ProjectTab)}
    className={`pb-2 text-sm font-medium border-b-2 transition-colors flex items-center gap-1.5
      ${activeTab === tab
        ? 'border-indigo-500 text-white'
        : 'border-transparent text-zinc-500 hover:text-zinc-300'}`}>
    {label}
    {count != null && count > 0 && (
      <span className="text-[10px] bg-zinc-700 text-zinc-300 px-1.5 py-0.5 rounded-full">
        {count}
      </span>
    )}
  </button>
);
```

**新建 `ProjectTrashView.tsx`（~150行）：**
- 调用 `fetchProjectFiles(projectId, true)` 过滤 `is_trashed`
- 每行显示：文件名 | 删除时间
- 操作：恢复 | 永久删除

**新建 `ProjectSharesView.tsx`（~150行）：**
- 调用 `GET /projects/{pid}/shares`（新API）
- 每行：分享名 | 类型 | 状态 | 查看次数 | 操作

**后端新增：**
```python
# projects_router.py
@router.get("/{project_id}/shares")
async def list_project_shares(project_id: str, auth: AuthDep):
    """List all shares for files in this project."""
    ...

@router.put("/{project_id}/files/{file_id}/restore")
async def restore_file(project_id: str, file_id: str, auth: AuthDep):
    """Restore a trashed file."""
    ...
```

**Commit:** `feat(projects): tabs for files/shares/trash with count badges`

---

### Task 2.2 文件工具栏升级（3h）

**修改文件：** `frontend/components/ProjectFilesView.tsx`

**完整工具栏：**
```tsx
<div className="flex items-center justify-between mb-4">
  <span className="text-xs text-zinc-500">
    共 {filteredFiles.length} 项
  </span>
  <div className="flex items-center gap-2">
    {/* 排序 */}
    <select value={sortBy} onChange={...}
      className="text-xs bg-zinc-800 border border-zinc-700/50 rounded-lg px-2 py-1.5">
      <option value="updated_at">更新时间</option>
      <option value="filename">名称</option>
      <option value="file_size_bytes">大小</option>
    </select>

    {/* 筛选 */}
    <FilterDropdown
      options={[
        { value: 'video', label: '视频' },
        { value: 'image', label: '图片' },
        { value: 'document', label: '文档' },
      ]}
      selected={filterType}
      onChange={setFilterType}
    />

    {/* 视图切换 */}
    <ViewToggle mode={viewMode} onChange={setViewMode} />

    {/* 上传下拉 */}
    <DropdownButton label={t('projects.upload')} icon={<Upload size={14}/>}
      className="bg-green-600 hover:bg-green-500">
      <MenuItem onClick={() => fileInputRef.current?.click()}>
        上传文件
      </MenuItem>
      <MenuItem onClick={() => folderInputRef.current?.click()}>
        上传文件夹
      </MenuItem>
    </DropdownButton>

    {/* 新建下拉 */}
    <DropdownButton label={t('projects.new')} icon={<Plus size={14}/>}
      className="bg-indigo-600 hover:bg-indigo-500">
      <MenuItem onClick={handleCreateFolder}>新建文件夹</MenuItem>
    </DropdownButton>

    {/* Hidden inputs */}
    <input ref={fileInputRef} type="file" multiple onChange={handleUpload} className="hidden" />
    <input ref={folderInputRef} type="file" {...{webkitdirectory: ''} as any}
           onChange={handleUploadFolder} className="hidden" />
  </div>
</div>
```

**Commit:** `feat(projects): file toolbar with search, sort, filter, upload/new dropdown`

---

### Task 2.3 项目内文件夹支持（6h）

**Migration `069_project_folders.sql`：**
```sql
-- 069_project_folders.sql
-- Add folder support within projects

CREATE TABLE IF NOT EXISTS project_folders (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  project_id UUID NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
  parent_id UUID REFERENCES project_folders(id) ON DELETE CASCADE,
  name VARCHAR(200) NOT NULL DEFAULT '未命名文件夹',
  created_by UUID REFERENCES auth.users(id),
  created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_project_folders_project ON project_folders(project_id);
CREATE INDEX IF NOT EXISTS idx_project_folders_parent ON project_folders(project_id, parent_id);

ALTER TABLE project_files ADD COLUMN IF NOT EXISTS folder_id UUID
  REFERENCES project_folders(id) ON DELETE SET NULL;
CREATE INDEX IF NOT EXISTS idx_project_files_folder ON project_files(project_id, folder_id);

-- RLS
ALTER TABLE project_folders ENABLE ROW LEVEL SECURITY;

CREATE POLICY "Users can read project folders" ON project_folders FOR SELECT
  USING (project_id IN (SELECT id FROM projects));
CREATE POLICY "Users can manage project folders" ON project_folders FOR ALL
  USING (project_id IN (SELECT id FROM projects))
  WITH CHECK (project_id IN (SELECT id FROM projects));

CREATE POLICY "Service role full access on project_folders" ON project_folders FOR ALL
  USING (auth.role() = 'service_role')
  WITH CHECK (auth.role() = 'service_role');

DROP TRIGGER IF EXISTS update_project_folders_updated_at ON project_folders;
CREATE TRIGGER update_project_folders_updated_at
  BEFORE UPDATE ON project_folders FOR EACH ROW EXECUTE FUNCTION update_updated_at_column();
```

**后端 API（projects_router.py 新增 5 endpoint）：**
```python
@router.get("/{project_id}/folders")
async def list_folders(project_id: str, parent_id: Optional[str] = None, auth: AuthDep):
    ...

@router.post("/{project_id}/folders")
async def create_folder(project_id: str, data: CreateFolderRequest, auth: AuthDep):
    ...

@router.put("/{project_id}/folders/{folder_id}")
async def rename_folder(project_id: str, folder_id: str, data: RenameFolderRequest, auth: AuthDep):
    ...

@router.delete("/{project_id}/folders/{folder_id}")
async def delete_folder(project_id: str, folder_id: str, auth: AuthDep):
    ...

@router.put("/{project_id}/files/{file_id}/move")
async def move_file(project_id: str, file_id: str, data: MoveFileRequest, auth: AuthDep):
    # data.folder_id: str | null
    ...
```

**前端 Type 新增（types.ts）：**
```typescript
export interface ProjectFolder {
  id: string;
  project_id: string;
  parent_id: string | null;
  name: string;
  created_by: string | null;
  created_at: string;
  updated_at: string;
}
```

**前端 Service 新增（projectsService.ts + 6 方法）：**
```typescript
export const fetchProjectFolders = async (
  projectId: string, parentId?: string | null
): Promise<ProjectFolder[]> => {
  const params = new URLSearchParams();
  if (parentId) params.set('parent_id', parentId);
  const res = await fetch(`${getApiUrl()}/api/v1/projects/${projectId}/folders?${params}`,
    { headers: await getAuthHeaders() });
  if (!res.ok) throw new Error('Failed to fetch folders');
  return (await res.json()).data || [];
};

export const createProjectFolder = async (
  projectId: string, name: string, parentId?: string | null
): Promise<ProjectFolder> => { ... };

export const renameProjectFolder = async (
  projectId: string, folderId: string, name: string
): Promise<ProjectFolder> => { ... };

export const deleteProjectFolder = async (
  projectId: string, folderId: string
): Promise<void> => { ... };

export const moveFileToFolder = async (
  projectId: string, fileId: string, folderId: string | null
): Promise<void> => { ... };

// 文件列表增加 folder_id 参数
export const fetchProjectFiles = async (
  projectId: string, options?: { includeTrashed?: boolean; folderId?: string | null }
): Promise<ProjectFile[]> => {
  const params = new URLSearchParams();
  if (options?.includeTrashed) params.set('include_trashed', 'true');
  if (options?.folderId) params.set('folder_id', options.folderId);
  ...
};
```

**前端组件修改（ProjectFilesView.tsx）：**
```tsx
// 新增 state
const [currentFolderId, setCurrentFolderId] = useState<string | null>(null);
const [folders, setFolders] = useState<ProjectFolder[]>([]);
const [folderChain, setFolderChain] = useState<ProjectFolder[]>([]);

// 加载逻辑
useEffect(() => {
  loadFolders();
  loadFiles();
}, [project.id, currentFolderId]);

const loadFolders = async () => {
  const data = await fetchProjectFolders(project.id, currentFolderId);
  setFolders(data);
};

// 面包屑
<nav className="flex items-center gap-1 text-sm mb-4">
  <button onClick={() => setCurrentFolderId(null)}
    className="text-zinc-400 hover:text-white">{project.name}</button>
  {folderChain.map(f => (
    <React.Fragment key={f.id}>
      <ChevronRight size={14} className="text-zinc-600" />
      <button onClick={() => setCurrentFolderId(f.id)}
        className="text-zinc-400 hover:text-white">{f.name}</button>
    </React.Fragment>
  ))}
</nav>

// 渲染文件夹卡片（复用 FolderCard 样式）
{folders.map(folder => (
  <div key={folder.id} onClick={() => setCurrentFolderId(folder.id)}
    onDoubleClick={() => setCurrentFolderId(folder.id)}
    className="... cursor-pointer">
    <FolderIcon size={44} className="text-amber-400" />
    <p>{folder.name}</p>
  </div>
))}
```

**Commits：**
- `feat(db): migration 069 - project_folders table and file folder_id`
- `feat(api): project folder CRUD and file move endpoints`
- `feat(projects): folder navigation with breadcrumb in project files`

---

### Task 2.4 批量选择 + 操作栏（4h）

**修改：** `ProjectFilesView.tsx` + `FileCard.tsx`

**State：**
```tsx
const [selectedIds, setSelectedIds] = useState<Set<string>>(new Set());

const toggleSelect = (id: string, e: React.MouseEvent) => {
  setSelectedIds(prev => {
    const next = new Set(prev);
    if (e.shiftKey && lastSelectedId) {
      // 范围选择
      const allIds = files.map(f => f.id);
      const start = allIds.indexOf(lastSelectedId);
      const end = allIds.indexOf(id);
      const range = allIds.slice(Math.min(start, end), Math.max(start, end) + 1);
      range.forEach(rid => next.add(rid));
    } else if (e.metaKey || e.ctrlKey) {
      next.has(id) ? next.delete(id) : next.add(id);
    } else {
      return new Set([id]);
    }
    return next;
  });
  setLastSelectedId(id);
};
```

**浮动操作栏：**
```tsx
{selectedIds.size > 0 && (
  <div className="fixed bottom-6 left-1/2 -translate-x-1/2 z-40
                  bg-zinc-900 border border-zinc-700 rounded-xl px-5 py-3
                  flex items-center gap-4 shadow-2xl">
    <span className="text-sm text-zinc-300">已选 {selectedIds.size} 项</span>
    <button onClick={handleBatchMove}
      className="text-sm text-zinc-300 hover:text-white px-3 py-1.5 hover:bg-zinc-800 rounded-lg">
      移动到
    </button>
    <button onClick={handleBatchSetStatus}
      className="text-sm text-zinc-300 hover:text-white px-3 py-1.5 hover:bg-zinc-800 rounded-lg">
      设置状态
    </button>
    <button onClick={handleBatchDelete}
      className="text-sm text-red-400 hover:text-red-300 px-3 py-1.5 hover:bg-red-500/10 rounded-lg">
      删除
    </button>
    <button onClick={() => setSelectedIds(new Set())}
      className="text-sm text-zinc-500 hover:text-zinc-300 px-3 py-1.5">
      取消
    </button>
  </div>
)}
```

**FileCard.tsx props 增加：**
```tsx
isSelected?: boolean;
onToggleSelect?: (e: React.MouseEvent) => void;
selectable?: boolean;
```

**Commit:** `feat(projects): batch selection with floating action bar`

---

### Task 2.5 项目标题区（4h）

**修改：** `ProjectFilesView.tsx` 头部

```tsx
<div className="flex items-center justify-between mb-2">
  <div className="flex items-center gap-3">
    <button onClick={onBack} className="p-2 text-zinc-400 hover:text-white hover:bg-zinc-800 rounded-xl">
      <ArrowLeft size={20} />
    </button>
    <h1 className="text-2xl font-bold text-white">{project.name}</h1>
    {/* 复用 ReviewStatusDropdown */}
    <ReviewStatusDropdown
      value={projectStatus}
      onChange={handleSetProjectStatus}
      trigger={
        <button className="flex items-center gap-1.5 px-3 py-1.5 bg-zinc-800 hover:bg-zinc-700
                           rounded-lg text-sm text-zinc-400">
          设置状态 <ChevronDown size={12} />
        </button>
      }
    />
  </div>
  <div className="flex items-center gap-3">
    {/* 成员头像 (Sprint 3 接真实数据，Sprint 2 占位) */}
    <div className="flex items-center -space-x-2">
      <div className="w-7 h-7 rounded-full bg-indigo-500/30 flex items-center justify-center
                      text-[10px] text-indigo-300 border-2 border-zinc-900">
        {/* owner initial */}
      </div>
      <button className="w-7 h-7 rounded-full bg-zinc-800 border-2 border-zinc-900
                         flex items-center justify-center text-zinc-500 hover:text-zinc-300">
        <Plus size={12} />
      </button>
    </div>
    {/* 搜索 */}
    <div className="relative">
      <Search size={14} className="absolute left-2.5 top-1/2 -translate-y-1/2 text-zinc-500" />
      <input value={searchQuery} onChange={e => setSearchQuery(e.target.value)}
        placeholder="搜索文件"
        className="w-40 pl-8 pr-3 py-1.5 text-xs bg-zinc-800/60 border border-zinc-700/50
                   rounded-lg text-zinc-200 placeholder-zinc-500" />
    </div>
  </div>
</div>
```

**Commit:** `feat(projects): project header with status dropdown and member avatars`

---

## 六、Sprint 3 — 设置与成员（~12h）

### Task 3.1 项目设置面板（3h）

**新建：** `frontend/components/ProjectSettingsPanel.tsx`（~200行）

侧滑面板（从右侧滑入），包含：
- 项目名称编辑
- 项目公告编辑
- 项目类型选择
- 项目分组
- 危险区：删除项目

API：复用 `PUT /projects/{id}`

**Commit:** `feat(projects): project settings panel with edit and delete`

---

### Task 3.2 项目成员管理（5h）

**Migration `070_project_members.sql`：**
```sql
CREATE TABLE IF NOT EXISTS project_members (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  project_id UUID NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
  user_id UUID NOT NULL REFERENCES auth.users(id) ON DELETE CASCADE,
  role VARCHAR(20) NOT NULL DEFAULT 'viewer'
    CHECK (role IN ('admin', 'editor', 'viewer')),
  invited_by UUID REFERENCES auth.users(id),
  created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  UNIQUE(project_id, user_id)
);

ALTER TABLE project_members ENABLE ROW LEVEL SECURITY;

CREATE POLICY "Users can read project members" ON project_members FOR SELECT
  USING (project_id IN (SELECT id FROM projects));

CREATE POLICY "Admins can manage members" ON project_members FOR ALL
  USING (
    project_id IN (SELECT id FROM projects WHERE owner_id = auth.uid())
    OR (user_id = auth.uid() AND role = 'admin')
  )
  WITH CHECK (project_id IN (SELECT id FROM projects WHERE owner_id = auth.uid()));

CREATE POLICY "Service role full access on project_members" ON project_members FOR ALL
  USING (auth.role() = 'service_role')
  WITH CHECK (auth.role() = 'service_role');
```

**后端 API（4 endpoint）：**
```
GET    /projects/{pid}/members           → 列表
POST   /projects/{pid}/members           → 添加 {user_id, role}
PUT    /projects/{pid}/members/{mid}     → 改角色
DELETE /projects/{pid}/members/{mid}     → 移除
```

**新建：** `ProjectMembersPanel.tsx`（~200行）

**Commits：**
- `feat(db): migration 070 - project_members table with RLS`
- `feat(projects): member management panel with invite and roles`

---

### Task 3.3 文件状态标签（2h）

**修改 `FileCard.tsx`** — 复用 `ReviewStatusBadge`：
```tsx
import { ReviewStatusBadge } from './ReviewStatusBadge';

// 在卡片左上角
{file.review_status && (
  <ReviewStatusBadge status={file.review_status} size="sm" />
)}
```

**筛选增加状态选项。**

**Commit:** `feat(projects): file review status badges and status filter`

---

### Task 3.4 项目分组筛选（2h）

从 projects 数据提取 unique groups，侧边栏按分组展示。

**Commit:** `feat(projects): project group filtering in sidebar`

---

## 七、Sprint 4 — 分享、右键、收集（~12h）

### Task 4.1 分享功能（4h）

复用已有 `shares` 表。新建 `ProjectShareModal.tsx`。

**Commit:** `feat(projects): share creation and management`

### Task 4.2 文件右键菜单（3h）

新建 `ProjectFileContextMenu.tsx`（~150行）。

菜单项：下载 / 重命名 / 移动到 / 复制到 / 设置状态 / 版本历史 / 文件信息 / 分享 / 删除

**Commit:** `feat(projects): file context menu with full actions`

### Task 4.3 收集功能（3h）

**Migration `071_project_collections.sql`：**
```sql
CREATE TABLE IF NOT EXISTS project_collections (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  project_id UUID NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
  collection_code VARCHAR(20) UNIQUE NOT NULL,
  allowed_types TEXT[],
  max_file_size_mb INT DEFAULT 500,
  deadline TIMESTAMPTZ,
  is_active BOOLEAN DEFAULT true,
  created_by UUID REFERENCES auth.users(id),
  created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
```

新建 `ProjectCollectModal.tsx` + 公开上传页 `/collect/{code}`。

**Commit:** `feat(projects): collection links for external uploads`

### Task 4.4 标记颜色 + 打磨（2h）

```sql
ALTER TABLE projects ADD COLUMN IF NOT EXISTS color_label VARCHAR(20);
```

**Commit:** `feat(projects): color labels and final polish`

---

## 八、文件清单

### 新建文件

| 文件 | Sprint | 行数 |
|------|--------|------|
| `frontend/components/ProjectsSidebar.tsx` | 1 | ~280 |
| `frontend/components/ProjectContextMenu.tsx` | 1 | ~130 |
| `frontend/components/ProjectTrashView.tsx` | 2 | ~150 |
| `frontend/components/ProjectSharesView.tsx` | 2 | ~150 |
| `frontend/components/ProjectSettingsPanel.tsx` | 3 | ~200 |
| `frontend/components/ProjectMembersPanel.tsx` | 3 | ~200 |
| `frontend/components/ProjectShareModal.tsx` | 4 | ~200 |
| `frontend/components/ProjectFileContextMenu.tsx` | 4 | ~150 |
| `frontend/components/ProjectCollectModal.tsx` | 4 | ~150 |
| `supabase/migrations/068_project_announcement.sql` | 1 | ~5 |
| `supabase/migrations/069_project_folders.sql` | 2 | ~40 |
| `supabase/migrations/070_project_members.sql` | 3 | ~30 |
| `supabase/migrations/071_project_collections.sql` | 4 | ~20 |

### 修改文件

| 文件 | Sprint |
|------|--------|
| `pages/ProjectsPage.tsx` | 1, 2 |
| `components/ProjectsListView.tsx` | 1 |
| `components/ProjectCard.tsx` | 1 |
| `components/ProjectFilesView.tsx` | 2 |
| `components/FileCard.tsx` | 2, 3 |
| `components/CreateProjectModal.tsx` | 1 |
| `services/projectsService.ts` | 2, 3, 4 |
| `types.ts` | 2, 3 |
| `public/locales/zh.json` | 1 |
| `public/locales/en.json` | 1 |
| `backend/app/api/projects_router.py` | 1, 2, 3, 4 |
| `backend/app/schemas/projects.py` | 1, 2, 3 |

---

## 九、工时

| Sprint | 内容 | 前端 | 后端 | 合计 |
|--------|------|------|------|------|
| 1 | 列表页升级 | 12h | 4h | **16h** |
| 2 | 内部页升级 | 14h | 6h | **20h** |
| 3 | 设置与成员 | 6h | 6h | **12h** |
| 4 | 分享与收集 | 6h | 6h | **12h** |
| **合计** | | **38h** | **22h** | **60h** |
