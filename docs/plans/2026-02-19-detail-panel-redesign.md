# Detail Panel Redesign Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** 重新设计 Downloads 详情页右侧面板（VideoDetailPanel Info tab），恢复 MediaCard 风格 AI 创作功能并新增 Rating + Notes；同时在外层 RipVaultView 预览面板增加 Rating 和 Notes。

**Architecture:** VideoDetailPanel Info tab 从当前精简的元数据视图，重写为 AI 文字创作工作台（Engagement → Tags → Rating+AI → AI Buttons → Notes → Description → AI Results → Download/Refetch）。RipVaultView 面板在 Tags 和 AI Status 之间插入 Rating + Notes。数据层在 `parsed_media` 表新增 `rating` 和 `notes` 列。

**Tech Stack:** React 19, TypeScript, TailwindCSS, Supabase PostgreSQL

---

## Task 1: DB Migration — 添加 rating 和 notes 到 parsed_media

**Files:**
- Create: `supabase/migrations/080_parsed_media_rating_notes.sql`

**Step 1: 创建 migration 文件**

```sql
-- 080_parsed_media_rating_notes.sql
-- Add user-editable rating and notes to parsed_media for Downloads panels.

ALTER TABLE parsed_media
  ADD COLUMN IF NOT EXISTS rating SMALLINT CHECK (rating >= 0 AND rating <= 5) DEFAULT 0,
  ADD COLUMN IF NOT EXISTS notes TEXT DEFAULT '';
```

**Step 2: 在本地 Supabase 执行**

Run:
```bash
PGPASSWORD=postgres psql -h 127.0.0.1 -p 54322 -U postgres -d postgres -f supabase/migrations/080_parsed_media_rating_notes.sql
```
Expected: `ALTER TABLE` 成功

**Step 3: 验证**

Run:
```bash
PGPASSWORD=postgres psql -h 127.0.0.1 -p 54322 -U postgres -d postgres -c "SELECT column_name, data_type FROM information_schema.columns WHERE table_name='parsed_media' AND column_name IN ('rating','notes');"
```
Expected: 两列（rating smallint, notes text）

**Step 4: Commit**

```bash
git add supabase/migrations/080_parsed_media_rating_notes.sql
git commit -m "feat(db): add rating and notes columns to parsed_media"
```

---

## Task 2: 前端类型 — Video 接口添加 rating

**Files:**
- Modify: `frontend/types.ts:68-70`

**Step 1: 添加 rating 字段**

在 `types.ts` 的 Video interface，`notes` 字段之后添加 `rating`:

```typescript
  // User Data
  notes?: string;
  rating?: number; // 0-5 star rating
  tags?: string[];
```

**Step 2: 验证构建**

Run: `cd frontend && npm run build`
Expected: 成功（rating 为 optional，不影响现有代码）

**Step 3: Commit**

```bash
git add frontend/types.ts
git commit -m "feat(types): add rating field to Video interface"
```

---

## Task 3: 重写 VideoDetailPanel Info Tab

**Files:**
- Modify: `frontend/components/VideoDetailPanel.tsx:259-410` (Info tab 部分)

**核心原则：保留 Transcript/Analysis tab 不动，只重写 Info tab 内容。**

从 MediaCard.tsx 移植以下功能到 Info tab：
1. Title + description
2. 发布时间 + 时长
3. Engagement 统计 (2x2 grid)
4. MediaTagPicker（可编辑标签）
5. **新：StarRating + AI 状态图标同行**
6. AI 操作按钮（Copy/Extract/Rewrite/Analyze，2x2 grid，gradient 样式）
7. **新：Notes textarea**（blur 自动保存）
8. Description 全文
9. AI 结果显示区（extract/rewrite/analyze text）
10. Download + Refetch 底部按钮
11. Music download 行

### Step 1: 添加新的 imports

在 VideoDetailPanel.tsx 顶部添加：

```typescript
import { Video, Collection } from '../types';
import { MediaTagPicker } from './MediaTagPicker';
import { useToast } from './Toast';
import { getDownloadUrl } from '../services/dataService';
import { getSupabaseClient } from '../supabaseClient';
import { isVideoType, getVideoUrl, getCoverUrl } from '../utils/awemeType';
import {
  FileText, Sparkles, Eye, Loader2, Copy, Download, Check,
  Clock, Tag, ChevronRight, Brain, AlertCircle,
  Heart, MessageCircle, Share2, Bookmark, User, MonitorPlay,
  Music, Video as VideoIcon, Image as ImageIcon,
  PenTool, Wand2, RefreshCw, Star,
} from 'lucide-react';
```

