# Script Editor v2 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Upgrade Script Editor to match Storyboard Copilot v0.2.1 — TipTap rich text, constrained vertical layout, import/export, AI enhancements.

**Architecture:** Retrofit existing ReactFlow canvas with dagre constrained layout, replace ChapterNode with TipTap-embedded ChapterFlowNode, add StoryRootNode and BranchNode types. Backend adds content_json field, import/export APIs, and AI prompt enhancements for HTML output.

**Tech Stack:** TipTap (@tiptap/react), dagre, ReactFlow (@xyflow/react), python-docx, pypdf, bleach

**Spec:** `docs/superpowers/specs/2026-04-04-script-editor-v2-design.md`

---

## Phase 1: Foundation — Database + TipTap Setup

### Task 1: Database Migration

**Files:**
- Create: `supabase/migrations/116_script_editor_v2.sql`

- [ ] **Step 1: Write migration SQL**

```sql
-- 116_script_editor_v2.sql
-- Script Editor v2: add content_json and genre fields

-- Add JSONB field for TipTap document structure (source of truth)
ALTER TABLE script_chapters
  ADD COLUMN IF NOT EXISTS content_json JSONB;

-- Add genre field for story style
ALTER TABLE script_projects
  ADD COLUMN IF NOT EXISTS genre VARCHAR(50);

-- Backfill: wrap existing content as basic TipTap JSON
UPDATE script_chapters
SET content_json = jsonb_build_object(
  'type', 'doc',
  'content', jsonb_build_array(
    jsonb_build_object(
      'type', 'paragraph',
      'content', jsonb_build_array(
        jsonb_build_object('type', 'text', 'text', COALESCE(content, ''))
      )
    )
  )
)
WHERE content_json IS NULL AND content IS NOT NULL AND content != '';

COMMENT ON COLUMN script_chapters.content_json IS 'TipTap ProseMirror JSON document (source of truth)';
COMMENT ON COLUMN script_chapters.content IS 'Plain text derived from content_json (for search and AI)';
COMMENT ON COLUMN script_projects.genre IS 'Story genre/style (e.g. 科幻, 悬疑, 爱情)';
```

- [ ] **Step 2: Execute migration via Supabase MCP**

Run the migration SQL via Supabase MCP `execute_sql` tool.

- [ ] **Step 3: Commit**

```bash
git add supabase/migrations/116_script_editor_v2.sql
git commit -m "feat: add content_json and genre fields for Script Editor v2"
```

---

### Task 2: Install TipTap Dependencies

**Files:**
- Modify: `frontend/package.json`

- [ ] **Step 1: Install TipTap packages**

```bash
cd frontend && npm install @tiptap/react @tiptap/starter-kit @tiptap/extension-placeholder @tiptap/extension-heading @tiptap/extension-bold @tiptap/extension-italic @tiptap/extension-horizontal-rule
```

- [ ] **Step 2: Verify build**

```bash
cd frontend && npm run build
```

Expected: Build succeeds with no errors.

- [ ] **Step 3: Commit**

```bash
git add frontend/package.json frontend/package-lock.json
git commit -m "chore: add TipTap dependencies for Script Editor v2"
```

---

### Task 3: TipTap Custom Extensions — SceneHeading

**Files:**
- Create: `frontend/features/script/extensions/SceneHeading.ts`

- [ ] **Step 1: Create SceneHeading TipTap extension**

```typescript
// frontend/features/script/extensions/SceneHeading.ts
import { Node, mergeAttributes } from '@tiptap/core';

/**
 * SceneHeading — renders as <h2> with amber color in editor.
 * Format: 场景N：场景名 – 时间 – 内/外景
 */
export const SceneHeading = Node.create({
  name: 'sceneHeading',
  group: 'block',
  content: 'inline*',
  defining: true,

  parseHTML() {
    return [
      {
        tag: 'h2',
        // Only match h2 elements that look like scene headings
        getAttrs: (element) => {
          const text = (element as HTMLElement).textContent || '';
          return /^场景\s*\d/.test(text) ? {} : false;
        },
      },
    ];
  },

  renderHTML({ HTMLAttributes }) {
    return ['h2', mergeAttributes(HTMLAttributes, { class: 'scene-heading' }), 0];
  },
});
```

- [ ] **Step 2: Commit**

```bash
git add frontend/features/script/extensions/SceneHeading.ts
git commit -m "feat: add SceneHeading TipTap extension"
```

---

### Task 4: TipTap Custom Extensions — Dialogue

**Files:**
- Create: `frontend/features/script/extensions/Dialogue.ts`

