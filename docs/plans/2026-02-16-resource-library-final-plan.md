# MediaHub 资源库审计 & 实施计划（终版）

> 审计日期: 2026-02-16
> MediaHub 版本: v0.7.2
> 对标: 分秒帧(Mediatrack) 生产环境 (app.mediatrack.cn)
> 综合评分: **4.8/10** — 框架已搭好，但文件操作核心交互几乎为零

---

## 状态标记说明
- ✅ 功能完善 | 🟡 有但有差距 | 🔴 缺失 | ⚪ 不适用

---

## A. 侧边栏导航（6/10）

| 功能项 | MediaHub | 分秒帧 | 差距描述 |
|--------|----------|--------|---------|
| 侧边栏结构和层级 | 🟡 左侧主导航+中间面板 | ✅ 极窄icon栏+展开面板 | 分秒帧空间利用更好，MediaHub两层侧边栏占用过多水平空间 |
| 文件夹树展开/折叠 | 🟡 仅一级"我的资源" | ✅ 可展开子文件夹，有箭头指示 | MediaHub侧边栏不展示文件夹树层级 |
| 智能文件夹 | ✅ 有"智能文件夹"区块 | ✅ 有智能文件夹树节点 | 对等 |
| 标签功能 | 🔴 侧边栏无标签入口 | ✅ 侧边栏有"标签"树节点 | MediaHub缺少侧边栏标签导航 |
| 分享管理入口 | ✅ | ✅ | 对等 |
| 回收站入口 | ✅ | ✅ | 对等 |
| 侧边栏拖拽目标 | 🔴 不能拖文件到侧边栏文件夹 | ✅ 支持 | 见模块D拖拽实现方案 |
| 星标/收藏夹快速访问 | 🔴 "快速访问"只是占位 | ✅ 有"★ 星标项目"置顶区域 | 需实现持久化到user preference |

**MediaHub 独有**: RipVault、积分系统、项目、待办事项
**分秒帧 独有**: 拍摄上云、标签树导航、底部附加功能按钮栏

### 改进项

#### 【P1】侧边栏文件夹树递归展开
- **问题**: 只有一级"我的资源"，无法展开子文件夹
- **分秒帧做法**: 团队资源库可展开子文件夹，有箭头指示
- **MediaHub 现状**: 仅一级展示，只能在主区域进入子文件夹
- **代码方案**: 新建 `FolderTreeItem` 递归组件，支持展开/折叠
  - 涉及文件: `ResourcesView.tsx`
  - 预估: 2h
  - 同时增加拖拽目标支持:
```tsx
// 侧边栏文件夹按钮增加:
onDragOver={(e) => { e.preventDefault(); setHover(true); }}
onDragLeave={() => setHover(false)}
onDrop={(e) => {
  const data = e.dataTransfer.getData('application/mediahub-items');
  if (data) {
    const { ids } = JSON.parse(data);
    moveResources(ids, folder.id, selectedLibraryId);
    reload();
  }
  setHover(false);
}}
```

#### 【P2】侧边栏增加"标签"导航入口
- **问题**: 只在详情页有标签，无法通过标签快速筛选
- **分秒帧做法**: 侧边栏有"标签"树节点，可展开查看所有标签
- **方向**: 增加标签树节点，点击标签自动筛选文件列表

#### 【P3】极窄icon栏+展开面板布局
- **方向**: 考虑采用分秒帧的极窄icon栏布局，节省横向空间

---

## B. 工具栏（6/10）

| 功能项 | MediaHub | 分秒帧 | 差距描述 |
|--------|----------|--------|---------|
| 排序功能 | 🟡 6项耦合 | ✅ 4字段×2方向=8组合 | 分秒帧字段和方向独立选择 |
| 筛选功能 | 🔴 无 | ✅ 有筛选按钮(漏斗图标) | 完全缺少 |
| 视图切换 | ✅ | ✅ | 对等 |
| 搜索功能 | 🟡 仅全局搜索(⌘K) | ✅ 全局+文件夹内搜索 | 缺少当前文件夹搜索 |
| 上传按钮 | ✅ | ✅ 有下拉(文件/文件夹) | 分秒帧上传按钮带下拉 |
| 新建按钮 | 🟡 仅"新建文件夹" | ✅ 7种新建类型 | MediaHub仅1种 |
| 收集功能 | 🔴 | ✅ 绿色"收集"按钮 | 缺少 |
| 全选/取消 | 🔴 | ✅ 工具栏有按钮 | 缺少 |

### 改进项

#### 【P1】增加筛选功能
- **问题**: 完全没有筛选功能，大量文件时无法快速定位
- **分秒帧做法**: 漏斗图标，支持按类型/标签/日期筛选
- **MediaHub 现状**: 无筛选按钮
- **代码方案**: 在工具栏增加 Filter 面板
  - 涉及文件: `ResourcesView.tsx`
  - 预估: 2h