### Step 2: 添加 Info tab 所需的 state 和 handlers

在组件函数内部，Transcript/Summary 相关 state 之后添加：

```typescript
// AI Feature States
const [extractText, setExtractText] = useState<string | null>(video.ai_extract_text || null);
const [rewrittenText, setRewrittenText] = useState<string | null>(video.ai_rewrite_text || null);
const [analysisText, setAnalysisText] = useState<string | null>(video.ai_analyze_text || null);
const [loadingAction, setLoadingAction] = useState<string | null>(null);
const [copied, setCopied] = useState(false);

// Notes
const [notesValue, setNotesValue] = useState(video.notes || '');

// Rating
const [ratingValue, setRatingValue] = useState(video.rating || 0);
const [hoverRating, setHoverRating] = useState(0);

// Download
const [isDownloading, setIsDownloading] = useState(false);
const [isRetrying, setIsRetrying] = useState(false);
const [retrySuccess, setRetrySuccess] = useState(false);
const [showRefetchMenu, setShowRefetchMenu] = useState(false);

const { addToast } = useToast();
const isVideo = isVideoType(video.media_type);
const videoUrl = getVideoUrl(video);
```

### Step 3: 添加 handlers（从 MediaCard 移植）

```typescript
// Rating
const handleRating = (star: number) => {
  const newRating = star === ratingValue ? 0 : star;
  setRatingValue(newRating);
  onUpdate?.(video.platform_id, { rating: newRating });
};

// Notes auto-save
const handleNotesBlur = () => {
  if (notesValue !== (video.notes || '')) {
    onUpdate?.(video.platform_id, { notes: notesValue });
  }
};

// AI action handler (copy/extract/rewrite/analyze) — 从 MediaCard 移植
const handleAction = async (action: string) => { ... };

// Save AI content
const saveAIContent = (field: string, content: string) => {
  if (onUpdate && video.platform_id) {
    onUpdate(video.platform_id, { [field]: content, ai_generated_at: new Date().toISOString() });
  }
};

// Download handlers — 从 MediaCard 移植
const onDownloadVideo = async () => { ... };
const onDownloadAudio = () => { ... };
const onRefetch = async (options: { video?: boolean; music?: boolean; cover?: boolean }) => { ... };
```

### Step 4: 重写 Info tab JSX

替换 `{activeTab === 'info' && (...)}` 内的全部内容，新布局：