- [ ] **Step 1: Create Dialogue TipTap extension**

```typescript
// frontend/features/script/extensions/Dialogue.ts
import { Node, mergeAttributes } from '@tiptap/core';

/**
 * Dialogue — renders as <p> with amber color, character name bold.
 * Format: 角色名：（动作描述）台词内容
 */
export const Dialogue = Node.create({
  name: 'dialogue',
  group: 'block',
  content: 'inline*',
  defining: true,

  parseHTML() {
    return [
      {
        tag: 'p',
        getAttrs: (element) => {
          const el = element as HTMLElement;
          // Match paragraphs that start with <strong>CharName</strong>：
          const strong = el.querySelector('strong');
          if (!strong) return false;
          const text = el.textContent || '';
          return /：/.test(text) ? {} : false;
        },
      },
    ];
  },

  renderHTML({ HTMLAttributes }) {
    return ['p', mergeAttributes(HTMLAttributes, { class: 'dialogue-line' }), 0];
  },
});
```

- [ ] **Step 2: Commit**

```bash
git add frontend/features/script/extensions/Dialogue.ts
git commit -m "feat: add Dialogue TipTap extension"
```

---

### Task 5: ScriptTipTapEditor Component

**Files:**
- Create: `frontend/features/script/components/ScriptTipTapEditor.tsx`
- Create: `frontend/features/script/extensions/index.ts`

- [ ] **Step 1: Create extensions barrel export**

```typescript
// frontend/features/script/extensions/index.ts
export { SceneHeading } from './SceneHeading';
export { Dialogue } from './Dialogue';
```

- [ ] **Step 2: Create ScriptTipTapEditor component**

```tsx
// frontend/features/script/components/ScriptTipTapEditor.tsx
import { useEditor, EditorContent } from '@tiptap/react';
import StarterKit from '@tiptap/starter-kit';
import Placeholder from '@tiptap/extension-placeholder';
import { useCallback, useEffect } from 'react';
import { SceneHeading, Dialogue } from '../extensions';

interface ScriptTipTapEditorProps {
  contentJson: Record<string, unknown> | null;
  onUpdate: (json: Record<string, unknown>, html: string) => void;
  placeholder?: string;
  editable?: boolean;
}

export function ScriptTipTapEditor({
  contentJson,
  onUpdate,
  placeholder = 'Start writing...',
  editable = true,
}: ScriptTipTapEditorProps) {
  const editor = useEditor({
    extensions: [
      StarterKit.configure({
        heading: { levels: [1, 2, 3, 4] },
      }),
      Placeholder.configure({ placeholder }),
      SceneHeading,
      Dialogue,
    ],
    content: contentJson || undefined,
    editable,
    onUpdate: ({ editor }) => {
      const json = editor.getJSON();
      const html = editor.getHTML();
      onUpdate(json as Record<string, unknown>, html);
    },
  });

  // Sync external content changes (e.g. AI expansion result)
  useEffect(() => {
    if (editor && contentJson && !editor.isFocused) {
      const currentJson = JSON.stringify(editor.getJSON());
      const newJson = JSON.stringify(contentJson);
      if (currentJson !== newJson) {
        editor.commands.setContent(contentJson);
      }
    }
  }, [editor, contentJson]);

  const handleKeyDown = useCallback((e: React.KeyboardEvent) => {
    // Prevent ReactFlow from intercepting keyboard events
    e.stopPropagation();
  }, []);

  if (!editor) return null;

  return (
    <div
      className="script-tiptap-editor"
      onKeyDown={handleKeyDown}
      onMouseDown={(e) => e.stopPropagation()}
    >
      {/* Toolbar */}
      {editable && (
        <div className="flex gap-0.5 px-3 py-1.5 border-b border-zinc-800">
          {([1, 2, 3, 4] as const).map((level) => (
            <button
              key={level}
              type="button"
              className={`px-2 py-0.5 rounded text-xs ${
                editor.isActive('heading', { level })
                  ? 'bg-indigo-600 text-white'
                  : 'text-zinc-400 hover:bg-zinc-800'
              }`}
              onClick={() => editor.chain().focus().toggleHeading({ level }).run()}
            >
              H{level}
            </button>
          ))}
          <button
            type="button"
            className={`px-2 py-0.5 rounded text-xs ${
              editor.isActive('paragraph') && !editor.isActive('heading')
                ? 'bg-indigo-600 text-white'
                : 'text-zinc-400 hover:bg-zinc-800'
            }`}
            onClick={() => editor.chain().focus().setParagraph().run()}
          >
            Body
          </button>
          <span className="w-px h-4 bg-zinc-700 mx-1 self-center" />
          <button
            type="button"
            className={`px-2 py-0.5 rounded text-xs font-bold ${
              editor.isActive('bold') ? 'bg-indigo-600 text-white' : 'text-zinc-400 hover:bg-zinc-800'
            }`}
            onClick={() => editor.chain().focus().toggleBold().run()}
          >
            B
          </button>
          <button
            type="button"
            className={`px-2 py-0.5 rounded text-xs italic ${
              editor.isActive('italic') ? 'bg-indigo-600 text-white' : 'text-zinc-400 hover:bg-zinc-800'
            }`}
            onClick={() => editor.chain().focus().toggleItalic().run()}
          >
            I
          </button>
        </div>
      )}
      <EditorContent
        editor={editor}
        className="px-4 py-3 min-h-[120px] prose prose-invert prose-sm max-w-none
          [&_.scene-heading]:text-amber-500 [&_.scene-heading]:font-semibold [&_.scene-heading]:text-base [&_.scene-heading]:my-3
          [&_.dialogue-line]:text-amber-500 [&_.dialogue-line]:my-1
          [&_.dialogue-line_strong]:font-semibold"
      />
    </div>
  );
}
```

