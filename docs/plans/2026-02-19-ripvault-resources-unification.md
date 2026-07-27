# RipVault + Resources 视图统一 Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** 将 RipVault（我的下载）合并进 Resources（资源库），通过 source_type 筛选标签区分来源，消除重复代码，统一删除/数据管理逻辑。

**Architecture:** Resources 成为唯一的资源管理视图。后端 list 端点新增 `source_type` 过滤参数，并可选 join `parsed_media` 返回平台元数据。前端删除 RipVaultView 组件，在 Resources 内添加 [All / Downloads / Uploads] 筛选标签。删除统一用 `resource_id`。

**Tech Stack:** FastAPI + Supabase (PostgreSQL) / React 19 + TypeScript + Vite

---

## Task 1: 后端 — resource list 端点支持 source_type 过滤

**Files:**
- Modify: `backend/app/repositories/resources_repository.py:202-230`
- Modify: `backend/app/api/resources_router.py:214-232`

**Step 1: 修改 repository — get_resource_items 添加 source_type 参数**

在 `backend/app/repositories/resources_repository.py` 的 `get_resource_items` 方法中：

```python
async def get_resource_items(
    self,
    scope_type: str,
    scope_id: str,
    folder_id: Optional[str] = None,
    include_trashed: bool = False,
    source_type: Optional[str] = None,        # 新增
) -> List[Dict[str, Any]]:
    try:
        client = await self._get_client()
        query = (
            client.table(self.TABLE_ITEMS)
            .select("*, resource:resources!inner(*)")
            .eq("scope_type", scope_type)
            .eq("scope_id", scope_id)
        )
        if folder_id:
            query = query.eq("folder_id", folder_id)
        else:
            query = query.is_("folder_id", "null")

        if not include_trashed:
            query = query.eq("resource.is_trashed", False)

        # 新增: source_type 过滤
        if source_type:
            query = query.eq("resource.source_type", source_type)

        query = query.order("created_at", desc=True)
        result = await query.execute()
        return result.data or []
    except Exception as e:
        logger.error(f"Failed to get resource items: {e}")
        return []
```

**Step 2: 修改 router — list 端点添加 source_type query 参数**

在 `backend/app/api/resources_router.py` 的 `list_resources` 端点中：

```python
@router.get("")
async def list_resources(
    auth: AuthDep,
    scope_type: str = Query(..., pattern="^(personal|team)$"),
    scope_id: str = Query(...),
    folder_id: Optional[str] = Query(None),
    source_type: Optional[str] = Query(None, pattern="^(web|upload)$"),  # 新增
):
    """List resources in a scope, optionally filtered by folder and source type."""
    try:
        repo = ResourcesRepository()
        items = await repo.get_resource_items(
            scope_type=scope_type,
            scope_id=scope_id,
            folder_id=folder_id,
            source_type=source_type,    # 新增
        )
        return {"success": True, "data": items}
    except Exception as e:
        logger.error(f"Failed to list resources: {e}")
        raise HTTPException(status_code=500, detail="Failed to list resources")
```

**Step 3: 验证**

Run: `cd /Volumes/program/project-code/repos/mediahub/.worktrees/feature-Points-capacity-payment-system/backend && uv run python -c "from app.api.resources_router import router; print('Router OK')"`
Expected: `Router OK`

**Step 4: Commit**

```bash
git add backend/app/repositories/resources_repository.py backend/app/api/resources_router.py
git commit -m "feat(api): add source_type filter to resource list endpoint"
```

---

## Task 2: 后端 — resource 响应包含 parsed_media 元数据

**Files:**
- Modify: `backend/app/repositories/resources_repository.py:202-230`

**目的:** 当 resource 有 `media_id` 时，响应中包含 `parsed_media` 的平台元数据（author, likes, comments 等），供前端 InfoPanel 展示。

**Step 1: 修改 get_resource_items select 语句**

在 `resources_repository.py` 的 `get_resource_items` 中，扩展 select 以 join `parsed_media`：

```python
async def get_resource_items(
    self,
    scope_type: str,
    scope_id: str,
    folder_id: Optional[str] = None,
    include_trashed: bool = False,
    source_type: Optional[str] = None,
    include_media: bool = False,          # 新增
) -> List[Dict[str, Any]]:
    try:
        client = await self._get_client()

        # 根据是否需要 media 数据决定 select
        select_str = "*, resource:resources!inner(*)"
        if include_media:
            select_str = "*, resource:resources!inner(*, media:parsed_media(title, description, author_nickname, like_count, comment_count, share_count, favorite_count, source_platform, platform_id, published_at, video_tag))"

        query = (
            client.table(self.TABLE_ITEMS)
            .select(select_str)
            .eq("scope_type", scope_type)
            .eq("scope_id", scope_id)
        )
        # ... 其余过滤逻辑不变
```