```tsx
{activeTab === 'info' && (
  <div className="animate-in fade-in duration-300 flex flex-col">
    {/* Title */}
    <div className="px-4 pt-4">
      <h4 className="text-sm font-medium text-white break-words leading-snug">
        {video.title || video.desc || 'Untitled'}
      </h4>
      {video.desc && video.title && (
        <p className="mt-1.5 text-xs text-zinc-500 line-clamp-4">{video.desc}</p>
      )}
    </div>

    {/* Time + Duration */}
    <div className="px-4 mt-3 flex items-center gap-3 text-[11px] text-zinc-500">
      {video.published_at && (
        <span className="flex items-center gap-1">
          <Calendar size={11} /> {new Date(video.published_at).toLocaleDateString()}
        </span>
      )}
      {video.duration && (
        <span className="flex items-center gap-1">
          <Clock size={11} /> {video.duration}s
        </span>
      )}
    </div>

    {/* Engagement 2x2 */}
    <div className="px-4 mt-4">
      <div className="grid grid-cols-2 gap-1.5">
        {/* Heart/Comment/Share/Bookmark cards — 同当前实现 */}
      </div>
    </div>

    {/* Tags (editable) */}
    <div className="px-4 mt-4">
      {video.id ? (
        <MediaTagPicker mediaId={video.id} initialTagNames={video.tags || []} />
      ) : video.tags?.length > 0 && (
        <div className="flex flex-wrap gap-1.5">
          {video.tags.map((tag, i) => (
            <span key={i} className="text-[10px] px-2 py-0.5 rounded bg-zinc-800 text-zinc-400 border border-zinc-700">#{tag}</span>
          ))}
        </div>
      )}
    </div>

    {/* Rating + AI Status Icons — 同行 */}
    <div className="px-4 mt-4 flex items-center justify-between">
      {/* Star Rating */}
      <div className="flex items-center gap-0.5">
        {[1,2,3,4,5].map(star => (
          <button
            key={star}
            onClick={() => handleRating(star)}
            onMouseEnter={() => setHoverRating(star)}
            onMouseLeave={() => setHoverRating(0)}
            className="p-0.5 transition-colors"
          >
            <Star
              size={16}
              className={(hoverRating || ratingValue) >= star
                ? 'text-amber-400 fill-amber-400'
                : 'text-zinc-600'}
            />
          </button>
        ))}
      </div>
      {/* AI Status Icons */}
      <div className="flex items-center gap-2">
        <FileText size={14} className={getAIStatusClass(video.transcript_status)} title={`Transcript: ${video.transcript_status || 'none'}`} />
        <Sparkles size={14} className={getAIStatusClass(video.summary_status)} title={`Summary: ${video.summary_status || 'none'}`} />
        <Eye size={14} className={getAIStatusClass(video.visual_analysis_status)} title={`Visual: ${video.visual_analysis_status || 'none'}`} />
      </div>
    </div>

    {/* AI Action Buttons 2x2 */}
    <div className="px-4 mt-4 grid grid-cols-4 gap-1.5">
      <button onClick={() => handleAction('copy')} className="flex items-center justify-center gap-1 p-2 rounded-lg bg-zinc-800 hover:bg-zinc-700 text-zinc-400 hover:text-white transition-colors border border-zinc-700">
        {copied ? <Check size={14} className="text-green-500" /> : <Copy size={14} />}
        <span className="text-[10px]">{copied ? 'Done' : 'Copy'}</span>
      </button>
      <button onClick={() => handleAction('extract')} disabled={loadingAction === 'extract'} className="ai-btn ai-btn-extract flex items-center justify-center gap-1 p-2 rounded-lg text-teal-300">
        {loadingAction === 'extract' ? <Loader2 size={14} className="animate-spin relative z-10" /> : <FileText size={14} className="relative z-10" />}
        <span className="text-[10px] relative z-10">Extract</span>
      </button>
      <button onClick={() => handleAction('rewrite')} disabled={loadingAction === 'rewrite'} className="ai-btn ai-btn-rewrite flex items-center justify-center gap-1 p-2 rounded-lg text-violet-300">
        {loadingAction === 'rewrite' ? <Loader2 size={14} className="animate-spin relative z-10" /> : <PenTool size={14} className="relative z-10" />}
        <span className="text-[10px] relative z-10">Rewrite</span>
      </button>
      <button onClick={() => handleAction('analyze')} disabled={loadingAction === 'analyze'} className="ai-btn ai-btn-analyze flex items-center justify-center gap-1 p-2 rounded-lg text-indigo-300">
        {loadingAction === 'analyze' ? <Loader2 size={14} className="animate-spin relative z-10" /> : <Wand2 size={14} className="relative z-10" />}
        <span className="text-[10px] relative z-10">Analyze</span>
      </button>
    </div>

    {/* Notes */}
    <div className="px-4 mt-4">
      <h4 className="text-[11px] font-semibold text-zinc-500 uppercase tracking-widest mb-1.5">Notes</h4>
      <textarea
        value={notesValue}
        onChange={e => setNotesValue(e.target.value)}
        onBlur={handleNotesBlur}
        placeholder="Add notes..."
        className="w-full bg-zinc-900 border border-zinc-800 rounded-lg px-3 py-2 text-xs text-zinc-300 placeholder-zinc-600 resize-none min-h-[60px] focus:outline-none focus:border-zinc-600 transition-colors"
        rows={3}
      />
    </div>

    {/* Description */}
    <div className="px-4 mt-4">
      <p className="text-xs text-zinc-300 whitespace-pre-wrap leading-relaxed">
        {video.description || <span className="text-zinc-500 italic">No description available.</span>}
      </p>
    </div>

    {/* AI Results Area */}
    <div className="px-4 mt-4 space-y-3">
      {extractText && (
        <div className="animate-in fade-in slide-in-from-top-2">
          <div className="flex items-center gap-2 mb-1.5">
            <div className="p-1 rounded bg-teal-500/10 text-teal-400"><FileText size={12} /></div>
            <span className="text-xs font-semibold text-teal-200">Extracted Data</span>
          </div>
          <div className="p-3 rounded-lg border border-teal-500/20 bg-teal-500/5 text-xs text-zinc-300 leading-relaxed whitespace-pre-wrap">{extractText}</div>
        </div>
      )}
      {rewrittenText && (/* similar for rewrite */)}
      {analysisText && (/* similar for analysis */)}
    </div>

    {/* Download Footer */}
    <div className="px-4 mt-4 pt-3 border-t border-zinc-800/60 pb-6 space-y-2">
      <div className="flex gap-2">
        {isVideo && videoUrl && (
          <button onClick={onDownloadVideo} disabled={isDownloading}
            className="flex-1 flex items-center justify-center gap-2 px-3 py-2 bg-zinc-100 hover:bg-white text-black rounded-lg font-medium text-xs transition-colors">
            {isDownloading ? <Loader2 size={14} className="animate-spin" /> : <Download size={14} />}
            {isDownloading ? 'Downloading...' : 'Download'}
          </button>
        )}
        {/* Refetch dropdown button */}
        <div className="relative">
          <button onClick={() => setShowRefetchMenu(!showRefetchMenu)} disabled={isRetrying}
            className="h-9 w-9 flex items-center justify-center rounded-lg bg-zinc-800 text-zinc-300 border border-zinc-700 hover:bg-zinc-700 hover:text-white transition-colors">
            {isRetrying ? <Loader2 size={16} className="animate-spin" /> : retrySuccess ? <Check size={16} className="text-emerald-400" /> : <RefreshCw size={16} />}
          </button>
          {showRefetchMenu && (
            <>
              <div className="fixed inset-0 z-40" onClick={() => setShowRefetchMenu(false)} />
              <div className="absolute bottom-full right-0 mb-2 w-44 bg-zinc-900 border border-zinc-700 rounded-lg shadow-xl z-50 py-1">
                <button onClick={() => onRefetch({ video: true })} className="w-full px-3 py-2 text-left text-xs text-zinc-300 hover:bg-zinc-800 flex items-center gap-2">
                  <VideoIcon size={14} className="text-indigo-400" /> Download Video
                </button>
                <button onClick={() => onRefetch({ cover: true })} className="w-full px-3 py-2 text-left text-xs text-zinc-300 hover:bg-zinc-800 flex items-center gap-2">
                  <ImageIcon size={14} className="text-emerald-400" /> Download Cover
                </button>
                <button onClick={() => onRefetch({ music: true })} className="w-full px-3 py-2 text-left text-xs text-zinc-300 hover:bg-zinc-800 flex items-center gap-2">
                  <Music size={14} className="text-amber-400" /> Download Audio
                </button>
                <div className="border-t border-zinc-700 my-1" />
                <button onClick={() => onRefetch({ video: true, cover: true, music: true })} className="w-full px-3 py-2 text-left text-xs text-zinc-300 hover:bg-zinc-800 flex items-center gap-2">
                  <Download size={14} className="text-sky-400" /> Download All
                </button>
              </div>
            </>
          )}
        </div>
      </div>
      {/* Music download row */}
      {video.need_download_music && (
        <button onClick={onDownloadAudio}
          className="w-full flex items-center justify-between px-3 py-2.5 bg-zinc-950 rounded-lg border border-zinc-800 text-xs text-zinc-400 hover:text-zinc-200 transition-colors">
          <div className="flex items-center gap-2">
            <div className="p-1 bg-indigo-500/10 rounded"><Music size={12} className="text-indigo-500" /></div>
            <span className="truncate max-w-[200px]">{video.music_name || 'Original Audio'}</span>
          </div>
          <Download size={12} />
        </button>
      )}
    </div>
  </div>
)}
```