- [ ] **Step 3: Verify build**

```bash
cd frontend && npm run build
```

- [ ] **Step 4: Commit**

```bash
git add frontend/features/script/extensions/index.ts frontend/features/script/components/ScriptTipTapEditor.tsx
git commit -m "feat: add ScriptTipTapEditor component with toolbar and custom extensions"
```

---

## Phase 2: Canvas Architecture — Node Types + Layout

### Task 6: Update Store — Add content_json and View Mode

**Files:**
- Modify: `frontend/stores/scriptCanvasStore.ts`

- [ ] **Step 1: Update ChapterNodeData interface and store**

Add `contentJson` field to `ChapterNodeData`, add `viewMode` state, and add `editingNodeId` for TipTap virtualization:

```typescript
// Add to ChapterNodeData interface:
contentJson: Record<string, unknown> | null;

// Add to ScriptCanvasState:
viewMode: 'canvas' | 'grid' | 'list';
editingNodeId: string | null;
setViewMode: (mode: 'canvas' | 'grid' | 'list') => void;
setEditingNodeId: (nodeId: string | null) => void;
```

Update `addChapterNode` to initialize `contentJson: null`.

- [ ] **Step 2: Commit**

```bash
git add frontend/stores/scriptCanvasStore.ts
git commit -m "feat: add contentJson, viewMode, editingNodeId to script canvas store"
```

---

### Task 7: ChapterFlowNode — Replace ChapterNode

**Files:**
- Create: `frontend/features/script/nodes/ChapterFlowNode.tsx`
- Modify: `frontend/features/script/nodes/index.ts`

- [ ] **Step 1: Create ChapterFlowNode**

New node component that embeds ScriptTipTapEditor when `editingNodeId === id`, otherwise renders static HTML. Full structure:
- Chapter label (file icon + "第N章 标题")
- Number badge (purple) + editable title
- TipTap editor OR static HTML content
- Summary row + AI expand button (sparkles icon)
- Create branch button (purple gradient)
- Green connector handle at bottom

Key details:
- `draggable={false}` for main chapters (constrained layout)
- `onKeyDown` and `onMouseDown` stopPropagation on editor area
- `onWheel` stopPropagation to prevent canvas zoom during content scroll
- Click on content area sets `editingNodeId`

- [ ] **Step 2: Update nodes index to export ChapterFlowNode**

```typescript
// frontend/features/script/nodes/index.ts
export { ChapterFlowNode } from './ChapterFlowNode';
// Keep ChapterNode export for backward compatibility during migration
export { ChapterNode } from './ChapterNode';
```

- [ ] **Step 3: Register in ScriptCanvas**

Update `frontend/features/script/ScriptCanvas.tsx` to use `ChapterFlowNode` as the node type:

```typescript
const nodeTypes = useMemo(() => ({
  chapter: ChapterFlowNode,
  branch: BranchNode,      // Task 8
  storyRoot: StoryRootNode, // Task 9
}), []);
```

Also set `deleteKeyCode={null}` on the ReactFlow component.

- [ ] **Step 4: Commit**

```bash
git add frontend/features/script/nodes/ChapterFlowNode.tsx frontend/features/script/nodes/index.ts frontend/features/script/ScriptCanvas.tsx
git commit -m "feat: add ChapterFlowNode with TipTap virtualization"
```

