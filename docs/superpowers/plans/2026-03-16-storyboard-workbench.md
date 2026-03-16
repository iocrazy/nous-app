# Storyboard Workbench Implementation Plan

> **For agentic workers:** REQUIRED: Use superpowers:subagent-driven-development (if subagents available) or superpowers:executing-plans to implement this plan. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build a full-featured storyboard workbench module for MediaHub with node-based canvas, AI image/video generation, character consistency, script-to-storyboard, and animatic playback.

**Architecture:** FastAPI backend (5 routers, 4 services, repository pattern) + React 19 frontend (@xyflow/react canvas, Zustand store, Konva annotations) + Supabase PostgreSQL (8 tables, Snowflake BIGINT IDs, RLS) + Celery async tasks + NAS file storage.

**Tech Stack:** Python 3.12+, FastAPI, Celery, Supabase, React 19, TypeScript, @xyflow/react v12, Zustand, react-konva, TailwindCSS, Pillow, FFmpeg

**Spec:** `docs/superpowers/specs/2026-03-16-storyboard-workbench-design.md`

**Working Directory:** `/Volumes/program/project-code/repos/mediahub/.worktrees/storyboard/`

---

## File Structure

### Backend (new files)

| File | Responsibility |
|------|---------------|
| `supabase/migrations/107_storyboard_schema.sql` | All 8 tables, indexes, triggers, RLS policies |
| `backend/app/schemas/storyboard.py` | Pydantic request/response models |
| `backend/app/repositories/storyboard_repository.py` | Data access layer (all 6 repositories) |
| `backend/app/services/storyboard_service.py` | Project CRUD, canvas sync, character management |
| `backend/app/services/storyboard_ai_service.py` | AI generation, script splitting, video analysis |
| `backend/app/services/storyboard_image_service.py` | Image processing (split, merge, preview, PNG metadata) |
| `backend/app/services/storyboard_export_service.py` | PNG/PDF/ZIP export |
| `backend/app/api/sb_projects_router.py` | Project CRUD endpoints |
| `backend/app/api/sb_canvas_router.py` | Node/edge/frame endpoints |
| `backend/app/api/sb_characters_router.py` | Character CRUD endpoints |
| `backend/app/api/sb_ai_router.py` | AI generation endpoints |
| `backend/app/api/sb_export_router.py` | Export endpoints |
| `backend/app/services/video_providers/base.py` | Abstract provider interfaces |
| `backend/app/services/video_providers/registry.py` | Provider registry |
| `backend/app/tasks/storyboard_tasks.py` | Celery async tasks |

### Backend (modified files)

| File | Change |
|------|--------|
| `backend/app/api/__init__.py` | Register 5 new routers |
| `backend/app/core/enums.py` | Add sb_ task types (if enum-based) |

### Frontend (new files)