#### 【P1】"新建"按钮改造为下拉菜单
- **问题**: 仅支持新建文件夹
- **分秒帧做法**: 下拉菜单支持7种新建类型
- **代码方案**: 改为下拉菜单，至少支持新建文件夹+智能文件夹+资源库
  - 涉及文件: `ResourcesView.tsx`
  - 预估: 1.5h
  - 工具栏按钮颜色优化: 上传改绿色 `bg-emerald-600`，新建改蓝色下拉

#### 【P1】资源库内搜索框
- **问题**: 只有全局搜索(⌘K)，无法在当前文件夹内搜索
- **分秒帧做法**: 嵌入工具栏的搜索框，搜索当前文件夹
- **代码方案**: 工具栏增加搜索输入框
  - 涉及文件: `ResourcesView.tsx`
  - 预估: 1.5h

#### 【P2】上传按钮增加下拉
- **方向**: 区分上传文件/上传文件夹，增加 `<input webkitdirectory directory>`

#### 【P3】考虑增加"收集"功能
- **方向**: 关联 Phase 3 分享系统的 delivery 类型

---

## C. 文件卡片（5/10）

| 功能项 | MediaHub | 分秒帧 | 差距描述 |
|--------|----------|--------|---------|
| 网格卡片设计 | 🟡 文件夹纯图标；文件有缩略图 | ✅ 文件夹有内容预览；文件有三点菜单 | 分秒帧信息量更大 |
| 列表模式 | 🟡 信息少 | ✅ 列头可排序，多列信息 | 缺少文件大小、类型等列 |
| 文件缩略图 | 🔴 视频仅紫色占位色块+Film图标 | ✅ 真实帧截图 | 无实际帧截图 |
| 时间戳格式 | 🟡 英文"Feb 15, 2026" | ✅ 中文"2026-02-15 20:47" | 不够精确 |
| hover效果 | 🟡 仅显示删除按钮 | ✅ 选择框+三点菜单 | 不完善 |
| ⋮ 三点菜单 | 🔴 无 | ✅ 每个卡片右下角有 | 触摸屏用户无法操作 |

### 改进项

#### 【P0】文件缩略图升级
- **问题**: 视频只有紫色占位色块+通用Film图标，无实际帧截图
- **分秒帧做法**: 显示视频真实帧截图
- **MediaHub 现状**: `ResourceCard.tsx` 使用通用色块占位
- **代码方案**: 改造 `ResourceCard.tsx` 缩略图逻辑
  - 视频: 使用 `thumbnail_path` 或 `cover_image_path` 显示真实帧
  - Office: Word蓝 / PPT橙 / Excel绿 / PDF红 专属大图标
  - 图片: 直接使用缩略图
  - 文件夹: 合成内部文件缩略图
  - 涉及文件: `ResourceCard.tsx`
  - 预估: 3h

#### 【P1】文件卡片增加⋮三点菜单 + 悬浮操作层
- **问题**: 无悬浮效果，只能右键；触摸屏用户无法操作
- **分秒帧做法**: hover显示选择框+三点菜单
- **MediaHub 现状**: hover仅显示删除按钮
- **代码方案**: 每个卡片增加:
  - 左上角: 勾选框（hover 或多选模式时显示）
  - 右下角: ⋮ 三点操作菜单按钮
  - 整体: hover 时有微妙的边框/阴影变化
  - 涉及文件: `ResourceCard.tsx`, `FolderCard.tsx`
  - 预估: 2h

#### 【P1】文件夹卡片增加内容预览
- **问题**: 纯空文件夹图标，信息量为零
- **分秒帧做法**: 文件夹卡片显示内部2-3个文件的小图标预览
- **代码方案**: 
  - 涉及文件: `FolderCard.tsx`, `resourceService.ts`
  - 预估: 2h

#### 【P2】时间戳改为中文格式，精确到分钟
- **方向**: "Feb 15, 2026" → "2026-02-15 14:30"
- 涉及文件: `ResourceCard.tsx`, `FolderCard.tsx`, `ResourceDetail.tsx`
- 预估: 0.5h

#### 【P2】列表视图增强
- **方向**: 增加列头可排序（文件类型、大小、创建者、更新时间）
- **代码方案**:
```tsx
<div className="flex items-center gap-4 px-4 py-2 border-b border-zinc-800/50 text-xs text-zinc-500">
  <div className="w-8" /> {/* checkbox */}
  <div className="w-10" /> {/* icon */}
  <div className="flex-1 cursor-pointer hover:text-zinc-300" onClick={() => toggleSort('name')}>名称</div>
  <div className="w-20 cursor-pointer">类型</div>
  <div className="w-24 cursor-pointer">大小</div>
  <div className="w-32 cursor-pointer">修改时间</div>
  <div className="w-20">操作</div>
</div>
```
  - 涉及文件: `ResourcesView.tsx`
  - 预估: 1h