---

### Task 8: BranchNode

**Files:**
- Create: `frontend/features/script/nodes/BranchNode.tsx`

- [ ] **Step 1: Create BranchNode**

Similar to ChapterFlowNode but more compact:
- "Supplement" label with file-plus icon
- Number badge + title
- TipTap editor (also virtualized via editingNodeId)
- `draggable={true}` (branches can be freely positioned)
- Connected via purple curved edge from source chapter

- [ ] **Step 2: Commit**

```bash
git add frontend/features/script/nodes/BranchNode.tsx
git commit -m "feat: add BranchNode for story branches/supplements"
```

---

### Task 9: StoryRootNode

**Files:**
- Create: `frontend/features/script/nodes/StoryRootNode.tsx`

- [ ] **Step 1: Create StoryRootNode**

Small card displaying:
- Book icon + story name
- Chapter count + genre tag (purple badge)
- Not editable, not draggable
- Source handle on right side, connects to first chapter

- [ ] **Step 2: Commit**

```bash
git add frontend/features/script/nodes/StoryRootNode.tsx
git commit -m "feat: add StoryRootNode card"
```

---

### Task 10: dagre Constrained Layout

**Files:**
- Create: `frontend/features/script/layout/dagreLayout.ts`
- Modify: `frontend/features/script/ScriptCanvas.tsx`

- [ ] **Step 1: Install dagre**

```bash
cd frontend && npm install @dagrejs/dagre
```

- [ ] **Step 2: Create layout function**

```typescript
// frontend/features/script/layout/dagreLayout.ts
import dagre from '@dagrejs/dagre';
import type { ScriptNode, ScriptEdge } from '../../../stores/scriptCanvasStore';

const NODE_WIDTH = 580;
const NODE_HEIGHT = 300;
const NODE_GAP = 80;

export function applyDagreLayout(
  nodes: ScriptNode[],
  edges: ScriptEdge[],
): ScriptNode[] {
  const g = new dagre.graphlib.Graph();
  g.setDefaultEdgeLabel(() => ({}));
  g.setGraph({ rankdir: 'TB', nodesep: NODE_GAP, ranksep: NODE_GAP });

  // Only layout main chapters (not branches, not storyRoot)
  const mainChapters = nodes.filter(
    (n) => n.type === 'chapter' && !n.data.branchType
  );
  const otherNodes = nodes.filter(
    (n) => n.type !== 'chapter' || n.data.branchType
  );

  for (const node of mainChapters) {
    g.setNode(node.id, { width: NODE_WIDTH, height: NODE_HEIGHT });
  }
  for (const edge of edges) {
    if (g.hasNode(edge.source) && g.hasNode(edge.target)) {
      g.setEdge(edge.source, edge.target);
    }
  }

  dagre.layout(g);

  const layoutedMain = mainChapters.map((node) => {
    const pos = g.node(node.id);
    return {
      ...node,
      position: { x: pos.x - NODE_WIDTH / 2, y: pos.y - NODE_HEIGHT / 2 },
    };
  });

  return [...layoutedMain, ...otherNodes];
}
```

- [ ] **Step 3: Integrate into ScriptCanvas**

Call `applyDagreLayout` on initial load and when chapters are added/removed. Add a "Re-layout" button in the toolbar.

- [ ] **Step 4: Commit**

```bash
git add frontend/features/script/layout/dagreLayout.ts frontend/package.json frontend/package-lock.json
git commit -m "feat: add dagre constrained vertical layout for main chapters"
```

---

### Task 11: ViewControls Component

**Files:**
- Create: `frontend/features/script/components/ViewControls.tsx`

- [ ] **Step 1: Create ViewControls**

Bottom-right overlay with:
- Grid icon (grid-2x2) — switches to grid view
- List icon (list) — switches to list view
- Canvas icon (square) — switches to canvas view (default, highlighted)
- Separator
- Minus button — zoom out
- Zoom percentage display
- Plus button — zoom in

Uses `useScriptCanvasStore` for `viewMode`/`setViewMode` and ReactFlow's `useReactFlow` for zoom controls.

- [ ] **Step 2: Add to ScriptEditorPage layout**

Render `<ViewControls />` as absolute-positioned overlay in bottom-right of canvas area.

- [ ] **Step 3: Commit**

```bash
git add frontend/features/script/components/ViewControls.tsx
git commit -m "feat: add ViewControls with zoom and view mode switching"
```

---

## Phase 3: AI Enhancements