| File | Responsibility |
|------|---------------|
| `frontend/pages/StoryboardWorkbench/index.tsx` | Route entry, project list ↔ canvas switch |
| `frontend/pages/StoryboardWorkbench/ProjectListPage.tsx` | Project grid with cards |
| `frontend/pages/StoryboardWorkbench/CanvasEditorPage.tsx` | Full-screen canvas editor |
| `frontend/stores/storyboardStore.ts` | Zustand store for all storyboard state |
| `frontend/services/storyboardService.ts` | API client layer |
| `frontend/hooks/storyboard/useStoryboardCanvas.ts` | Canvas interaction logic |
| `frontend/hooks/storyboard/useStoryboardPersist.ts` | Debounced persistence |
| `frontend/hooks/storyboard/useStoryboardRealtime.ts` | Supabase Realtime subscription |
| `frontend/hooks/storyboard/useAnimaticPlayer.ts` | Animatic playback control |
| `frontend/hooks/storyboard/useImagePool.ts` | Image pool reference management |
| `frontend/components/storyboard/canvas/StoryboardCanvas.tsx` | @xyflow/react main canvas |
| `frontend/components/storyboard/canvas/CanvasToolbar.tsx` | Top toolbar |
| `frontend/components/storyboard/canvas/CanvasMiniMap.tsx` | Minimap |
| `frontend/components/storyboard/canvas/NodeSelectionMenu.tsx` | Node type picker |
| `frontend/components/storyboard/canvas/edges/SmartEdge.tsx` | Custom edge component |
| `frontend/components/storyboard/nodes/UploadNode.tsx` | Upload image node |
| `frontend/components/storyboard/nodes/ImageEditNode.tsx` | AI generation node |
| `frontend/components/storyboard/nodes/StoryboardSplitNode.tsx` | Grid split node |
| `frontend/components/storyboard/nodes/StoryboardGenNode.tsx` | Batch generation node |
| `frontend/components/storyboard/nodes/ImageToVideoNode.tsx` | Image-to-video node |
| `frontend/components/storyboard/nodes/TextAnnotationNode.tsx` | Text annotation node |
| `frontend/components/storyboard/nodes/GroupNode.tsx` | Group node |
| `frontend/components/storyboard/nodes/ExportNode.tsx` | Export node |
| `frontend/components/storyboard/nodes/shared/NodeWrapper.tsx` | Common node shell |
| `frontend/components/storyboard/nodes/shared/NodeControlStyles.ts` | Unified control styles |
| `frontend/components/storyboard/nodes/shared/NodeImagePreview.tsx` | Image preview in node |
| `frontend/components/storyboard/chat/ChatPanel.tsx` | AI chat sidebar |
| `frontend/components/storyboard/chat/ChatMessage.tsx` | Chat message bubble |
| `frontend/components/storyboard/chat/ChatInput.tsx` | Chat input with @mention |
| `frontend/components/storyboard/timeline/FrameTimeline.tsx` | Bottom timeline bar |
| `frontend/components/storyboard/timeline/FrameThumb.tsx` | Timeline frame thumbnail |
| `frontend/components/storyboard/timeline/AnimaticPlayer.tsx` | Animatic player |
| `frontend/components/storyboard/characters/CharacterPanel.tsx` | Character management panel |
| `frontend/components/storyboard/characters/CharacterCard.tsx` | Character card |
| `frontend/components/storyboard/characters/CharacterEditor.tsx` | Character edit dialog |
| `frontend/components/storyboard/tools/CropTool.tsx` | Crop tool |
| `frontend/components/storyboard/tools/AnnotateTool.tsx` | Konva annotation tool |
| `frontend/components/storyboard/tools/SplitTool.tsx` | Grid split tool |
| `frontend/components/storyboard/tools/ShotMetadataEditor.tsx` | Shot metadata editor |
| `frontend/components/storyboard/tools/CameraOverlay.tsx` | Camera movement overlay |
| `frontend/components/storyboard/project/ProjectCard.tsx` | Project card component |
| `frontend/components/storyboard/project/NewProjectDialog.tsx` | New project dialog |
| `frontend/components/storyboard/project/ScriptImportDialog.tsx` | Script import dialog |
| `frontend/components/storyboard/export/ExportDialog.tsx` | Export settings dialog |
| `frontend/components/storyboard/export/ExportPreview.tsx` | Export preview |
| `frontend/components/storyboard/shared/ImageViewerModal.tsx` | Full-screen image viewer |
| `frontend/components/storyboard/shared/ImagePool.ts` | Image pool utility |

### Frontend (modified files)

| File | Change |
|------|--------|
| `frontend/router.tsx` | Add storyboard route under team scope |
| `frontend/package.json` | Add @xyflow/react, zustand, react-konva deps |

### i18n (modified files)

| File | Change |
|------|--------|
| `frontend/public/locales/en.json` | Add storyboard namespace translations |
| `frontend/public/locales/zh.json` | Add storyboard namespace translations |

---

## Chunk 1: Database & Backend Foundation

### Task 1: Database Migration

**Files:**
- Create: `supabase/migrations/107_storyboard_schema.sql`

- [ ] **Step 1: Write the migration SQL**

Create `107_storyboard_schema.sql` with all 8 tables, junction table, indexes, triggers, RLS policies, and realtime publication. Use Snowflake BIGINT PKs. Reference the spec section 3 for exact schema.

Key tables: `storyboard_projects`, `storyboard_characters`, `storyboard_nodes`, `storyboard_edges`, `storyboard_frames`, `storyboard_frame_characters`, `storyboard_assets`, `storyboard_video_assets`.

Include: `update_sb_updated_at()` trigger function, per-table triggers, team-scoped RLS policies, service_role bypass, `supabase_realtime` publication.

- [ ] **Step 2: Verify migration syntax**

Run: `cd backend && python -c "open('../../supabase/migrations/107_storyboard_schema.sql').read()"`
Expected: No syntax errors in reading

- [ ] **Step 3: Commit**

```bash
git add supabase/migrations/107_storyboard_schema.sql
git commit -m "feat(db): add storyboard schema migration (8 tables, RLS, triggers)"
```

---

### Task 2: Pydantic Schemas

**Files:**
- Create: `backend/app/schemas/storyboard.py`

- [ ] **Step 1: Write all request/response schemas**

Create schemas matching spec section 4:
- `StoryboardProjectCreate`, `StoryboardProjectUpdate`
- `StoryboardNodeCreate`, `StoryboardNodeUpdate`
- `CanvasSyncRequest`
- `StoryboardFrameUpdate`
- `CharacterCreate`, `CharacterUpdate`
- `GenerateImageRequest`, `GenerateVideoRequest`
- `SplitScriptRequest`
- Response models: `StoryboardProjectResponse`, `StoryboardNodeResponse`, etc.

Use `Field()` with validation (min_length, max_length, pattern, ge, le).

- [ ] **Step 2: Verify imports**