**Step 2: 修改 router 传递 include_media 参数**

```python
@router.get("")
async def list_resources(
    auth: AuthDep,
    scope_type: str = Query(..., pattern="^(personal|team)$"),
    scope_id: str = Query(...),
    folder_id: Optional[str] = Query(None),
    source_type: Optional[str] = Query(None, pattern="^(web|upload)$"),
    include_media: bool = Query(False),    # 新增
):
    """List resources in a scope, optionally filtered by folder and source type."""
    try:
        repo = ResourcesRepository()
        items = await repo.get_resource_items(
            scope_type=scope_type,
            scope_id=scope_id,
            folder_id=folder_id,
            source_type=source_type,
            include_media=include_media,    # 新增
        )
        return {"success": True, "data": items}
```

**Step 3: 验证**

Run: `cd /Volumes/program/project-code/repos/mediahub/.worktrees/feature-Points-capacity-payment-system/backend && uv run python -c "from app.api.resources_router import router; print('Router OK')"`
Expected: `Router OK`

**Step 4: Commit**

```bash
git add backend/app/repositories/resources_repository.py backend/app/api/resources_router.py
git commit -m "feat(api): include parsed_media metadata in resource response"
```

---

## Task 3: 前端类型 — 扩展 ResourceItem 支持 media 元数据

**Files:**
- Modify: `frontend/types.ts`

**Step 1: 在 types.ts 中添加 MediaMetadata 类型和扩展 Resource 接口**

在 `Resource` 接口附近添加：

```typescript
/** Platform metadata from parsed_media (for source_type='web' resources) */
export interface MediaMetadata {
  title: string | null;
  description: string | null;
  author_nickname: string | null;
  like_count: number;
  comment_count: number;
  share_count: number;
  favorite_count: number;
  source_platform: string | null;
  platform_id: string | null;
  published_at: string | null;
  video_tag: string | null;
}
```

在 `Resource` 接口中添加可选字段：

```typescript
media: MediaMetadata | null;  // Populated when include_media=true
```

**Step 2: 验证**

Run: `cd /Volumes/program/project-code/repos/mediahub/.worktrees/feature-Points-capacity-payment-system/frontend && npx tsc --noEmit 2>&1 | head -20`
Expected: No new errors (existing errors OK)

**Step 3: Commit**

```bash
git add frontend/types.ts
git commit -m "feat(types): add MediaMetadata type for parsed_media join"
```

---

## Task 4: 前端服务 — resourceService 支持 source_type 和 include_media

**Files:**
- Modify: `frontend/services/resourceService.ts`

**Step 1: 修改 fetchResources 函数添加可选参数**

找到 `fetchResources` 或 `listResources` 函数（获取资源列表的那个），添加 `source_type` 和 `include_media` 参数：

```typescript
export async function fetchResources(
  scopeType: string,
  scopeId: string,
  folderId?: string | null,
  options?: {
    source_type?: 'web' | 'upload';
    include_media?: boolean;
  },
): Promise<ResourceItem[]> {
  const apiUrl = getApiUrl();
  const params = new URLSearchParams({
    scope_type: scopeType,
    scope_id: scopeId,
  });
  if (folderId) params.set('folder_id', folderId);
  if (options?.source_type) params.set('source_type', options.source_type);
  if (options?.include_media) params.set('include_media', 'true');

  const response = await fetch(`${apiUrl}/api/v1/resources?${params}`, {
    headers: await getAuthHeaders(),
  });
  if (!response.ok) throw new Error('Failed to fetch resources');
  const json = await response.json();
  return json.data;
}
```

**Step 2: 验证**

Run: `cd /Volumes/program/project-code/repos/mediahub/.worktrees/feature-Points-capacity-payment-system/frontend && npx tsc --noEmit 2>&1 | head -20`

**Step 3: Commit**

```bash
git add frontend/services/resourceService.ts
git commit -m "feat(service): add source_type and include_media params to fetchResources"
```

---

## Task 5: 前端 — ResourcesView 用 resourceService 获取下载资源

**Files:**
- Modify: `frontend/components/ResourcesView.tsx`

**目的:** 将 `downloadedResources` 的数据源从 RipVaultView 的 useLibrary/dataService 切换到 resourceService 的 `fetchResources(scopeType, scopeId, null, { source_type: 'web', include_media: true })`。