#### 【P3】hover效果增加选择框

---

## D. 文件操作（2/10）⬅️ 核心短板

| 功能项 | MediaHub | 分秒帧 | 差距描述 |
|--------|----------|--------|---------|
| 文件右键菜单 | 🟡 7项(2项灰色) | ✅ 9项全可用 | 复制到/移动到灰色不可用 |
| 文件夹右键菜单 | 🔴 仅4项 | ✅ 9项 | 严重缺项 |
| 空白区域右键 | 🔴 浏览器默认 | ✅ 4项(上传/新建/刷新) | 缺少 |
| 复制到 | 🟡 灰色不可用 | ✅ 有文件夹选择对话框 | 需修复 |
| 移动到 | 🟡 灰色不可用 | ✅ 可用 | 需修复 |
| 拖拽移动 | 🔴 仅拖拽上传 | ✅ 文件→文件夹/侧边栏 | 完全未实现 |
| 重命名 | ✅ | ✅ | 对等（缺F2/双击入口） |
| 删除/回收站 | ✅ | ✅ | 对等 |
| 下载 | ✅ | ✅ 多了打包下载 | 缺打包下载 |
| 键盘快捷键 | 🔴 仅ESC | ✅ Ctrl+C/X/V/A等 | 完全缺失 |

### 改进项

#### 【P0】修复"复制到"和"移动到" + FolderPickerModal
- **问题**: 右键菜单中 "Copy To" 和 "Move To" 都是 `disabled: true`，`resourceService.ts` 无相关函数
- **分秒帧做法**: 右键→"移动到"→弹出文件夹选择树对话框→选中目标→确认
- **MediaHub 现状**: 菜单项存在但灰色不可用，服务层无实现
- **代码方案**:

**1) resourceService.ts 新增函数:**
```typescript
// 移动文件到指定文件夹
export async function moveResource(
  resourceItemId: string,
  targetFolderId: string | null,
  targetLibraryId?: string | null
): Promise<void> {
  const { error } = await supabase
    .from('resource_items')
    .update({
      folder_id: targetFolderId,
      ...(targetLibraryId !== undefined ? { library_id: targetLibraryId } : {}),
    })
    .eq('id', resourceItemId);
  if (error) throw error;
}

// 批量移动
export async function moveResources(
  resourceItemIds: string[],
  targetFolderId: string | null,
  targetLibraryId?: string | null
): Promise<void> {
  const { error } = await supabase
    .from('resource_items')
    .update({
      folder_id: targetFolderId,
      ...(targetLibraryId !== undefined ? { library_id: targetLibraryId } : {}),
    })
    .in('id', resourceItemIds);
  if (error) throw error;
}

// 复制文件（创建新 resource_item 指向同一个 resource）
export async function copyResource(
  resourceId: string,
  targetScopeType: 'personal' | 'team',
  targetScopeId: string,
  targetFolderId: string | null,
  targetLibraryId?: string | null
): Promise<ResourceItem> {
  const user = (await supabase.auth.getUser()).data.user;
  if (!user) throw new Error('Not authenticated');
  const { data, error } = await supabase
    .from('resource_items')
    .insert({
      resource_id: resourceId,
      scope_type: targetScopeType,
      scope_id: targetScopeId,
      folder_id: targetFolderId,
      library_id: targetLibraryId || null,
      added_by: user.id,
    })
    .select('*, resource:resources(*)')
    .single();
  if (error) throw error;
  return data;
}

// 移动文件夹
export async function moveFolder(
  folderId: string,
  targetParentId: string | null,
  targetLibraryId?: string | null
): Promise<void> {
  const { error } = await supabase
    .from('folders')
    .update({
      parent_id: targetParentId,
      ...(targetLibraryId !== undefined ? { library_id: targetLibraryId } : {}),
    })
    .eq('id', folderId);
  if (error) throw error;
}
```

**2) 新建 `FolderPickerModal.tsx`:**
```tsx
interface FolderPickerModalProps {
  isOpen: boolean;
  onClose: () => void;
  onSelect: (folderId: string | null, libraryId?: string | null) => void;
  title: string; // "移动到" 或 "复制到"
  scopeType: 'personal' | 'team';
  scopeId: string;
  excludeFolderIds?: string[]; // 排除自身及子文件夹（避免循环）
}
// 内部实现:
// 1. 渲染完整的文件夹树（含资源库层级）
// 2. 支持展开/折叠
// 3. 高亮当前选中目标
// 4. "新建文件夹"按钮（在目标位置新建）
// 5. 确认/取消按钮
```
预估: 4h