Run: `cd backend && uv run python -c "from app.schemas.storyboard import *; print('OK')"`
Expected: OK

- [ ] **Step 3: Commit**

```bash
git add backend/app/schemas/storyboard.py
git commit -m "feat(schema): add storyboard Pydantic request/response models"
```

---

### Task 3: Repository Layer

**Files:**
- Create: `backend/app/repositories/storyboard_repository.py`

- [ ] **Step 1: Write all 6 repository classes**

Follow existing pattern from `resources_repository.py`:
- `TABLE_*` constants for table names
- `_get_client()` async helper
- CRUD methods: `create`, `get_by_id`, `list_by_*`, `update`, `delete`
- `StoryboardProjectRepository` — includes `update_viewport`, `soft_delete`
- `StoryboardNodeRepository` — includes `bulk_upsert`
- `StoryboardEdgeRepository` — includes `bulk_upsert`
- `StoryboardFrameRepository` — includes `reorder`, `bulk_upsert`
- `StoryboardCharacterRepository`
- `StoryboardAssetRepository` — includes `find_by_hash`

- [ ] **Step 2: Verify imports**

Run: `cd backend && uv run python -c "from app.repositories.storyboard_repository import *; print('OK')"`
Expected: OK

- [ ] **Step 3: Commit**

```bash
git add backend/app/repositories/storyboard_repository.py
git commit -m "feat(repo): add storyboard repository layer (6 repositories)"
```

---

### Task 4: Core Service

**Files:**
- Create: `backend/app/services/storyboard_service.py`

- [ ] **Step 1: Write StoryboardService class**

Methods:
- `create_project(team_id, user_id, name, description)` — create project + ensure NAS directory
- `create_project_from_script(team_id, user_id, name, script_text)` — dispatch Celery task
- `get_project_full(project_id)` — fetch project + nodes + edges + frames + characters in one call
- `list_projects(team_id, page, limit, search, sort)` — paginated list
- `update_project(project_id, data)` — update name/description/settings
- `soft_delete_project(project_id)` — set status='deleted'
- `update_viewport(project_id, viewport_json)` — lightweight update
- `sync_canvas(project_id, sync_request)` — batch upsert nodes/edges, delete removed
- `create_character(project_id, name, description, reference_image)` — save image to NAS, generate thumbnail
- `get_character_prompt_fragment(character_id)` — build prompt text from visual_traits

- [ ] **Step 2: Verify imports**

Run: `cd backend && uv run python -c "from app.services.storyboard_service import StoryboardService; print('OK')"`
Expected: OK

- [ ] **Step 3: Commit**

```bash
git add backend/app/services/storyboard_service.py
git commit -m "feat(service): add storyboard core service (project CRUD, canvas sync, characters)"
```

---

### Task 5: Image Processing Service

**Files:**
- Create: `backend/app/services/storyboard_image_service.py`

- [ ] **Step 1: Write StoryboardImageService class**

Methods:
- `split_image(image_path, rows, cols)` — grid split with Pillow, return frame paths
- `detect_scenes(video_path, threshold=30)` — FFmpeg frame extraction + pixel diff
- `merge_frames(frames, layout, annotations)` — composite with frame numbers + notes
- `generate_preview(image_path)` — 512px thumbnail (JPEG)
- `embed_png_metadata(image_path, metadata)` — write grid info to PNG chunks
- `read_png_metadata(image_path)` — read embedded metadata
- `compute_file_hash(file_path)` — SHA-256 for dedup

- [ ] **Step 2: Verify imports**

Run: `cd backend && uv run python -c "from app.services.storyboard_image_service import StoryboardImageService; print('OK')"`
Expected: OK

- [ ] **Step 3: Commit**

```bash
git add backend/app/services/storyboard_image_service.py
git commit -m "feat(service): add storyboard image processing service"
```

---

### Task 6: AI Provider Base & Registry

**Files:**
- Create: `backend/app/services/video_providers/base.py`
- Create: `backend/app/services/video_providers/registry.py`
- Create: `backend/app/services/video_providers/__init__.py`

- [ ] **Step 1: Write abstract base classes**

`base.py`:
- `BaseImageProvider(ABC)` with `generate()`, `check_status()`
- `BaseVideoProvider(ABC)` with `generate()`, `check_status()`
- Common dataclasses: `ImageGenResult`, `VideoGenResult`, `TaskStatus`

- [ ] **Step 2: Write provider registry**

`registry.py`:
- `ProviderRegistry` class with `register_image_provider()`, `register_video_provider()`
- `get_image_provider(name)`, `get_video_provider(name)`
- `list_available_models()`

- [ ] **Step 3: Verify imports**

Run: `cd backend && uv run python -c "from app.services.video_providers import ProviderRegistry; print('OK')"`
Expected: OK

- [ ] **Step 4: Commit**

```bash
git add backend/app/services/video_providers/
git commit -m "feat(provider): add AI provider base classes and registry"
```