**Step 1: 添加 fetchDownloadedResources 函数**

在 ResourcesView 中（靠近其他 fetch 函数），添加：

```typescript
const fetchDownloadedResources = useCallback(async () => {
  if (!user?.id) return;
  try {
    const scopeType = selectedTeamId ? 'team' : 'personal';
    const scopeId = selectedTeamId || user.id;
    const items = await fetchResources(scopeType, scopeId, null, {
      source_type: 'web',
      include_media: true,
    });
    setDownloadedResources(items);
  } catch (err) {
    console.error('Failed to fetch downloaded resources:', err);
  }
}, [user?.id, selectedTeamId]);
```

**Step 2: 在 useEffect 中调用**

当 `isDownloadsView` 为 true 且切换到 downloads 时触发加载：

```typescript
useEffect(() => {
  if (isDownloadsView) {
    fetchDownloadedResources();
  }
}, [isDownloadsView, fetchDownloadedResources]);
```

**Step 3: 验证**

Run: `cd /Volumes/program/project-code/repos/mediahub/.worktrees/feature-Points-capacity-payment-system/frontend && npm run build`
Expected: Build success

**Step 4: Commit**

```bash
git add frontend/components/ResourcesView.tsx
git commit -m "feat(resources): fetch downloads via resourceService instead of dataService"
```

---

## Task 6: 前端 — 替换 Downloads section 为 source filter tabs

**Files:**
- Modify: `frontend/components/ResourcesView.tsx`

**目的:** 移除 `isDownloadsView` 分支中对 `<RipVaultView />` 的渲染，改为在 Resources 统一视图中展示所有内容，顶部添加 [All / Downloads / Uploads] 筛选标签。

**Step 1: 添加 sourceFilter 状态**

```typescript
const [sourceFilter, setSourceFilter] = useState<'all' | 'web' | 'upload'>('all');
```

**Step 2: 添加 Source Filter Tabs UI**

在 Resources 视图的工具栏区域（breadcrumb 下方），添加筛选标签：

```tsx
{/* Source Filter Tabs */}
<div className="flex gap-1 px-4 py-2 border-b border-white/5">
  {(['all', 'web', 'upload'] as const).map((filter) => (
    <button
      key={filter}
      onClick={() => setSourceFilter(filter)}
      className={`px-3 py-1 text-xs rounded-full transition-colors ${
        sourceFilter === filter
          ? 'bg-blue-500/20 text-blue-400'
          : 'text-gray-400 hover:text-gray-300 hover:bg-white/5'
      }`}
    >
      {filter === 'all' ? t('resources.filterAll') :
       filter === 'web' ? t('resources.filterDownloads') :
       t('resources.filterUploads')}
    </button>
  ))}
</div>
```

**Step 3: 修改数据获取逻辑**

在现有的 resources fetch 逻辑中，根据 `sourceFilter` 传递 `source_type` 参数：

```typescript
// 在 loadResources 或类似函数中
const items = await fetchResources(scopeType, scopeId, currentFolderId, {
  source_type: sourceFilter === 'all' ? undefined : sourceFilter,
  include_media: sourceFilter === 'web' || sourceFilter === 'all',
});
```

**Step 4: 移除 RipVaultView 条件渲染**

在 ResourcesView.tsx 的渲染逻辑中（line ~1960）：
- 移除 `{isDownloadsView ? (<RipVaultView />) : (` 分支
- 让 Downloads 和 Resources 走同一个渲染路径

**Step 5: 更新 sidebar Downloads 按钮**

将 Downloads 按钮（line ~1849-1855）改为设置 sourceFilter 而非导航到 `/resources/downloads`：
- 点击 Downloads → `setSourceFilter('web')` + 导航到 `/resources`（无 section）
- 或者保留 URL 路由，但渲染走统一路径

**Step 6: 验证**

Run: `cd /Volumes/program/project-code/repos/mediahub/.worktrees/feature-Points-capacity-payment-system/frontend && npm run build`

**Step 7: Commit**

```bash
git add frontend/components/ResourcesView.tsx
git commit -m "feat(resources): replace RipVaultView with source filter tabs"
```

---

## Task 7: 前端 — ResourceInfoPanel 显示平台元数据

**Files:**
- Modify: `frontend/components/ResourceInfoPanel.tsx`

**目的:** 当资源有 `media` 数据（source_type='web'）时，在 Properties 区域显示平台元数据（author, likes, comments, shares, published_at）。

**Step 1: 在 Properties section 中添加 media 数据展示**

在 ResourceInfoPanel 的 Properties 列表中，当 `resource.media` 存在时，展示额外字段：