**3) 修改 ResourcesView.tsx 右键菜单:**
```tsx
{
  label: t('resources.copyTo'),
  icon: <Copy size={14} />,
  onClick: () => {
    setOperationMode('copy');
    setOperationTargets([item]);
    setShowFolderPicker(true);
  },
},
{
  label: t('resources.moveTo'),
  icon: <Move size={14} />,
  onClick: () => {
    setOperationMode('move');
    setOperationTargets([item]);
    setShowFolderPicker(true);
  },
},
```
预估: 2h

#### 【P0】实现拖拽移动文件/文件夹
- **问题**: 仅有拖拽上传，没有文件夹内拖拽移动
- **分秒帧做法**: 拖拽文件→侧边栏文件夹/内容区文件夹卡片→移动；支持多选拖拽
- **MediaHub 现状**: 完全未实现
- **代码方案**:

**ResourceCard.tsx 增加拖拽源:**
```tsx
<div
  draggable
  onDragStart={(e) => {
    const dragIds = selectedIds.has(item.id) 
      ? Array.from(selectedIds) 
      : [item.id];
    e.dataTransfer.setData('application/mediahub-items', JSON.stringify({
      type: 'resources',
      ids: dragIds,
    }));
    e.dataTransfer.effectAllowed = 'move';
    const badge = document.createElement('div');
    badge.textContent = `${dragIds.length} 个文件`;
    badge.className = 'bg-indigo-600 text-white px-3 py-1.5 rounded-lg text-sm';
    document.body.appendChild(badge);
    e.dataTransfer.setDragImage(badge, 0, 0);
    setTimeout(() => document.body.removeChild(badge), 0);
  }}
>
```

**FolderCard.tsx 增加拖拽目标:**
```tsx
<div
  onDragOver={(e) => {
    e.preventDefault();
    e.dataTransfer.dropEffect = 'move';
    setDragHover(true);
  }}
  onDragLeave={() => setDragHover(false)}
  onDrop={(e) => {
    e.preventDefault();
    setDragHover(false);
    const data = e.dataTransfer.getData('application/mediahub-items');
    if (data) {
      const { type, ids } = JSON.parse(data);
      if (type === 'resources') {
        onDropResources?.(ids, folder.id);
      }
    }
  }}
  className={`... ${dragHover ? 'ring-2 ring-indigo-500 bg-indigo-500/10' : ''}`}
>
```
- 涉及文件: `ResourceCard.tsx`, `FolderCard.tsx`, `ResourcesView.tsx`（侧边栏也需要作为拖拽目标）
- 预估: 5h

#### 【P0】实现键盘快捷键系统
- **问题**: 仅ESC关闭详情面板，无任何其他快捷键
- **分秒帧做法 & 行业标准**: Ctrl+C/X/V/A, Delete, F2, Enter, ←→, Ctrl+Shift+N
- **MediaHub 现状**: 完全缺失
- **代码方案**: 新建 `hooks/useFileKeyboard.ts`

```typescript
import { useEffect } from 'react';

interface ClipboardState {
  mode: 'copy' | 'cut' | null;
  itemIds: string[];
  itemType: 'resource' | 'folder';
}

export function useFileKeyboard({
  selectedIds, setSelectedIds, allItems,
  onTrash, onRename, onOpen, onPaste,
  clipboard, setClipboard, onNewFolder, canDo,
}: { /* 参数类型略 */ }) {
  useEffect(() => {
    const handler = (e: KeyboardEvent) => {
      const mod = e.metaKey || e.ctrlKey;
      const target = e.target as HTMLElement;
      if (target.tagName === 'INPUT' || target.tagName === 'TEXTAREA' || target.isContentEditable) return;

      if (mod && e.key === 'a') { e.preventDefault(); setSelectedIds(new Set(allItems.map(i => i.id))); }
      if (mod && e.key === 'c' && selectedIds.size > 0) { e.preventDefault(); setClipboard({ mode: 'copy', itemIds: Array.from(selectedIds), itemType: 'resource' }); }
      if (mod && e.key === 'x' && selectedIds.size > 0) { e.preventDefault(); setClipboard({ mode: 'cut', itemIds: Array.from(selectedIds), itemType: 'resource' }); }
      if (mod && e.key === 'v' && clipboard.mode) { e.preventDefault(); onPaste(clipboard); }
      if ((e.key === 'Delete' || e.key === 'Backspace') && selectedIds.size > 0) { e.preventDefault(); onTrash(Array.from(selectedIds)); }
      if (e.key === 'F2' && selectedIds.size === 1) { e.preventDefault(); onRename(Array.from(selectedIds)[0]); }
      if (e.key === 'Enter' && selectedIds.size === 1) { e.preventDefault(); onOpen(Array.from(selectedIds)[0]); }
      if (mod && e.shiftKey && e.key === 'N') { e.preventDefault(); onNewFolder(); }
      if (e.key === 'Escape') { setSelectedIds(new Set()); }
    };
    document.addEventListener('keydown', handler);
    return () => document.removeEventListener('keydown', handler);
  }, [selectedIds, allItems, clipboard]);
}
```
- 涉及文件: 新建 `hooks/useFileKeyboard.ts`, `ResourcesView.tsx`
- 预估: 3h