---

### Task 7: AI Service

**Files:**
- Create: `backend/app/services/storyboard_ai_service.py`

- [ ] **Step 1: Write StoryboardAIService class**

Methods:
- `generate_image(request)` — build prompt with character traits, dispatch Celery task
- `generate_image_batch(requests)` — parallel aggregation by provider
- `generate_video(request)` — dispatch video generation task
- `split_script(script_text, style_guide)` — call LLM, return structured scene breakdown
- `analyze_video(video_url)` — scene detect + LLM visual analysis + prompt reversal
- `chat(project_id, message, selected_frame_id)` — contextual AI chat

- [ ] **Step 2: Verify imports**

Run: `cd backend && uv run python -c "from app.services.storyboard_ai_service import StoryboardAIService; print('OK')"`
Expected: OK

- [ ] **Step 3: Commit**

```bash
git add backend/app/services/storyboard_ai_service.py
git commit -m "feat(service): add storyboard AI service (generation, script split, chat)"
```

---

### Task 8: Export Service

**Files:**
- Create: `backend/app/services/storyboard_export_service.py`

- [ ] **Step 1: Write StoryboardExportService class**

Methods:
- `export_png(project_id, options)` — merge frames into grid with annotations
- `export_pdf(project_id, options)` — generate storyboard PDF document
- `export_zip(project_id)` — bundle project JSON + all assets

- [ ] **Step 2: Verify imports**

Run: `cd backend && uv run python -c "from app.services.storyboard_export_service import StoryboardExportService; print('OK')"`
Expected: OK

- [ ] **Step 3: Commit**

```bash
git add backend/app/services/storyboard_export_service.py
git commit -m "feat(service): add storyboard export service (PNG/PDF/ZIP)"
```

---

### Task 9: Celery Tasks

**Files:**
- Create: `backend/app/tasks/storyboard_tasks.py`

- [ ] **Step 1: Write all Celery task functions**

Tasks (see spec section 7.1):
- `generate_storyboard_image` — single frame AI generation
- `generate_storyboard_image_batch` — parallel batch generation
- `generate_storyboard_video` — image-to-video
- `split_script_to_storyboard` — LLM script analysis → create nodes/frames
- `analyze_video_scenes` — FFmpeg + LLM analysis
- `export_storyboard` — PNG/PDF/ZIP export
- `split_image_grid` — grid split
- `process_annotation` — apply Konva annotations to image

Each task: update `unified_tasks` status via `TaskTracker`, handle errors, retry logic.

- [ ] **Step 2: Verify imports**

Run: `cd backend && uv run python -c "from app.tasks.storyboard_tasks import *; print('OK')"`
Expected: OK

- [ ] **Step 3: Commit**

```bash
git add backend/app/tasks/storyboard_tasks.py
git commit -m "feat(tasks): add storyboard Celery async tasks"
```

---

### Task 10: API Routers

**Files:**
- Create: `backend/app/api/sb_projects_router.py`
- Create: `backend/app/api/sb_canvas_router.py`
- Create: `backend/app/api/sb_characters_router.py`
- Create: `backend/app/api/sb_ai_router.py`
- Create: `backend/app/api/sb_export_router.py`
- Modify: `backend/app/api/__init__.py`

- [ ] **Step 1: Write sb_projects_router.py**

6 endpoints: POST/GET/GET/{id}/PUT/{id}/DELETE/{id}/PUT/{id}/viewport
Follow existing pattern: `APIRouter(prefix="/storyboard/projects")`, `AuthDep`, section comments.

- [ ] **Step 2: Write sb_canvas_router.py**

9 endpoints: nodes CRUD, edges CRUD, frames CRUD, batch sync, reorder.

- [ ] **Step 3: Write sb_characters_router.py**

4 endpoints: character CRUD.

- [ ] **Step 4: Write sb_ai_router.py**

6 endpoints: generate image/video, split-script, analyze-video, detect-scenes, chat.

- [ ] **Step 5: Write sb_export_router.py**

1 endpoint: export.

- [ ] **Step 6: Register routers in __init__.py**

Add imports and `api_router.include_router()` calls for all 5 routers with tags.

- [ ] **Step 7: Verify server starts**

Run: `cd backend && uv run uvicorn app.main:app --port 8081 &; sleep 3; curl http://localhost:8081/api/v1/storyboard/projects -H "Authorization: Bearer test" ; kill %1`
Expected: 401 or valid response (server starts without import errors)

- [ ] **Step 8: Commit**

```bash
git add backend/app/api/sb_*.py backend/app/api/__init__.py
git commit -m "feat(api): add 5 storyboard routers (26 endpoints)"
```

---

## Chunk 2: Frontend Foundation

### Task 11: Install Dependencies

**Files:**
- Modify: `frontend/package.json`

- [ ] **Step 1: Install new npm packages**