### Step 5: 验证构建

Run: `cd frontend && npm run build`
Expected: 成功

### Step 6: Commit

```bash
git add frontend/components/VideoDetailPanel.tsx
git commit -m "feat: rewrite VideoDetailPanel Info tab as AI creation workbench"
```

---

## Task 4: RipVaultView 面板 — 添加 Rating + Notes

**Files:**
- Modify: `frontend/components/RipVaultView.tsx:540-593` (Tags 和 AI Status 之间)

### Step 1: 添加 state

在 RipVaultView 组件中，`selectedVideo` 相关 state 附近添加：

```typescript
const [panelNotes, setPanelNotes] = useState('');
const [panelRating, setPanelRating] = useState(0);
const [panelHoverRating, setPanelHoverRating] = useState(0);
```

当 `selectedVideo` 变化时同步 notes/rating:

```typescript
useEffect(() => {
  setPanelNotes(selectedVideo?.notes || '');
  setPanelRating(selectedVideo?.rating || 0);
}, [selectedVideo?.platform_id]);
```

### Step 2: 添加 handlers

```typescript
const handlePanelRating = (star: number) => {
  if (!selectedVideo) return;
  const newRating = star === panelRating ? 0 : star;
  setPanelRating(newRating);
  handleUpdateLibraryItem(selectedVideo.platform_id, { rating: newRating });
};

const handlePanelNotesBlur = () => {
  if (!selectedVideo) return;
  if (panelNotes !== (selectedVideo.notes || '')) {
    handleUpdateLibraryItem(selectedVideo.platform_id, { notes: panelNotes });
  }
};
```