#### 【P1】文件夹右键菜单补全
- **问题**: 仅4项(打开/重命名/分享/删除)
- **分秒帧做法**: 9项全可用
- **MediaHub 现状**: 缺少下载、复制到、移动到
- **代码方案**: 修改 `ResourcesView.tsx` 文件夹右键菜单，增加缺失项
- 预估: 1h

#### 【P1】增加空白区域右键菜单
- **问题**: 空白区域右键弹出浏览器默认菜单
- **分秒帧做法**: 4项: 上传文件、上传文件夹、新建(子菜单)、刷新
- **代码方案**: `ResourcesView.tsx` 空白区域增加 `onContextMenu` 处理
- 预估: 1h

#### 【P2】增加"打包下载"功能
- **方向**: 支持选中多个文件打包为 ZIP 下载

#### 【P3】增加"导出文件夹目录"功能

---

## E. 批量操作（1/10）⬅️ 核心短板

| 功能项 | MediaHub | 分秒帧 | 差距描述 |
|--------|----------|--------|---------|
| 选中模式 | 🔴 无复选框 | ✅ 右键即进入选中模式 | 完全缺失 |
| 全选/取消 | 🔴 | ✅ 顶部按钮 | 完全缺失 |
| 批量操作工具栏 | 🔴 | ✅ 下载/移动/标签/更多/分享 | 完全缺失 |
| Shift+Click 范围选 | 🔴 | ✅ | 未实现 |
| Ctrl/Cmd+Click 多选 | 🔴 | ✅ | 未实现 |

### 改进项

#### 【P0】实现批量选择 + 批量操作工具栏
- **问题**: 没有任何多选机制，没有批量操作
- **分秒帧做法**: 右键进入选中模式，顶部出现全选/取消，工具栏变为批量操作
- **MediaHub 现状**: 完全缺失
- **代码方案**:

**1) ResourcesView.tsx 新增选择状态 + 点击逻辑:**
```tsx
const [selectedIds, setSelectedIds] = useState<Set<string>>(new Set());
const [lastSelectedId, setLastSelectedId] = useState<string | null>(null);

const handleItemClick = (id: string, e: React.MouseEvent) => {
  if (e.metaKey || e.ctrlKey) {
    // Ctrl+Click: 切换选中
    setSelectedIds(prev => {
      const next = new Set(prev);
      next.has(id) ? next.delete(id) : next.add(id);
      return next;
    });
    setLastSelectedId(id);
  } else if (e.shiftKey && lastSelectedId) {
    // Shift+Click: 范围选择
    const allIds = [...childFolders.map(f => `folder-${f.id}`), ...sortedItems.map(i => i.id)];
    const startIdx = allIds.indexOf(lastSelectedId);
    const endIdx = allIds.indexOf(id);
    if (startIdx !== -1 && endIdx !== -1) {
      const [from, to] = startIdx < endIdx ? [startIdx, endIdx] : [endIdx, startIdx];
      setSelectedIds(new Set([...selectedIds, ...allIds.slice(from, to + 1)]));
    }
  } else {
    handleResourceClick(sortedItems.find(i => i.id === id)!);
  }
};
```

**2) 批量操作浮动工具栏:**
```tsx
{selectedIds.size > 0 && (
  <div className="fixed bottom-6 left-1/2 -translate-x-1/2 z-50 flex items-center gap-2 px-4 py-2.5 bg-zinc-900/95 backdrop-blur border border-zinc-700 rounded-2xl shadow-2xl">
    <span className="text-sm text-zinc-300 mr-2">
      已选 <strong className="text-white">{selectedIds.size}</strong> 项
    </span>
    <div className="w-px h-5 bg-zinc-700" />
    <button onClick={handleBatchMove}><Move size={14} /> 移动到</button>
    <button onClick={handleBatchCopy}><Copy size={14} /> 复制到</button>
    <button onClick={handleBatchDownload}><Download size={14} /> 下载</button>
    <button onClick={handleBatchShare}><Share2 size={14} /> 分享</button>
    <div className="w-px h-5 bg-zinc-700" />
    <button onClick={handleBatchTrash} className="text-red-400"><Trash2 size={14} /> 删除</button>
    <button onClick={() => setSelectedIds(new Set())}><X size={14} /></button>
  </div>
)}
```
- 涉及文件: `ResourcesView.tsx`, `ResourceCard.tsx`, `FolderCard.tsx`
- 预估: 5h

---

## F. 文件详情页（6/10）