```bash
cd frontend
npm install @xyflow/react zustand react-konva konva
```

- [ ] **Step 2: Verify installation**

Run: `cd frontend && npm ls @xyflow/react zustand react-konva`
Expected: Packages listed without errors

- [ ] **Step 3: Commit**

```bash
git add frontend/package.json frontend/package-lock.json
git commit -m "chore(deps): add @xyflow/react, zustand, react-konva, konva"
```

---

### Task 12: TypeScript Types & API Service

**Files:**
- Create: `frontend/services/storyboardService.ts`
- Modify: `frontend/types.ts` (add storyboard types)

- [ ] **Step 1: Add TypeScript interfaces to types.ts**

Add interfaces: `StoryboardProject`, `StoryboardNode`, `StoryboardEdge`, `StoryboardFrame`, `StoryboardCharacter`, `StoryboardAsset`, `StoryboardVideoAsset`, `ProjectSummary`.

- [ ] **Step 2: Write storyboardService.ts**

Follow existing `resourceService.ts` pattern (exported async functions, `getApiUrl()`, `getAuthHeaders()`):
- `fetchProjects(teamId, page, limit)`
- `fetchProject(projectId)`
- `createProject(data)`
- `updateProject(projectId, data)`
- `deleteProject(projectId)`
- `updateViewport(projectId, viewport)`
- `syncCanvas(projectId, syncData)`
- `fetchCharacters(projectId)`
- `createCharacter(projectId, data)`
- `updateCharacter(characterId, data)`
- `deleteCharacter(characterId)`
- `updateFrame(frameId, data)`
- `reorderFrames(frameIds)`
- `generateImage(data)`
- `generateVideo(data)`
- `splitScript(data)`
- `exportProject(projectId, format, options)`
- `chat(projectId, message, frameId)`

- [ ] **Step 3: Verify TypeScript compiles**

Run: `cd frontend && npx tsc --noEmit --pretty 2>&1 | head -20`
Expected: No errors in new files

- [ ] **Step 4: Commit**

```bash
git add frontend/types.ts frontend/services/storyboardService.ts
git commit -m "feat(frontend): add storyboard types and API service layer"
```

---

### Task 13: Zustand Store

**Files:**
- Create: `frontend/stores/storyboardStore.ts`

- [ ] **Step 1: Write Zustand store**

State shape per spec section 5.3. Actions:
- Project: `setProjectList`, `setCurrentProject`, `clearCurrentProject`
- Canvas: `setNodes`, `setEdges`, `addNode`, `updateNode`, `deleteNode`, `onNodesChange`, `onEdgesChange`
- Selection: `setSelectedNodeId`
- Viewport: `setViewport`
- Characters: `setCharacters`, `addCharacter`, `updateCharacter`, `removeCharacter`
- History: `pushHistory`, `undo`, `redo` (max 50 snapshots)
- Chat: `addChatMessage`, `clearChat`
- Timeline: `setTimelineOrder`, `reorderTimeline`
- ImagePool: `addToPool`, `removeFromPool`, `getFromPool`

- [ ] **Step 2: Verify TypeScript compiles**

Run: `cd frontend && npx tsc --noEmit --pretty 2>&1 | grep storyboardStore`
Expected: No errors

- [ ] **Step 3: Commit**

```bash
git add frontend/stores/storyboardStore.ts
git commit -m "feat(store): add Zustand storyboard store with undo/redo"
```

---

### Task 14: Custom Hooks

**Files:**
- Create: `frontend/hooks/storyboard/useStoryboardCanvas.ts`
- Create: `frontend/hooks/storyboard/useStoryboardPersist.ts`
- Create: `frontend/hooks/storyboard/useStoryboardRealtime.ts`
- Create: `frontend/hooks/storyboard/useAnimaticPlayer.ts`
- Create: `frontend/hooks/storyboard/useImagePool.ts`

- [ ] **Step 1: Write useStoryboardCanvas**

Canvas interaction: keyboard shortcuts, connection handling, node selection, double-click behaviors.

- [ ] **Step 2: Write useStoryboardPersist**

Debounced sync: 260ms for nodes/edges, 280ms for viewport. Uses `storyboardService.syncCanvas()` and `storyboardService.updateViewport()`.

- [ ] **Step 3: Write useStoryboardRealtime**

Supabase Realtime subscription to `unified_tasks` filtered by project_id and sb_ task_type.

- [ ] **Step 4: Write useAnimaticPlayer**

Playback state machine: play/pause/stop, frame sequencing, duration handling, speed control.

- [ ] **Step 5: Write useImagePool**

Reference counting, dedup by hash, cleanup on unmount.

- [ ] **Step 6: Commit**

```bash
git add frontend/hooks/storyboard/
git commit -m "feat(hooks): add storyboard custom hooks (canvas, persist, realtime, animatic, imagePool)"
```

---

### Task 15: Routing & Page Shell