### Task 12: Backend — Add genre and expansion_request to AI Service

**Files:**
- Modify: `backend/app/services/script_ai_service.py`
- Modify: `backend/app/schemas/script.py`

- [ ] **Step 1: Update generate_outline to accept genre**

Add `genre: Optional[str] = None` parameter to `generate_outline()`. Include genre in the system prompt when provided.

- [ ] **Step 2: Update expand_chapter for HTML output + expansion_request**

Add `expansion_request: Optional[str] = None` parameter to `expand_chapter()`. Change the system prompt to instruct the LLM to output HTML format:

```python
system_prompt = """You are a professional screenplay writer. Expand the chapter summary into full screenplay content.

OUTPUT FORMAT (mandatory):
- Scene headings: <h2>场景N：场景名 – 时间 – 内/外景</h2>
- Action/description: <p>paragraph text</p>
- Character dialogue: <p><strong>角色名</strong>：（动作描述）台词内容</p>
- Scene separator: <hr>
- Do NOT wrap in any outer tags. Output raw HTML fragments only.
"""
```

- [ ] **Step 3: Add HTML sanitization**

Install bleach: `cd backend && uv add bleach`

Create a sanitize function:

```python
import bleach

ALLOWED_TAGS = ['h2', 'h3', 'p', 'strong', 'em', 'hr', 'br']

def sanitize_ai_html(html: str) -> str:
    return bleach.clean(html, tags=ALLOWED_TAGS, strip=True)
```

Apply `sanitize_ai_html()` to AI output before returning.

- [ ] **Step 4: Update content derivation in script_service.py**

In `ScriptService.update_chapter()` and `sync_canvas()`, when `content_json` is provided, extract plain text and write to `content`:

```python
from bs4 import BeautifulSoup

def _extract_text_from_json(content_json: dict) -> str:
    """Extract plain text from TipTap JSON for search/AI usage."""
    # Convert JSON to HTML via simple traversal, then strip tags
    # ... implementation
```

- [ ] **Step 5: Commit**

```bash
git add backend/app/services/script_ai_service.py backend/app/services/script_service.py backend/app/schemas/script.py backend/pyproject.toml
git commit -m "feat: enhance AI service — genre, expansion_request, HTML output, sanitization"
```

---

### Task 13: Frontend — Enhanced CreateStoryDialog

**Files:**
- Modify: `frontend/features/script/CreateStoryDialog.tsx`

- [ ] **Step 1: Add genre selector and advanced settings**

Enhance dialog with:
- Story concept textarea (existing)
- Chapter count slider (1-20, default 5)
- Collapsible "Advanced Settings" section:
  - Genre dropdown: Not specified / Suspense / Romance / Sci-Fi / Fantasy / Historical / Modern Urban / Comedy / Tragedy / Action Adventure / Horror / Custom...
  - Chapter count display (red number)

- [ ] **Step 2: Add outline result view**

After AI generates outline, show result dialog:
- Story concept echo
- Story name (editable) + Regenerate button
- Chapter outline list (number + title + summary)
- Confirm Create Outline button

- [ ] **Step 3: Commit**

```bash
git add frontend/features/script/CreateStoryDialog.tsx
git commit -m "feat: enhance CreateStoryDialog with genre selection and outline preview"
```

---

### Task 14: Frontend — Enhanced ExpandChapterDialog

**Files:**
- Modify: `frontend/features/script/ExpandChapterDialog.tsx`

- [ ] **Step 1: Add expansion_request and result preview**

Enhance dialog:
- Original text (read-only, showing chapter summary)
- Expansion request input (optional): "e.g. add more dialogue, intensify conflict..."
- Generated result area: shows AI-returned HTML rendered as preview
- Cancel / Confirm Replace buttons

When "Confirm Replace" is clicked:
1. Parse AI HTML into TipTap JSON via `editor.commands.setContent(html)`
2. Update `contentJson` in store
3. Trigger auto-save

- [ ] **Step 2: Commit**

```bash
git add frontend/features/script/ExpandChapterDialog.tsx
git commit -m "feat: enhance ExpandChapterDialog with custom request and result preview"
```

---

### Task 15: WelcomeScreen

**Files:**
- Create: `frontend/features/script/components/WelcomeScreen.tsx`
- Modify: `frontend/pages/ScriptEditor/ScriptEditorPage.tsx`

- [ ] **Step 1: Create WelcomeScreen component**

Two-card layout:
- "Import Script" card — upload icon, "Import from TXT, PDF, Word files"
- "Create Story" card — sparkles icon, "Enter story concept, AI generates outline"
- "Back to project" link at bottom