| 功能项 | MediaHub | 分秒帧 | 差距描述 |
|--------|----------|--------|---------|
| 整体布局 | 🟡 左侧预览+右侧信息 | ✅ 全屏预览+右侧信息+底部播放控制 | 分秒帧更沉浸 |
| 视频预览 | 🟡 原生HTML5播放器 | ✅ 自定义播放器(倍速/帧步进/设置) | 功能弱 |
| 文件属性 | 🟡 基础属性 | ✅ 含创建人/更新人/更新时间 | 缺字段 |
| 标签 | ✅ | ✅ | 对等 |
| 备注/描述 | ✅ | ✅ | 对等 |
| 版本管理 | 🟡 显示"v1"无版本列表 | ✅ 版本选择器 | UI不完善 |
| 批注 | 🔴 | 🟡 有批注按钮 | 缺少 |
| 来源信息 | ✅ "来源: 网页下载" | 🔴 | MediaHub独有 |
| 分享按钮 | 🔴 无醒目入口 | ✅ 紫色"分享"按钮 | 缺少 |
| 文件描述编辑 | 🔴 只读元数据 | ✅ 可编辑描述区域 | 缺少 |
| 视频技术信息 | 🔴 仅size/type/duration | ✅ 分辨率/帧率/编码/码率/声道 | 缺少 |

### 改进项

#### 【P1】视频播放器升级
- **问题**: 原生HTML5播放器，功能简陋
- **分秒帧做法**: 自定义播放器，支持倍速/帧前进后退/设置
- **方向**: 升级为自定义播放器或集成开源播放器组件
- 涉及文件: `ResourceDetail.tsx`
- 预估: 需独立评估（复杂度高）

#### 【P1】详情页增加醒目的分享按钮
- **问题**: 仅在...菜单中，不够醒目
- **代码方案**: 增加紫色"分享"按钮
- 涉及文件: `ResourceDetail.tsx`
- 预估: 0.5h

#### 【P2】版本管理增加版本列表/切换UI
- **方向**: 版本选择器下拉，显示各版本信息

#### 【P2】增加批注功能（视频时间轴批注）
- **方向**: 关联播放时间点的评论/标注

#### 【P2】文件描述编辑 + 技术信息
- **方向**: `resources` 表增加 `description TEXT` 和 `metadata JSONB`
- 涉及文件: `ResourceDetail.tsx`, `ResourceInfoPanel.tsx`, `resourceService.ts`
- 预估: 1.5h（描述）+ 后端 ffprobe 集成（技术信息）

#### 【P3】文件属性增加"更新人"和"更新时间"字段

---

## G. 文件夹 & 面包屑导航（7/10）

| 功能项 | MediaHub | 分秒帧 | 差距描述 |
|--------|----------|--------|---------|
| 创建文件夹 | ✅ | ✅ | 对等 |
| 文件夹卡片设计 | 🔴 纯空图标 | ✅ 内容预览缩略图 | 信息量差距大 |
| 面包屑导航 | 🟡 仅"我的资源"文字 | ✅ 层级路径可点击 | 缺真正面包屑 |
| 文件夹右键菜单 | 🔴 仅4项 | ✅ 9项 | 见D节 |

### 改进项

#### 【P1】实现面包屑导航
- **问题**: 仅显示"我的资源"文字，非面包屑链
- **分秒帧做法**: 层级路径显示（我的资源库 > 子文件夹 > ...），可点击返回
- **代码方案**: 在工具栏上方增加面包屑组件
- 涉及文件: `ResourcesView.tsx`
- 预估: 1.5h

---

## H. 拖拽功能

> ⚠️ 拖拽功能无法通过自动化工具完整测试，建议手动测试

| 功能项 | MediaHub | 分秒帧 | 差距描述 |
|--------|----------|--------|---------|
| 拖文件到文件夹 | 🔴 | ✅ | 见D节实现方案 |
| 拖文件到侧边栏 | 🔴 | ✅ | 见A节侧边栏方案 |
| 从桌面拖入上传 | ✅ | ✅ | 对等 |

---

## I. 键盘快捷键（0/10）

| 快捷键 | MediaHub | 分秒帧 | 说明 |
|--------|----------|--------|------|
| Ctrl/Cmd+A | 🔴 | ✅ | 全选 |
| Ctrl/Cmd+C/X/V | 🔴 | ✅ | 复制/剪切/粘贴 |
| Delete/Backspace | 🔴 | ✅ | 删除 |
| F2 | 🔴 | ✅ | 重命名 |
| Enter | 🔴 | ✅ | 打开 |
| Ctrl+Shift+N | 🔴 | ✅ | 新建文件夹 |
| ←→ | 🔴 | 🟡 | 详情页切换文件 |
| Escape | ✅ | ✅ | 关闭/取消 |
| ⌘K | ✅ | 🔴 | MediaHub独有搜索 |