```tsx
{/* Platform metadata (for downloaded resources) */}
{resource.media && (
  <>
    {resource.media.author_nickname && (
      <div className="flex justify-between">
        <span className="text-gray-400">{t('resources.infoPanel.author')}</span>
        <span className="text-gray-200">{resource.media.author_nickname}</span>
      </div>
    )}
    {resource.media.source_platform && (
      <div className="flex justify-between">
        <span className="text-gray-400">{t('resources.infoPanel.platform')}</span>
        <span className="text-gray-200 capitalize">{resource.media.source_platform}</span>
      </div>
    )}
    {resource.media.published_at && (
      <div className="flex justify-between">
        <span className="text-gray-400">{t('resources.infoPanel.published')}</span>
        <span className="text-gray-200">{new Date(resource.media.published_at).toLocaleDateString()}</span>
      </div>
    )}
    {/* Engagement Stats */}
    <div className="grid grid-cols-2 gap-2 pt-2">
      {resource.media.like_count > 0 && (
        <div className="flex items-center gap-1 text-xs text-gray-400">
          <Heart size={12} /> {formatNumber(resource.media.like_count)}
        </div>
      )}
      {resource.media.comment_count > 0 && (
        <div className="flex items-center gap-1 text-xs text-gray-400">
          <MessageCircle size={12} /> {formatNumber(resource.media.comment_count)}
        </div>
      )}
      {resource.media.share_count > 0 && (
        <div className="flex items-center gap-1 text-xs text-gray-400">
          <Share2 size={12} /> {formatNumber(resource.media.share_count)}
        </div>
      )}
      {resource.media.favorite_count > 0 && (
        <div className="flex items-center gap-1 text-xs text-gray-400">
          <Bookmark size={12} /> {formatNumber(resource.media.favorite_count)}
        </div>
      )}
    </div>
  </>
)}
```

**Step 2: 添加 formatNumber 工具函数**

```typescript
function formatNumber(num: number): string {
  if (num >= 10000) return (num / 10000).toFixed(1) + 'w';
  if (num >= 1000) return (num / 1000).toFixed(1) + 'k';
  return String(num);
}
```

**Step 3: 验证**

Run: `cd /Volumes/program/project-code/repos/mediahub/.worktrees/feature-Points-capacity-payment-system/frontend && npm run build`

**Step 4: Commit**

```bash
git add frontend/components/ResourceInfoPanel.tsx
git commit -m "feat(info-panel): show platform metadata for downloaded resources"
```

---

## Task 8: 前端 — 统一删除逻辑（resource_id only）

**Files:**
- Modify: `frontend/components/ResourcesView.tsx`

**目的:** Downloads section 的删除不再调用 `trashResourceByPlatformId(platformId)`，而是统一调用 `trashResource(resourceId)`，因为所有下载的视频已有对应的 resource 记录。

**Step 1: 确认 currentItems 使用 resource_id**

ResourcesView 中的 `handleTrashResource` 和 `handleBatchTrashResources` 已经使用 `resource_id`。只需确保 Downloads 筛选下的 items 走同一个 handler（不再走 RipVaultView 的 `handleBatchDelete`）。

由于 Task 6 已经移除了 RipVaultView 的渲染，Downloads 数据现在走 `currentItems` → 使用统一的 Resources delete handlers，这一步应该已自动生效。

**Step 2: 验证删除流程**

1. 启动后端和前端
2. 在 Resources 视图中切换到 Downloads 筛选
3. 选中一个下载的资源
4. 点击删除 → 确认走 `trashResource(resourceId)` 路径
5. 资源出现在回收站中

**Step 3: Commit**

```bash
git add frontend/components/ResourcesView.tsx
git commit -m "feat(resources): unify delete flow to use resource_id for all sources"
```

---

## Task 9: 清理 — 移除 RipVaultView 及相关依赖

**Files:**
- Delete: `frontend/components/RipVaultView.tsx`
- Modify: `frontend/components/ResourcesView.tsx` (移除 import)
- Modify: `frontend/hooks/useLibrary.ts` (评估是否其他地方使用)

**Step 1: 检查 RipVaultView 的引用**

搜索整个前端 codebase 确认只有 ResourcesView 引用了 RipVaultView：

Run: `grep -r "RipVaultView" frontend/ --include="*.tsx" --include="*.ts"`

**Step 2: 检查 useLibrary 的引用**

Run: `grep -r "useLibrary" frontend/ --include="*.tsx" --include="*.ts"`

如果 useLibrary 只被 RipVaultView 使用，可以一并删除。
如果其他组件也用到（如 Dashboard），保留 hook 但删除 RipVaultView。