**Files:**
- Create: `frontend/pages/StoryboardWorkbench/index.tsx`
- Create: `frontend/pages/StoryboardWorkbench/ProjectListPage.tsx`
- Create: `frontend/pages/StoryboardWorkbench/CanvasEditorPage.tsx`
- Modify: `frontend/router.tsx`

- [ ] **Step 1: Write page shell components**

`index.tsx` — switches between ProjectListPage and CanvasEditorPage based on `currentProjectId` in store.
`ProjectListPage.tsx` — placeholder with "Storyboard Workbench" title and empty grid.
`CanvasEditorPage.tsx` — placeholder with back button and empty canvas area.

- [ ] **Step 2: Add route to router.tsx**

Add under team scope: `{ path: 'storyboard', element: <ModuleGuard moduleKey="storyboard"><StoryboardWorkbench /></ModuleGuard> }`

- [ ] **Step 3: Verify route works**

Run: `cd frontend && npm run build 2>&1 | tail -5`
Expected: Build succeeds

- [ ] **Step 4: Commit**

```bash
git add frontend/pages/StoryboardWorkbench/ frontend/router.tsx
git commit -m "feat(pages): add storyboard workbench page shell with routing"
```

---

## Chunk 3: Canvas & Node Components

### Task 16: Canvas Core

**Files:**
- Create: `frontend/components/storyboard/canvas/StoryboardCanvas.tsx`
- Create: `frontend/components/storyboard/canvas/CanvasToolbar.tsx`
- Create: `frontend/components/storyboard/canvas/CanvasMiniMap.tsx`
- Create: `frontend/components/storyboard/canvas/NodeSelectionMenu.tsx`
- Create: `frontend/components/storyboard/canvas/edges/SmartEdge.tsx`

- [ ] **Step 1: Write StoryboardCanvas**

@xyflow/react ReactFlow component with: custom node types registry, edge types, onConnect, onNodesChange, onEdgesChange, onNodeClick, background dots, fitView.

- [ ] **Step 2: Write CanvasToolbar**

Top toolbar: add node dropdown, zoom controls, fit view, lock toggle, undo/redo, export dropdown, character panel toggle.

- [ ] **Step 3: Write CanvasMiniMap**

Wrapper around @xyflow/react MiniMap component.

- [ ] **Step 4: Write NodeSelectionMenu**

Dropdown menu at cursor position when connection dropped without target. Shows compatible node types with icons.

- [ ] **Step 5: Write SmartEdge**

Custom edge with orthogonal/spline routing options.

- [ ] **Step 6: Commit**

```bash
git add frontend/components/storyboard/canvas/
git commit -m "feat(canvas): add storyboard canvas core (ReactFlow, toolbar, minimap, edge)"
```

---

### Task 17: Node Components (Part 1 — Core Nodes)

**Files:**
- Create: `frontend/components/storyboard/nodes/shared/NodeWrapper.tsx`
- Create: `frontend/components/storyboard/nodes/shared/NodeControlStyles.ts`
- Create: `frontend/components/storyboard/nodes/shared/NodeImagePreview.tsx`
- Create: `frontend/components/storyboard/nodes/UploadNode.tsx`
- Create: `frontend/components/storyboard/nodes/ImageEditNode.tsx`
- Create: `frontend/components/storyboard/nodes/TextAnnotationNode.tsx`
- Create: `frontend/components/storyboard/nodes/GroupNode.tsx`

- [ ] **Step 1: Write shared components**

`NodeWrapper` — common shell with selected state, locked state, connection handles, hover toolbar.
`NodeControlStyles` — unified sizing for model selector, ratio dropdown, generate button.
`NodeImagePreview` — image display with click-to-enlarge, progress overlay.

- [ ] **Step 2: Write UploadNode**

Drag-and-drop image upload, displays preview, output handle.

- [ ] **Step 3: Write ImageEditNode**

Prompt input, model selector, aspect ratio, character selector (@mentions), generate button, progress bar.

- [ ] **Step 4: Write TextAnnotationNode and GroupNode**

TextAnnotation: editable text area.
Group: visual container for organizing nodes.

- [ ] **Step 5: Commit**

```bash
git add frontend/components/storyboard/nodes/
git commit -m "feat(nodes): add core node components (upload, imageEdit, text, group)"
```

---

### Task 18: Node Components (Part 2 — Storyboard Nodes)

**Files:**
- Create: `frontend/components/storyboard/nodes/StoryboardSplitNode.tsx`
- Create: `frontend/components/storyboard/nodes/StoryboardGenNode.tsx`
- Create: `frontend/components/storyboard/nodes/ImageToVideoNode.tsx`
- Create: `frontend/components/storyboard/nodes/ExportNode.tsx`

- [ ] **Step 1: Write StoryboardSplitNode**

Input: source image. Controls: rows/cols grid selector. Output: frame grid display with thumbnails, drag-to-reorder.