Show WelcomeScreen when script project has 0 chapters.

- [ ] **Step 2: Integrate into ScriptEditorPage**

Conditionally render WelcomeScreen or ScriptCanvas based on chapter count.

- [ ] **Step 3: Commit**

```bash
git add frontend/features/script/components/WelcomeScreen.tsx frontend/pages/ScriptEditor/ScriptEditorPage.tsx
git commit -m "feat: add WelcomeScreen with import/create story options"
```

---

## Phase 4: Import & Export

### Task 16: Backend — Import API

**Files:**
- Create: `backend/app/api/script_import_router.py`
- Create: `backend/app/services/script_import_service.py`
- Modify: `backend/app/main.py` (register router)

- [ ] **Step 1: Create import service**

```python
# backend/app/services/script_import_service.py
import pypdf
import docx
from typing import List, Dict
from app.services.script_ai_service import ScriptAIService

MAX_FILE_SIZE = 10 * 1024 * 1024  # 10MB
MAX_PDF_PAGES = 200
MAX_CHAR_COUNT = 500_000

ALLOWED_MIME_TYPES = {
    'text/plain',
    'application/pdf',
    'application/vnd.openxmlformats-officedocument.wordprocessingml.document',
}

class ScriptImportService:
    async def parse_file(self, file_bytes: bytes, filename: str, mime_type: str) -> str:
        """Parse file content to plain text."""
        # ... implementation for txt/pdf/docx

    async def split_into_chapters(self, text: str, genre: str | None = None) -> List[Dict]:
        """Use AI to split text into chapters."""
        # ... call ScriptAIService for intelligent chapter splitting
```

- [ ] **Step 2: Create import router (async task-based)**

```python
# backend/app/api/script_import_router.py
@router.post("/import")
async def import_script(
    file: UploadFile,
    script_id: str = Form(...),
    # ... returns task_id for async processing
):
    # Validate file size, MIME type
    # Create unified_task
    # Dispatch Celery task for parsing + AI splitting
    # Return task_id
```

- [ ] **Step 3: Register router in main.py**

- [ ] **Step 4: Commit**

```bash
git add backend/app/services/script_import_service.py backend/app/api/script_import_router.py backend/app/main.py
git commit -m "feat: add script import API with file parsing and AI chapter splitting"
```

---

### Task 17: Backend — Export API

**Files:**
- Create: `backend/app/api/script_export_router.py`
- Create: `backend/app/services/script_export_service.py`
- Modify: `backend/app/main.py` (register router)

- [ ] **Step 1: Create export service**

```python
# backend/app/services/script_export_service.py
import io
from docx import Document
from docx.shared import Pt, RGBColor
import json

class ScriptExportService:
    def export_txt(self, project, chapters) -> str: ...
    def export_markdown(self, project, chapters) -> str: ...
    def export_json(self, project, chapters) -> dict: ...
    def export_docx(self, project, chapters) -> io.BytesIO:
        """Generate Word document with proper formatting:
        - Title: centered, large
        - Genre: centered subtitle
        - Chapter names: blue heading style
        - Scene headings: blue italic
        - Character dialogue: bold character name + regular text
        - Horizontal rules between dialogue groups
        """
        ...
```

- [ ] **Step 2: Create export router**

```python
# backend/app/api/script_export_router.py
@router.get("/{script_id}/export")
async def export_script(
    script_id: str,
    format: str = Query(..., regex="^(txt|docx|json|md)$"),
    branch_id: str | None = Query(None),
):
    # Fetch project + chapters (filtered by branch if specified)
    # Generate file in requested format
    # Return StreamingResponse with Content-Disposition: attachment
```

- [ ] **Step 3: Register router in main.py**

- [ ] **Step 4: Commit**

```bash
git add backend/app/services/script_export_service.py backend/app/api/script_export_router.py backend/app/main.py
git commit -m "feat: add script export API — TXT, Word, JSON, Markdown formats"
```

---

### Task 18: Frontend — ImportScriptDialog

**Files:**
- Create: `frontend/features/script/components/ImportScriptDialog.tsx`
- Modify: `frontend/services/scriptService.ts`

- [ ] **Step 1: Add import/export API functions to scriptService**