> 完整实现方案见 D 节 useFileKeyboard.ts

---

## J. 其他

| 功能项 | MediaHub | 分秒帧 | 差距描述 |
|--------|----------|--------|---------|
| 错误提示 | 🔴 英文"Failed to load resource" | 🟡 | 需国际化 |
| 加载状态/骨架屏 | 🟡 无骨架屏 | 🟡 | 需增加 |
| Toast通知 | 🔴 操作后无反馈 | ✅ | 需实现 |
| 撤销操作 | 🔴 | 🟡 | 需实现 |
| 上传冲突处理 | 🔴 同名直接覆盖 | ✅ | 需提示 |

### 改进项

#### 【P1】错误提示国际化
- **代码方案**: "Failed to load resource" → 中文提示
- 预估: 0.5h

#### 【P1】Toast 通知系统 + 撤销操作
- **问题**: 所有文件操作后无任何反馈
- **代码方案**:
```tsx
showToast({
  message: `已移至回收站: ${filename}`,
  action: { label: '撤销', onClick: () => restoreResource(resourceId) },
  duration: 5000,
});
```
- 涉及文件: `Toast.tsx`, `ResourcesView.tsx`
- 预估: 2h

#### 【P2】骨架屏/加载动画
- **方向**: 增加内容加载时的骨架屏

#### 【P2】上传冲突处理
- **方向**: 同名文件弹出对话框: 覆盖(新版本) / 重命名(加后缀) / 跳过
- 预估: 2h

#### 【P2】双击重命名入口
```tsx
<p onDoubleClick={(e) => { e.stopPropagation(); onStartRename?.(); }}
   className="text-sm text-zinc-200 truncate cursor-default">
  {filename}
</p>
```
- 涉及文件: `ResourceCard.tsx`, `FolderCard.tsx`
- 预估: 1h

---

## 总结: 修改清单（按优先级）

### P0 — 必须立即修复（没有这些不算文件管理器）

| # | 改进项 | 涉及文件 |
|---|--------|---------|
| 1 | 复制到/移动到 + FolderPickerModal | `resourceService.ts`, 新建 `FolderPickerModal.tsx`, `ResourcesView.tsx` |
| 2 | 批量选择(Ctrl+Click/Shift+Click) + 批量操作浮动工具栏 | `ResourcesView.tsx`, `ResourceCard.tsx`, `FolderCard.tsx` |
| 3 | 键盘快捷键系统 | 新建 `hooks/useFileKeyboard.ts`, `ResourcesView.tsx` |
| 4 | 拖拽移动文件到文件夹/侧边栏 | `ResourceCard.tsx`, `FolderCard.tsx`, `ResourcesView.tsx` |
| 5 | 文件缩略图升级(视频帧+Office图标) | `ResourceCard.tsx` |

### P1 — 高优先级改进

| # | 改进项 | 涉及文件 |
|---|--------|---------|
| 6 | 文件卡片⋮三点菜单 + 悬浮操作层 | `ResourceCard.tsx`, `FolderCard.tsx` |
| 7 | 空白区域右键菜单 | `ResourcesView.tsx` |
| 8 | 文件夹右键菜单补全 | `ResourcesView.tsx` |
| 9 | 文件夹卡片内容预览 | `FolderCard.tsx`, `resourceService.ts` |
| 10 | 面包屑导航 | `ResourcesView.tsx` |
| 11 | 侧边栏文件夹树递归展开 | `ResourcesView.tsx` |
| 12 | 筛选功能 | `ResourcesView.tsx` |
| 13 | "新建"下拉菜单 + 工具栏按钮颜色 | `ResourcesView.tsx` |
| 14 | 资源库内搜索框 | `ResourcesView.tsx` |
| 15 | Toast通知系统 + 撤销 | `Toast.tsx`, `ResourcesView.tsx` |
| 16 | 详情页分享按钮 | `ResourceDetail.tsx` |
| 17 | 错误提示国际化 | 多处 |

### P2 — 中优先级

| # | 改进项 |
|---|--------|
| 18 | 侧边栏标签导航 |
| 19 | 时间戳中文格式 |
| 20 | 列表视图增强(列头排序) |
| 21 | 上传按钮下拉(文件/文件夹) |
| 22 | 版本管理UI |
| 23 | 批注功能 |
| 24 | 文件描述编辑 + 技术信息 |
| 25 | 骨架屏/加载动画 |
| 26 | 上传冲突处理 |
| 27 | 双击重命名 |
| 28 | 排序增强(文件类型字段) |

### P3 — 低优先级

| # | 改进项 |
|---|--------|
| 29 | 打包下载 |
| 30 | 导出文件夹目录 |
| 31 | 收集功能 |
| 32 | 侧边栏极窄icon栏布局 |
| 33 | 星标/收藏夹快速访问 |