### Step 3: 在 Tags 和 AI Status 之间插入 Rating + Notes

在 line 554（Tags 结尾 `</div>`）之后、line 557（AI Status 开始）之前，插入：

```tsx
{/* Rating */}
<div className="px-4 mt-4">
  <h4 className="text-[11px] font-semibold text-zinc-500 uppercase tracking-widest mb-2">
    Rating
  </h4>
  <div className="flex items-center gap-0.5">
    {[1,2,3,4,5].map(star => (
      <button
        key={star}
        onClick={() => handlePanelRating(star)}
        onMouseEnter={() => setPanelHoverRating(star)}
        onMouseLeave={() => setPanelHoverRating(0)}
        className="p-0.5 transition-colors"
      >
        <Star
          size={16}
          className={(panelHoverRating || panelRating) >= star
            ? 'text-amber-400 fill-amber-400'
            : 'text-zinc-600'}
        />
      </button>
    ))}
  </div>
</div>

{/* Notes */}
<div className="px-4 mt-4">
  <h4 className="text-[11px] font-semibold text-zinc-500 uppercase tracking-widest mb-1.5">
    Notes
  </h4>
  <textarea
    value={panelNotes}
    onChange={e => setPanelNotes(e.target.value)}
    onBlur={handlePanelNotesBlur}
    placeholder="Add notes..."
    className="w-full bg-zinc-900 border border-zinc-800 rounded-lg px-3 py-2 text-xs text-zinc-300 placeholder-zinc-600 resize-none min-h-[60px] focus:outline-none focus:border-zinc-600 transition-colors"
    rows={3}
  />
</div>
```

### Step 4: 添加 Star import

在 RipVaultView.tsx 的 lucide-react imports 中添加 `Star`。

### Step 5: 验证构建

Run: `cd frontend && npm run build`
Expected: 成功

### Step 6: Commit

```bash
git add frontend/components/RipVaultView.tsx
git commit -m "feat: add Rating and Notes to RipVaultView info panel"
```

---

## Task 5: 验证与截图

**Step 1:** 启动后端 `cd backend && uv run uvicorn app.main:app --reload --port 8081`

**Step 2:** 启动前端 `cd frontend && npm run dev -- --port 5176`

**Step 3:** 打开浏览器 http://localhost:5176/，登录 test@test.com / test1234

**Step 4:** 验证清单：

- [ ] RipVaultView：选中一个视频 → 右侧面板显示 Rating 星级
- [ ] RipVaultView：点击星星 → 评分保存 → 刷新保持
- [ ] RipVaultView：Notes 输入文字 → blur → 刷新保持
- [ ] 详情页：点击 "Open Detail" 进入 PlayerPage
- [ ] VideoDetailPanel Info tab：标题、时间、Engagement 正确显示
- [ ] VideoDetailPanel Info tab：MediaTagPicker 可编辑标签
- [ ] VideoDetailPanel Info tab：Rating ★★★★☆ 与 AI 图标在同一行
- [ ] VideoDetailPanel Info tab：AI 按钮（Copy/Extract/Rewrite/Analyze）可点击
- [ ] VideoDetailPanel Info tab：Notes textarea 可编辑、blur 保存
- [ ] VideoDetailPanel Info tab：Description 全文显示
- [ ] VideoDetailPanel Info tab：Download 按钮功能正常
- [ ] VideoDetailPanel Info tab：Refetch dropdown 显示（Video/Cover/Audio/All）
- [ ] Transcript/Analysis tabs 不受影响

**Step 5:** 截图确认