- [ ] **Step 2: Write StoryboardGenNode**

Multiple frame description inputs, batch generate button, model/provider selection, per-frame progress.

- [ ] **Step 3: Write ImageToVideoNode**

Source image input, motion prompt, provider selector (Kling/Runway/Vidu), duration, motion intensity, video preview.

- [ ] **Step 4: Write ExportNode**

Format selector (PNG/PDF/ZIP), options (include annotations, frame numbers), export button.

- [ ] **Step 5: Commit**

```bash
git add frontend/components/storyboard/nodes/
git commit -m "feat(nodes): add storyboard-specific nodes (split, gen, video, export)"
```

---

## Chunk 4: Panels & Tools

### Task 19: Character System

**Files:**
- Create: `frontend/components/storyboard/characters/CharacterPanel.tsx`
- Create: `frontend/components/storyboard/characters/CharacterCard.tsx`
- Create: `frontend/components/storyboard/characters/CharacterEditor.tsx`

- [ ] **Step 1: Write CharacterPanel**

Left slide-out panel. List of character cards. "New character" button.

- [ ] **Step 2: Write CharacterCard**

Avatar thumbnail, name, trait summary, usage count, edit/delete buttons.

- [ ] **Step 3: Write CharacterEditor**

Modal dialog: name, reference image upload, description textarea, visual traits (auto-extracted, editable).

- [ ] **Step 4: Commit**

```bash
git add frontend/components/storyboard/characters/
git commit -m "feat(characters): add character panel, card, and editor components"
```

---

### Task 20: Chat Panel

**Files:**
- Create: `frontend/components/storyboard/chat/ChatPanel.tsx`
- Create: `frontend/components/storyboard/chat/ChatMessage.tsx`
- Create: `frontend/components/storyboard/chat/ChatInput.tsx`

- [ ] **Step 1: Write ChatPanel**

Right collapsible panel. Message list. Shows "selected: frame N" indicator.

- [ ] **Step 2: Write ChatMessage**

Message bubble (user/AI). AI messages with action buttons (apply to frame, regenerate).

- [ ] **Step 3: Write ChatInput**

Input field with @mention autocomplete for character names. Send button.

- [ ] **Step 4: Commit**

```bash
git add frontend/components/storyboard/chat/
git commit -m "feat(chat): add AI chat panel with @mention support"
```

---

### Task 21: Timeline & Animatic Player

**Files:**
- Create: `frontend/components/storyboard/timeline/FrameTimeline.tsx`
- Create: `frontend/components/storyboard/timeline/FrameThumb.tsx`
- Create: `frontend/components/storyboard/timeline/AnimaticPlayer.tsx`

- [ ] **Step 1: Write FrameTimeline**

Bottom bar (collapsible). Horizontal frame thumbnail strip with drag-to-reorder. Play controls. Progress bar.

- [ ] **Step 2: Write FrameThumb**

Frame thumbnail with duration, shot type badge, transition indicator. Drag handle.

- [ ] **Step 3: Write AnimaticPlayer**

Playback engine: static frames by duration, video frames play actual video, transitions (cut/fade/dissolve), speed control, canvas sync on frame change.

- [ ] **Step 4: Commit**

```bash
git add frontend/components/storyboard/timeline/
git commit -m "feat(timeline): add frame timeline and animatic player"
```

---

### Task 22: Tools

**Files:**
- Create: `frontend/components/storyboard/tools/CropTool.tsx`
- Create: `frontend/components/storyboard/tools/AnnotateTool.tsx`
- Create: `frontend/components/storyboard/tools/SplitTool.tsx`
- Create: `frontend/components/storyboard/tools/ShotMetadataEditor.tsx`
- Create: `frontend/components/storyboard/tools/CameraOverlay.tsx`

- [ ] **Step 1: Write CropTool**

Aspect-ratio-aware image cropping with drag handles.

- [ ] **Step 2: Write AnnotateTool**

Konva canvas overlay: pen, line, rect, circle, text, arrow tools. Color/width controls. Save to annotations_json.

- [ ] **Step 3: Write SplitTool**

Grid size selector (rows x cols), preview overlay, execute split.

- [ ] **Step 4: Write ShotMetadataEditor**

Form: shot type, camera angle, camera movement, focal length, lighting, duration, transition type dropdowns.

- [ ] **Step 5: Write CameraOverlay**

Semi-transparent camera movement icons overlaid on frame images.

- [ ] **Step 6: Commit**

```bash
git add frontend/components/storyboard/tools/
git commit -m "feat(tools): add crop, annotate, split, shot metadata, camera overlay tools"
```

---

## Chunk 5: Project Management & Export

### Task 23: Project List Components

**Files:**
- Create: `frontend/components/storyboard/project/ProjectCard.tsx`
- Create: `frontend/components/storyboard/project/NewProjectDialog.tsx`
- Create: `frontend/components/storyboard/project/ScriptImportDialog.tsx`