```typescript
export async function importScript(scriptId: string, file: File): Promise<{ task_id: string }> {
  const formData = new FormData();
  formData.append('file', file);
  formData.append('script_id', scriptId);
  const resp = await fetch(`${API_BASE}/api/v1/scripts/import`, {
    method: 'POST',
    headers: await getAuthHeaders(),
    body: formData,
  });
  return resp.json();
}

export function getExportUrl(scriptId: string, format: string, branchId?: string): string {
  const params = new URLSearchParams({ format });
  if (branchId) params.set('branch_id', branchId);
  return `${API_BASE}/api/v1/scripts/${scriptId}/export?${params}`;
}
```

- [ ] **Step 2: Create ImportScriptDialog**

File upload dialog with:
- Drag-and-drop zone
- File type filter (.txt, .pdf, .docx)
- Upload progress bar
- Parsing status (via task polling)
- Chapter preview list after completion
- Confirm Import button

- [ ] **Step 3: Commit**

```bash
git add frontend/features/script/components/ImportScriptDialog.tsx frontend/services/scriptService.ts
git commit -m "feat: add ImportScriptDialog and import/export API functions"
```

---

### Task 19: Frontend — ExportDialog

**Files:**
- Create: `frontend/features/script/components/ExportDialog.tsx`

- [ ] **Step 1: Create ExportDialog**

Two-step dialog:
1. Branch selection (if branches exist, otherwise skip)
2. Format selection list:
   - Export as TXT (file-text icon)
   - Export as Word .docx (file-type icon, blue)
   - Export as JSON (braces icon)
   - Export as Markdown (file-code icon)

Clicking a format triggers file download via `window.open(getExportUrl(...))`.

- [ ] **Step 2: Commit**

```bash
git add frontend/features/script/components/ExportDialog.tsx
git commit -m "feat: add ExportDialog with branch selection and format options"
```

---

## Phase 5: Views + Sidebar Polish

### Task 20: GridView

**Files:**
- Create: `frontend/features/script/views/GridView.tsx`

- [ ] **Step 1: Create GridView**

Grid layout of chapter cards (2-3 columns):
- Chapter number badge + title
- Summary text (truncated)
- Click navigates to chapter in canvas view

- [ ] **Step 2: Commit**

```bash
git add frontend/features/script/views/GridView.tsx
git commit -m "feat: add GridView for chapter overview"
```

---

### Task 21: ListView

**Files:**
- Create: `frontend/features/script/views/ListView.tsx`

- [ ] **Step 1: Create ListView**

Vertical list of chapters:
- Chapter number + title + summary
- Compact view for quick scanning
- Click navigates to chapter in canvas view

- [ ] **Step 2: Commit**

```bash
git add frontend/features/script/views/ListView.tsx
git commit -m "feat: add ListView for chapter browsing"
```

---

### Task 22: ScriptAssetsSidebar Style Alignment

**Files:**
- Modify: `frontend/features/script/ScriptAssetsSidebar.tsx`

- [ ] **Step 1: Update sidebar to match reference app**

- Add top toolbar: sparkles (AI), download (export), copy, panel-left-close (collapse)
- Chapter list in "Plot Summary" group shows branches as indented sub-items with sparkles icon
- Click chapter scrolls canvas to that node
- Collapsible groups with chevron-down/chevron-right
- Each group header has a "+" button for adding items
- Match dark theme styling (bg-zinc-900 borders, zinc-800 hover)

- [ ] **Step 2: Wire export button to ExportDialog**

- [ ] **Step 3: Commit**

```bash
git add frontend/features/script/ScriptAssetsSidebar.tsx
git commit -m "feat: align ScriptAssetsSidebar with reference app style"
```

---

### Task 23: Auto-Save Integration

**Files:**
- Modify: `frontend/features/script/ScriptCanvas.tsx`
- Modify: `frontend/stores/scriptCanvasStore.ts`

- [ ] **Step 1: Add debounced auto-save**

In ScriptCanvas, watch for node data changes and trigger `syncScriptCanvas()` with 500ms debounce. Show save status indicator (editing/saving/saved) in toolbar.

- [ ] **Step 2: Add localStorage fallback**

On save failure, store pending changes in `localStorage` keyed by script ID. On next successful load, check for pending changes and sync them.

- [ ] **Step 3: Commit**

```bash
git add frontend/features/script/ScriptCanvas.tsx frontend/stores/scriptCanvasStore.ts
git commit -m "feat: add auto-save with debounce and offline fallback"
```

---

## Phase 6: Format Presets + Final Integration

### Task 24: Backend — Format Preset in Settings

**Files:**
- Modify: `backend/app/services/script_service.py`

- [ ] **Step 1: Add default format preset on project creation**

When creating a script project, initialize `settings_json` with default format preset:

