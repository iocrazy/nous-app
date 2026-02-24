# RipVault Sprite Scrub + Unified Detail Page

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Add sprite hover scrub to RipVault cards, and extend ResourceDetail with Transcript/Analysis tabs so all detail views share one component.

**Architecture:** CompactMediaCard gains sprite scrub by looking up the resource via `media_id` linkage. ResourceDetail's inspector gains two new tabs (Transcript, Analysis) extracted from VideoDetailPanel. RipVault double-click navigates to ResourceDetail instead of PlayerPage.

**Tech Stack:** React, TypeScript, existing backend sprite/AI endpoints

---

### Task 1: Add sprite hover scrub to CompactMediaCard

**Files:**
- Modify: `frontend/components/CompactMediaCard.tsx`
- Modify: `frontend/services/resourceService.ts` (add lookupResourceByMediaId if needed)

**Step 1: Add sprite scrub state and refs**

Add to CompactMediaCard the same hover scrub pattern from ResourceCard:
- `isHovering`, `spriteLoaded`, `spriteError`, `scrubPercent` state
- `thumbRef`, `spriteImgRef` refs
- Compute `spriteUrl` from resource ID

**Step 2: Accept optional resourceId prop**

Add `resourceId?: string` to `CompactMediaCardProps`. When provided, use `getPreviewSpriteUrl(resourceId)` for the sprite URL.

**Step 3: Wire up hover handlers on thumbnail container**

Replace the current `onMouseEnter/onMouseLeave` (which toggles video autoplay) with:
- If `resourceId` exists → sprite scrub behavior (preload sprite on first hover, track mouse position)
- Fallback to current video autoplay behavior if no resourceId

**Step 4: Render sprite overlay and progress bar**

Same pattern as ResourceCard:
- Sprite frame overlay (absolute positioned, centered, contain-fit)
- Bottom progress bar showing scrub position
- Hide the cover image when sprite is active

**Step 5: Commit**

```bash
git add frontend/components/CompactMediaCard.tsx
git commit -m "feat: add sprite hover scrub to CompactMediaCard"
```

---

### Task 2: Pass resourceId from RipVaultView to CompactMediaCard

**Files:**
- Modify: `frontend/hooks/useLibrary.ts` (or wherever library data is fetched)
- Modify: `frontend/components/RipVaultView.tsx`
- Modify: `frontend/types.ts` (add resource_id to ParsedMedia if not present)

**Step 1: Check if parsed_media already returns resource linkage**

The `resources` table has `media_id` linking to `parsed_media.id`. We need the reverse lookup: given a parsed_media item, find its resource_id.

**Step 2: Add resource_id to the library fetch query**

In the backend endpoint that returns parsed_media for the library, join with resources table to include `resource_id`. Alternatively, add a frontend lookup.

Simplest approach: Add `resource_id` as an optional field on `ParsedMedia` type. The backend `/api/v1/media` endpoint should return this via a left join.

**Step 3: Pass resourceId to CompactMediaCard**

In RipVaultView grid rendering:
```tsx
<CompactMediaCard
  key={item.platform_id}
  data={item}
  resourceId={item.resource_id}
  onClick={...}
/>
```

**Step 4: Commit**

```bash
git add frontend/components/RipVaultView.tsx frontend/types.ts
git commit -m "feat: pass resource_id to CompactMediaCard for sprite scrub"
```

---

### Task 3: Add Transcript tab to ResourceDetail inspector

**Files:**
- Modify: `frontend/components/ResourceDetail.tsx`

**Step 1: Add tab type and state**

Extend `rightTab` state from `'info' | 'review'` to `'info' | 'review' | 'transcript' | 'analysis'`.

**Step 2: Add Transcript and Analysis tab buttons**

In the inspector tab bar, conditionally show Transcript and Analysis tabs when the resource is a video or audio file (`mime_type?.startsWith('video/') || mime_type?.startsWith('audio/')`).

Tab layout:
```
[ Info ] [ Review ] [ Transcript ] [ Analysis ]
```

**Step 3: Implement Transcript tab content**

Extract the transcript UI logic from VideoDetailPanel (lines 252-392) into a standalone section within ResourceDetail. Use the `ByResource` API variants:
- `triggerTranscriptionByResource(resourceId)`
- `getTranscriptByResource(resourceId)`
- Poll for completion
- Display segments with timestamps
- Export as SRT/TXT

**Step 4: Add transcript state management**

Add state: `transcriptData`, `transcriptLoading`, `transcriptError`
Add effect to fetch transcript when tab switches to 'transcript' and `transcript_status === 'completed'`

**Step 5: Commit**

```bash
git add frontend/components/ResourceDetail.tsx
git commit -m "feat: add Transcript tab to ResourceDetail inspector"
```

---

### Task 4: Add Analysis tab to ResourceDetail inspector

**Files:**
- Modify: `frontend/components/ResourceDetail.tsx`

**Step 1: Implement Analysis tab content**

Two sections (same as VideoDetailPanel lines 394-595):

**Summary section:**
- Trigger button: `triggerSummaryByResource(resourceId)`
- Display: summary text, key points, topics
- Status indicator

**Visual Analysis section:**
- Trigger button: `triggerVisualAnalysisByResource(resourceId)` (video only)
- Display: analysis result text
- Status indicator

**Step 2: Add analysis state management**

Add state: `summaryData`, `summaryLoading`, `analysisLoading`
Fetch on tab switch when status is 'completed'

**Step 3: Commit**

```bash
git add frontend/components/ResourceDetail.tsx
git commit -m "feat: add Analysis tab to ResourceDetail inspector"
```

---

### Task 5: Update RipVault navigation to use ResourceDetail

**Files:**
- Modify: `frontend/components/RipVaultView.tsx`

**Step 1: Change double-click navigation**

Current: navigates to `/t/{teamId}/player/{video_id}`
Change to: navigate to `/t/{teamId}/resources/file/{resourceId}` when resource_id exists.
Fallback to current PlayerPage when no resource_id (video not yet downloaded).

**Step 2: Commit**

```bash
git add frontend/components/RipVaultView.tsx
git commit -m "feat: RipVault double-click navigates to unified ResourceDetail"
```

---

### Task 6: Verify and test

1. `npm run build` — 前端构建通过
2. 打开 RipVault 页面，hover 视频卡片 → 看到 sprite 扫描预览
3. 双击视频卡片 → 导航到 ResourceDetail 页面
4. ResourceDetail inspector 显示 4 个 tab：Info / Review / Transcript / Analysis
5. Transcript tab：可触发转录、显示结果、导出 SRT
6. Analysis tab：可触发摘要和视觉分析