- [ ] **Step 1: Write ProjectCard**

Card with cover thumbnail, name, frame count, character count, last modified. Hover menu (rename, duplicate, export, delete).

- [ ] **Step 2: Write NewProjectDialog**

Modal with 3 options: blank project, from script, from video. Name input.

- [ ] **Step 3: Write ScriptImportDialog**

Textarea for pasting script. Style guide input. Submit triggers split_script task.

- [ ] **Step 4: Commit**

```bash
git add frontend/components/storyboard/project/
git commit -m "feat(project): add project card, new project dialog, script import"
```

---

### Task 24: Export Components

**Files:**
- Create: `frontend/components/storyboard/export/ExportDialog.tsx`
- Create: `frontend/components/storyboard/export/ExportPreview.tsx`

- [ ] **Step 1: Write ExportDialog**

Format selection (PNG/PDF/ZIP), options (annotations, frame numbers, camera overlays).

- [ ] **Step 2: Write ExportPreview**

Preview of merged storyboard with selected options applied.

- [ ] **Step 3: Commit**

```bash
git add frontend/components/storyboard/export/
git commit -m "feat(export): add export dialog and preview components"
```

---

### Task 25: Shared Components & Image Pool

**Files:**
- Create: `frontend/components/storyboard/shared/ImageViewerModal.tsx`
- Create: `frontend/components/storyboard/shared/ImagePool.ts`

- [ ] **Step 1: Write ImageViewerModal**

Full-screen image viewer with zoom, pan, navigation arrows for multiple images.

- [ ] **Step 2: Write ImagePool**

Image pool utility: reference counting, SHA-256 dedup, URL management, cleanup.

- [ ] **Step 3: Commit**

```bash
git add frontend/components/storyboard/shared/
git commit -m "feat(shared): add image viewer modal and image pool utility"
```

---

## Chunk 6: Integration & Polish

### Task 26: Wire Up ProjectListPage

**Files:**
- Modify: `frontend/pages/StoryboardWorkbench/ProjectListPage.tsx`

- [ ] **Step 1: Integrate ProjectCard, NewProjectDialog, search/sort**

Replace placeholder with full implementation: fetch projects on mount, render grid of ProjectCards, wire NewProjectDialog, add search input and sort dropdown.

- [ ] **Step 2: Commit**

```bash
git add frontend/pages/StoryboardWorkbench/ProjectListPage.tsx
git commit -m "feat(pages): wire up storyboard project list with cards, search, sort"
```

---

### Task 27: Wire Up CanvasEditorPage

**Files:**
- Modify: `frontend/pages/StoryboardWorkbench/CanvasEditorPage.tsx`

- [ ] **Step 1: Integrate all canvas components**

Replace placeholder with full layout: StoryboardCanvas + CanvasToolbar + ChatPanel + FrameTimeline + CharacterPanel. Wire hooks (useStoryboardCanvas, useStoryboardPersist, useStoryboardRealtime).

- [ ] **Step 2: Commit**

```bash
git add frontend/pages/StoryboardWorkbench/CanvasEditorPage.tsx
git commit -m "feat(pages): wire up canvas editor with all panels and hooks"
```

---

### Task 28: i18n Translations

**Files:**
- Modify: `frontend/public/locales/en.json`
- Modify: `frontend/public/locales/zh.json`

- [ ] **Step 1: Add storyboard namespace translations**

Add all UI strings: page titles, button labels, node names, tool names, dialog texts, error messages.

- [ ] **Step 2: Commit**

```bash
git add frontend/public/locales/
git commit -m "feat(i18n): add storyboard workbench translations (en/zh)"
```

---

### Task 29: Final Build Verification

- [ ] **Step 1: Backend build check**

Run: `cd backend && uv run python -c "from app.main import app; print('Backend OK')"`
Expected: Backend OK

- [ ] **Step 2: Frontend build check**

Run: `cd frontend && npm run build`
Expected: Build succeeds with no errors

- [ ] **Step 3: Commit any fixes and tag**

```bash
git add -A
git commit -m "fix: resolve build issues from integration"
```

---

## Summary

| Chunk | Tasks | Focus |
|-------|-------|-------|
| 1 | Tasks 1-10 | Database + Backend (migration, schemas, repos, services, tasks, routers) |
| 2 | Tasks 11-15 | Frontend Foundation (deps, types, store, hooks, routing) |
| 3 | Tasks 16-18 | Canvas & Nodes (ReactFlow, 8 node types, toolbar) |
| 4 | Tasks 19-22 | Panels & Tools (characters, chat, timeline, annotation) |
| 5 | Tasks 23-25 | Project Management & Export (project list, export, shared) |
| 6 | Tasks 26-29 | Integration & Polish (wire up pages, i18n, build verification) |

**Total: 29 tasks across 6 chunks**

**Estimated new files: ~50 files**
**Estimated modified files: ~5 files**