---

## 完整修改文件清单

| 文件 | 改动类型 | 改动内容 |
|------|---------|---------|
| `frontend/services/resourceService.ts` | **新增函数** | `moveResource`, `moveResources`, `copyResource`, `moveFolder`, `batchTrash` |
| `frontend/hooks/useFileKeyboard.ts` | **新建** | 键盘快捷键处理 hook |
| `frontend/components/FolderPickerModal.tsx` | **新建** | 文件夹选择对话框 |
| `frontend/components/ResourcesView.tsx` | **大改** | 批量选择、拖拽、粘贴、筛选器、搜索、浮动工具栏、右键菜单、面包屑 |
| `frontend/components/ResourceCard.tsx` | **中改** | 缩略图升级、悬浮操作层、勾选框、拖拽源、双击重命名 |
| `frontend/components/FolderCard.tsx` | **中改** | 内容预览、拖拽目标、勾选框、双击重命名 |
| `frontend/components/ResourceDetail.tsx` | **小改** | 分享按钮、文件描述编辑、技术信息面板 |
| `frontend/components/ResourceInfoPanel.tsx` | **小改** | 增加描述、封面图 |
| `frontend/components/Toast.tsx` | **改进** | 支持撤销操作 |

---

## Sprint 实施计划

### Sprint 1: 核心文件操作（~16h）
| 任务 | 预估 | 涉及文件 |
|------|------|---------|
| `moveResource/copyResource/moveFolder` 服务函数 | 2h | resourceService.ts |
| `FolderPickerModal` 文件夹选择对话框 | 4h | FolderPickerModal.tsx |
| 右键菜单接入 FolderPickerModal | 2h | ResourcesView.tsx |
| 批量选择(Ctrl+Click, Shift+Click) | 3h | ResourcesView.tsx, ResourceCard.tsx, FolderCard.tsx |
| 批量操作浮动工具栏 | 2h | ResourcesView.tsx |
| 键盘快捷键 hook | 3h | useFileKeyboard.ts, ResourcesView.tsx |

### Sprint 2: 拖拽 & 卡片升级（~12h）
| 任务 | 预估 | 涉及文件 |
|------|------|---------|
| 文件卡片拖拽源 | 2h | ResourceCard.tsx |
| 文件夹卡片/侧边栏拖拽目标 | 3h | FolderCard.tsx, ResourcesView.tsx |
| 文件缩略图升级(视频帧+Office图标) | 3h | ResourceCard.tsx |
| 文件夹卡片内容预览 | 2h | FolderCard.tsx, resourceService.ts |
| 卡片悬浮操作层(⋮+勾选框) | 2h | ResourceCard.tsx, FolderCard.tsx |

### Sprint 3: 侧边栏 & 工具栏（~10h）
| 任务 | 预估 | 涉及文件 |
|------|------|---------|
| 侧边栏文件夹树递归展开 | 2h | ResourcesView.tsx |
| 筛选器(文件类型) | 2h | ResourcesView.tsx |
| 资源库内搜索 | 1.5h | ResourcesView.tsx |
| 工具栏按钮颜色+上传/新建下拉 | 1.5h | ResourcesView.tsx |
| 列表视图列头排序 | 1h | ResourcesView.tsx |
| 面包屑导航 | 1.5h | ResourcesView.tsx |
| 空白区域右键 + 文件夹右键补全 | 1.5h | ResourcesView.tsx |

### Sprint 4: 细节打磨（~8h）
| 任务 | 预估 | 涉及文件 |
|------|------|---------|
| Toast通知系统 + 撤销 | 2h | Toast.tsx, ResourcesView.tsx |
| 双击重命名 | 1h | ResourceCard.tsx, FolderCard.tsx |
| 时间戳本地化 | 0.5h | 多处 |
| 空状态优化 | 1h | ResourcesView.tsx |
| 文件夹上传 + 上传冲突处理 | 2h | ResourcesView.tsx |
| 文件详情页(描述编辑+分享按钮) | 1.5h | ResourceDetail.tsx, resourceService.ts |

**总计: ~46h，4 个 Sprint**

---

## 数据对比速览

| 维度 | MediaHub | 分秒帧 |
|------|----------|--------|
| 文件右键菜单项数 | 7项(2项灰色) | 9项(全可用) |
| 文件夹右键菜单项数 | 4项 | 9项 |
| 排序选项数 | 6项(耦合) | 4字段×2方向=8组合 |
| 新建选项 | 1项(仅文件夹) | 7项 |
| 批量操作 | 无 | 完整(全选/取消/工具栏) |
| 键盘快捷键 | 仅ESC+⌘K | 全套文件管理快捷键 |
| 详情页功能 | 基础预览+属性+标签+备注 | 增强预览+版本+批注+属性+标签+描述 |