**Step 3: 删除 RipVaultView**

```bash
rm frontend/components/RipVaultView.tsx
```

**Step 4: 移除 ResourcesView 中的 import**

删除 `import { RipVaultView } from './RipVaultView';`（line 39）

**Step 5: 评估 dataService 中 RipVault 相关函数**

以下函数如果只被 useLibrary/RipVaultView 使用，可标记为 deprecated 或删除：
- `fetchLibraryPaginated`
- `fetchLibrary`
- `fetchLibraryCount`

暂不删除（可能 Dashboard 等还在用），但在函数上方添加 `// TODO: migrate callers to resourceService` 注释。

**Step 6: 验证**

Run: `cd /Volumes/program/project-code/repos/mediahub/.worktrees/feature-Points-capacity-payment-system/frontend && npm run build`

**Step 7: Commit**

```bash
git add -A
git commit -m "refactor: remove RipVaultView, use unified Resources view"
```

---

## Task 10: 清理 — 移除 by-platform-id/trash 端点

**Files:**
- Modify: `backend/app/api/resources_router.py:557-601`
- Modify: `frontend/services/resourceService.ts` (移除 trashResourceByPlatformId)

**Step 1: 检查 trashResourceByPlatformId 前端引用**

Run: `grep -r "trashResourceByPlatformId\|by-platform-id" frontend/ --include="*.ts" --include="*.tsx"`

如果没有引用（Task 8 已统一到 resource_id 删除），可以安全移除。

**Step 2: 移除前端 service 函数**

在 `resourceService.ts` 中删除 `trashResourceByPlatformId` 函数。

**Step 3: 移除后端端点**

在 `resources_router.py` 中删除 `trash_resource_by_platform_id` 端点（line 557-601）。

**Step 4: 验证**

Run: `cd /Volumes/program/project-code/repos/mediahub/.worktrees/feature-Points-capacity-payment-system/frontend && npm run build`
Run: `cd /Volumes/program/project-code/repos/mediahub/.worktrees/feature-Points-capacity-payment-system/backend && uv run python -c "from app.api.resources_router import router; print('Router OK')"`

**Step 5: Commit**

```bash
git add backend/app/api/resources_router.py frontend/services/resourceService.ts
git commit -m "refactor: remove deprecated by-platform-id/trash endpoint"
```

---

## Task 11: i18n — 添加筛选标签翻译

**Files:**
- Modify: `frontend/public/locales/en.json`
- Modify: `frontend/public/locales/zh.json`

**Step 1: 添加英文翻译**

在 `resources` 对象中追加：

```json
"filterAll": "All",
"filterDownloads": "Downloads",
"filterUploads": "Uploads"
```

在 `resources.infoPanel` 对象中追加：

```json
"author": "Author",
"platform": "Platform",
"published": "Published",
"likes": "Likes",
"comments": "Comments",
"shares": "Shares",
"favorites": "Favorites"
```

**Step 2: 添加中文翻译**

```json
"filterAll": "全部",
"filterDownloads": "下载",
"filterUploads": "上传"
```

```json
"author": "作者",
"platform": "平台",
"published": "发布时间",
"likes": "点赞",
"comments": "评论",
"shares": "分享",
"favorites": "收藏"
```

**Step 3: 验证**

Run: `cd /Volumes/program/project-code/repos/mediahub/.worktrees/feature-Points-capacity-payment-system/frontend && npm run build`

**Step 4: Commit**

```bash
git add frontend/public/locales/en.json frontend/public/locales/zh.json
git commit -m "feat(i18n): add source filter and platform metadata translations"
```

---

## 验证清单

完成所有 Task 后的端到端验证：

1. **前端构建通过**: `cd frontend && npm run build` → 无错误
2. **后端启动正常**: `uv run uvicorn app.main:app --reload --port 8081` → 无报错
3. **Resources 视图**:
   - [All] 标签: 显示所有资源（上传 + 下载）
   - [Downloads] 标签: 只显示 source_type=web 的资源
   - [Uploads] 标签: 只显示 source_type=upload 的资源
4. **InfoPanel 平台数据**: 选中下载资源 → 显示 author, likes, comments, platform
5. **统一删除**: 在 Downloads 筛选下删除资源 → 走 resource_id 删除 → 出现在回收站
6. **回收站**: 回收站中同时显示上传和下载的已删除资源
7. **RipVaultView 已移除**: `import RipVaultView` 不再存在
8. **Sidebar**: Downloads 按钮行为正确（设置筛选而非渲染独立视图）