```python
DEFAULT_FORMAT_PRESET = {
    "format_preset": {
        "scene_heading": "场景N：场景名 – 时间 – 内/外景",
        "dialogue": "角色名：（动作描述）台词内容",
        "scene_separator": "hr",
        "action": "paragraph",
        "voiceover": "italic",
    }
}
```

- [ ] **Step 2: Pass format preset to AI service**

When calling `expand_chapter()`, read `format_preset` from project settings and include in the system prompt.

- [ ] **Step 3: Commit**

```bash
git add backend/app/services/script_service.py
git commit -m "feat: add default format preset and pass to AI service"
```

---

### Task 25: Frontend — ScriptEditorPage Integration

**Files:**
- Modify: `frontend/pages/ScriptEditor/ScriptEditorPage.tsx`

- [ ] **Step 1: Wire everything together**

Final integration of all components in ScriptEditorPage:
- Three-column layout: sidebar | canvas | (overlay: ViewControls)
- Conditional rendering: WelcomeScreen (0 chapters) | Canvas/Grid/List (based on viewMode)
- Import/Export dialogs triggered from sidebar toolbar
- CreateStoryDialog triggered from WelcomeScreen or toolbar
- Auto-save status in header

- [ ] **Step 2: Verify full flow**

Manual test:
1. Navigate to project → Scripts → New Script → WelcomeScreen appears
2. Click "Create Story" → Fill concept + genre → Generate → Confirm → Canvas with chapters
3. Click chapter → TipTap activates → Edit content → Auto-saves
4. Click AI expand → Fill request → Generate → Confirm Replace
5. Export → Select format → Download file

- [ ] **Step 3: Commit**

```bash
git add frontend/pages/ScriptEditor/ScriptEditorPage.tsx
git commit -m "feat: integrate Script Editor v2 — full page assembly"
```

---

### Task 26: CSS — Script Editor Styles

**Files:**
- Create: `frontend/features/script/script-editor.css`

- [ ] **Step 1: Add TipTap and node-specific styles**

```css
/* Scene headings — amber in editor */
.script-tiptap-editor .scene-heading {
  color: #f59e0b;
  font-weight: 600;
  font-size: 1rem;
  margin: 0.75rem 0 0.5rem;
}

/* Dialogue lines — amber with bold character name */
.script-tiptap-editor .dialogue-line {
  color: #f59e0b;
  margin: 0.25rem 0;
}

.script-tiptap-editor .dialogue-line strong {
  font-weight: 600;
}

/* Placeholder */
.script-tiptap-editor .tiptap p.is-editor-empty:first-child::before {
  color: #52525b;
  content: attr(data-placeholder);
  float: left;
  height: 0;
  pointer-events: none;
}

/* Connector between chapters */
.chapter-connector {
  width: 12px;
  height: 12px;
  background: #22c55e;
  border-radius: 50%;
}
```

- [ ] **Step 2: Import in ScriptEditorPage**

```typescript
import '../features/script/script-editor.css';
```

- [ ] **Step 3: Commit**

```bash
git add frontend/features/script/script-editor.css frontend/pages/ScriptEditor/ScriptEditorPage.tsx
git commit -m "feat: add Script Editor CSS — scene heading amber, dialogue styling"
```

---

### Task 27: Backend — Install New Dependencies

**Files:**
- Modify: `backend/pyproject.toml`

- [ ] **Step 1: Add dependencies**

```bash
cd backend && uv add python-docx pypdf bleach
```

- [ ] **Step 2: Verify**

```bash
cd backend && uv sync && uv run python -c "import docx; import pypdf; import bleach; print('OK')"
```

- [ ] **Step 3: Commit**

```bash
git add backend/pyproject.toml backend/uv.lock
git commit -m "chore: add python-docx, pypdf, bleach dependencies"
```

---

## Summary

| Phase | Tasks | Key Deliverable |
|-------|-------|-----------------|
| 1: Foundation | 1-5 | DB migration + TipTap editor + custom extensions |
| 2: Canvas | 6-11 | ChapterFlowNode + BranchNode + StoryRootNode + dagre + ViewControls |
| 3: AI | 12-15 | Genre selection + HTML output + expand request + WelcomeScreen |
| 4: Import/Export | 16-19 | File import (TXT/PDF/Word) + export (TXT/Word/JSON/MD) |
| 5: Views + Polish | 20-23 | GridView + ListView + sidebar alignment + auto-save |
| 6: Integration | 24-27 | Format presets + full page assembly + CSS + dependencies |

Total: 27 tasks across 6 phases. Each phase is independently testable.
