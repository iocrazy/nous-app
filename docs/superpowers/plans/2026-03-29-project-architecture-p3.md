# P3: AI Expansion + Branching System — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add AI-powered chapter expansion (summary → full content) and story branching (generate 2-4 alternative plot paths) to the Script Editor, plus upgrade the P2 outline generator from local placeholder to real LLM calls.

**Architecture:** Follows the existing storyboard AI pattern: backend service with `_call_llm()` using OpenAI-compatible API, Celery tasks for outline generation (async), synchronous endpoints for chapter expansion and branching (low latency, single-chapter scope). Frontend dialogs dispatch requests and update the canvas store when results arrive. Uses the existing `unified_task_manager` for async task tracking.

**Tech Stack:** FastAPI, Celery, httpx (OpenAI-compatible LLM API), React 19, Zustand, ReactFlow, TailwindCSS

---

## File Map

| Action | File | Responsibility |
|--------|------|---------------|
| Create | `backend/app/services/script_ai_service.py` | LLM calls for outline, expand, branch |
| Create | `backend/app/tasks/script_tasks.py` | Celery task for async outline generation |
| Create | `backend/app/api/script_ai_router.py` | AI endpoints: expand, branch, generate-outline |
| Create | `frontend/features/script/ExpandChapterDialog.tsx` | UI for AI chapter expansion |
| Create | `frontend/features/script/CreateBranchDialog.tsx` | UI for AI branching |
| Modify | `frontend/features/script/CreateStoryDialog.tsx` | Upgrade from local placeholder to backend AI call |
| Modify | `frontend/features/script/nodes/ChapterNode.tsx` | Enable Expand/Branch buttons, wire to dialogs |
| Modify | `frontend/features/script/ScriptCanvas.tsx` | Add dialog state management |
| Modify | `frontend/stores/scriptCanvasStore.ts` | Add expand/branch dialog state |
| Modify | `backend/app/api/__init__.py` | Register script_ai_router |
| Modify | `backend/app/schemas/script.py` | Add AI request schemas |

---

### Task 1: Backend — Script AI Service

**Files:**
- Create: `backend/app/services/script_ai_service.py`

- [ ] **Step 1: Create the service**

This mirrors `StoryboardAIService._call_llm()` pattern. Three LLM operations: generate_outline, expand_chapter, create_branches.

```python
"""Script AI Service — LLM-powered outline, expansion, and branching."""

import json
from typing import Any, Dict, List, Optional

import httpx
from loguru import logger

from app.core.config import settings


class ScriptAIService:
    """AI operations for the script editor module."""

    def __init__(self) -> None:
        self.api_url = getattr(settings, "LLM_API_URL", "http://localhost:8000/v1")
        self.api_key = getattr(settings, "LLM_API_KEY", "")
        self.model = getattr(settings, "LLM_MODEL", "gpt-4o")
        self.timeout = float(getattr(settings, "LLM_TIMEOUT_SECONDS", 120))

    async def _call_llm(
        self,
        messages: List[Dict[str, str]],
        temperature: float = 0.7,
        max_tokens: int = 4096,
    ) -> str:
        headers = {"Content-Type": "application/json"}
        if self.api_key:
            headers["Authorization"] = f"Bearer {self.api_key}"

        payload = {
            "model": self.model,
            "messages": messages,
            "temperature": temperature,
            "max_tokens": max_tokens,
        }

        async with httpx.AsyncClient(timeout=self.timeout) as client:
            resp = await client.post(
                f"{self.api_url}/chat/completions",
                json=payload,
                headers=headers,
            )
            resp.raise_for_status()
            data = resp.json()
            return data["choices"][0]["message"]["content"]

    def _extract_json(self, text: str) -> Any:
        """Extract JSON from LLM response that may be wrapped in markdown fences."""
        cleaned = text.strip()
        if cleaned.startswith("```"):
            lines = cleaned.split("\n")
            start = 1
            end = len(lines) - 1
            if lines[-1].strip() == "```":
                cleaned = "\n".join(lines[start:end])
            else:
                cleaned = "\n".join(lines[start:])
        return json.loads(cleaned)

    async def generate_outline(
        self,
        premise: str,
        chapter_count: int = 5,
        style_guide: Optional[str] = None,
    ) -> List[Dict[str, str]]:
        """Generate a story outline with chapter summaries from a premise."""
        system_prompt = (
            "You are a professional screenwriter and story architect. "
            "Generate a story outline as a JSON array of chapter objects.\n\n"
            "Each chapter object must have:\n"
            '- "title": string (chapter title)\n'
            '- "summary": string (2-3 sentence plot summary)\n\n'
            f"Generate exactly {chapter_count} chapters.\n"
            "Return ONLY a JSON array, no other text."
        )
        user_prompt = f"Story premise:\n{premise}"
        if style_guide:
            user_prompt += f"\n\nStyle guide:\n{style_guide}"

        messages = [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt},
        ]

        response = await self._call_llm(messages, temperature=0.7, max_tokens=4096)
        chapters = self._extract_json(response)

        if not isinstance(chapters, list):
            raise ValueError("LLM did not return a JSON array")

        return [
            {
                "title": ch.get("title", f"Chapter {i + 1}"),
                "summary": ch.get("summary", ""),
            }
            for i, ch in enumerate(chapters)
        ]

    async def expand_chapter(
        self,
        title: str,
        summary: str,
        context: Optional[str] = None,
    ) -> str:
        """Expand a chapter summary into full prose content."""
        system_prompt = (
            "You are a professional fiction writer. "
            "Expand the given chapter summary into full, vivid prose. "
            "Write 3-5 paragraphs. Use descriptive language, dialogue where "
            "appropriate, and maintain narrative flow. "
            "Return ONLY the prose text, no JSON or markdown."
        )

        user_prompt = f"Chapter title: {title}\nSummary: {summary}"
        if context:
            user_prompt = f"Story context:\n{context}\n\n{user_prompt}"

        messages = [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt},
        ]

        return await self._call_llm(messages, temperature=0.8, max_tokens=4096)

    async def create_branches(
        self,
        title: str,
        summary: str,
        branch_count: int = 2,
        branch_type: str = "choice",
        context: Optional[str] = None,
    ) -> List[Dict[str, str]]:
        """Generate alternative story branches from a chapter."""
        system_prompt = (
            "You are a professional interactive fiction writer. "
            f"Create exactly {branch_count} alternative story branches "
            f"from the given chapter. Branch type: {branch_type}.\n\n"
            "For 'choice' type: each branch represents a different decision "
            "the protagonist could make.\n"
            "For 'condition' type: each branch represents a different "
            "circumstance that could unfold.\n\n"
            "Return a JSON array of branch objects, each with:\n"
            '- "title": string (branch chapter title)\n'
            '- "summary": string (2-3 sentence plot summary for this branch)\n'
            '- "branch_label": string (short label like "Fight" or "Flee")\n\n'
            "Return ONLY a JSON array, no other text."
        )

        user_prompt = f"Chapter: {title}\nSummary: {summary}"
        if context:
            user_prompt = f"Story context:\n{context}\n\n{user_prompt}"

        messages = [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt},
        ]

        response = await self._call_llm(messages, temperature=0.9, max_tokens=4096)
        branches = self._extract_json(response)

        if not isinstance(branches, list):
            raise ValueError("LLM did not return a JSON array")

        return [
            {
                "title": b.get("title", f"Branch {i + 1}"),
                "summary": b.get("summary", ""),
                "branch_label": b.get("branch_label", f"Path {i + 1}"),
            }
            for i, b in enumerate(branches[:branch_count])
        ]
```

- [ ] **Step 2: Commit**

```bash
git add backend/app/services/script_ai_service.py
git commit -m "feat: ScriptAIService with LLM-powered outline, expand, and branch"
```

---

### Task 2: Backend — Celery Task for Outline Generation

**Files:**
- Create: `backend/app/tasks/script_tasks.py`

- [ ] **Step 1: Create the Celery task**

Async outline generation follows the `storyboard_tasks.py` pattern: create unified task → dispatch Celery → update progress → create chapters → complete.

```python
"""Script Celery tasks — async AI operations for the script editor."""

import asyncio
from typing import Optional

from celery import shared_task
from loguru import logger


def _run_async(coro):
    """Run an async coroutine in a sync Celery task."""
    return asyncio.run(coro)


async def _start_unified(task_id: str) -> None:
    try:
        from app.services.unified_task_manager import get_task_manager
        mgr = get_task_manager()
        await mgr.start(task_id)
    except Exception as e:
        logger.warning(f"[ScriptTasks] Failed to start unified task {task_id}: {e}")


async def _update_progress(task_id: str, progress: int, subtitle: str = "") -> None:
    try:
        from app.services.unified_task_manager import get_task_manager
        mgr = get_task_manager()
        await mgr.update_progress(task_id, progress, subtitle)
    except Exception as e:
        logger.warning(f"[ScriptTasks] Failed to update progress {task_id}: {e}")


async def _complete_unified(task_id: str, result_data: dict) -> None:
    try:
        from app.services.unified_task_manager import get_task_manager
        mgr = get_task_manager()
        await mgr.complete(task_id, subtitle="Done")
    except Exception as e:
        logger.warning(f"[ScriptTasks] Failed to complete unified task {task_id}: {e}")


async def _fail_unified(task_id: str, error_msg: str) -> None:
    try:
        from app.services.unified_task_manager import get_task_manager
        mgr = get_task_manager()
        await mgr.fail(task_id, error_msg)
    except Exception as e:
        logger.warning(f"[ScriptTasks] Failed to fail unified task {task_id}: {e}")


async def _generate_outline_async(
    task_id: str,
    script_id: str,
    premise: str,
    chapter_count: int,
    style_guide: Optional[str],
) -> dict:
    from app.services.script_ai_service import ScriptAIService
    from app.services.script_service import ScriptService

    await _start_unified(task_id)

    try:
        ai_svc = ScriptAIService()
        await _update_progress(task_id, 10, "Generating outline...")

        chapters = await ai_svc.generate_outline(premise, chapter_count, style_guide)
        await _update_progress(task_id, 60, f"Creating {len(chapters)} chapters...")

        script_svc = ScriptService()
        VERTICAL_GAP = 200
        START_X = 400
        START_Y = 100

        created_ids = []
        for i, ch in enumerate(chapters):
            chapter_data = {
                "script_id": script_id,
                "title": ch["title"],
                "summary": ch["summary"],
                "chapter_number": i + 1,
                "position_x": START_X,
                "position_y": START_Y + i * VERTICAL_GAP,
            }
            result = await script_svc.chapter_repo.create(chapter_data)
            created_ids.append(result.get("id", ""))

        await _update_progress(task_id, 90, "Finalizing...")

        result_data = {
            "status": "success",
            "chapter_count": len(chapters),
            "chapter_ids": created_ids,
        }
        await _complete_unified(task_id, result_data)
        return result_data

    except Exception as e:
        error_msg = str(e)
        logger.error(f"[ScriptTasks] generate_outline failed: {error_msg}")
        await _fail_unified(task_id, error_msg)
        return {"status": "failed", "error": error_msg}


@shared_task(
    name="generate_script_outline",
    bind=True,
    max_retries=2,
    default_retry_delay=30,
)
def generate_script_outline(
    self,
    task_id: str,
    script_id: str,
    premise: str,
    chapter_count: int = 5,
    style_guide: Optional[str] = None,
) -> dict:
    """Generate a story outline via LLM and create chapter nodes."""
    return _run_async(
        _generate_outline_async(task_id, script_id, premise, chapter_count, style_guide)
    )
```

- [ ] **Step 2: Commit**

```bash
git add backend/app/tasks/script_tasks.py
git commit -m "feat: Celery task for async AI outline generation"
```

---

### Task 3: Backend — AI Request Schemas + Router

**Files:**
- Modify: `backend/app/schemas/script.py`
- Create: `backend/app/api/script_ai_router.py`
- Modify: `backend/app/api/__init__.py`

- [ ] **Step 1: Add AI request schemas to script.py**

Add these schemas after the existing `GenerateOutlineRequest`:

```python
class ExpandChapterRequest(BaseModel):
    """Request body for AI chapter expansion."""

    script_id: str
    chapter_id: str
    title: str = Field(..., max_length=200)
    summary: str = Field(..., min_length=1, max_length=5000)
    context: Optional[str] = Field(None, max_length=10000)


class CreateBranchesRequest(BaseModel):
    """Request body for AI story branching."""

    script_id: str
    chapter_id: str
    title: str = Field(..., max_length=200)
    summary: str = Field(..., min_length=1, max_length=5000)
    branch_count: int = Field(default=2, ge=2, le=4)
    branch_type: str = Field(default="choice", pattern="^(choice|condition)$")
    context: Optional[str] = Field(None, max_length=10000)
```

- [ ] **Step 2: Create script_ai_router.py**

```python
"""Script AI Router — endpoints for AI-powered outline, expansion, and branching."""

from typing import Any, Dict

from fastapi import APIRouter, HTTPException
from loguru import logger

from app.core.deps import AuthDep
from app.schemas.script import (
    CreateBranchesRequest,
    ExpandChapterRequest,
    GenerateOutlineRequest,
)
from app.services.script_ai_service import ScriptAIService
from app.services.script_service import ScriptService
from app.services.unified_task_manager import get_task_manager

router = APIRouter(prefix="/scripts")


@router.post("/generate-outline")
async def generate_outline(auth: AuthDep, body: GenerateOutlineRequest) -> Dict[str, Any]:
    """Dispatch async outline generation. Returns task_id immediately."""
    try:
        mgr = get_task_manager()
        task_id = await mgr.create(
            user_id=auth.user_id,
            task_type="script_outline_gen",
            title=f"Generate outline ({body.chapter_count} chapters)",
        )

        from app.tasks.script_tasks import generate_script_outline

        generate_script_outline.delay(
            task_id,
            body.script_id,
            body.premise,
            body.chapter_count,
            body.style_guide,
        )

        return {"success": True, "task_id": task_id}
    except Exception as exc:
        logger.error("[ScriptAI] generate_outline failed: %s", exc)
        raise HTTPException(status_code=500, detail=f"Failed to generate outline: {exc}")


@router.post("/expand-chapter")
async def expand_chapter(auth: AuthDep, body: ExpandChapterRequest) -> Dict[str, Any]:
    """Synchronously expand a chapter summary into full prose."""
    try:
        ai_svc = ScriptAIService()
        content = await ai_svc.expand_chapter(
            title=body.title,
            summary=body.summary,
            context=body.context,
        )

        # Update the chapter with expanded content
        script_svc = ScriptService()
        updated = await script_svc.update_chapter(
            body.chapter_id, {"content": content}
        )

        return {"success": True, "data": {"content": content, "chapter": updated}}
    except Exception as exc:
        logger.error("[ScriptAI] expand_chapter failed: %s", exc)
        raise HTTPException(status_code=500, detail=f"Failed to expand chapter: {exc}")


@router.post("/create-branches")
async def create_branches(auth: AuthDep, body: CreateBranchesRequest) -> Dict[str, Any]:
    """Synchronously generate story branches and create chapter nodes."""
    try:
        ai_svc = ScriptAIService()
        branches = await ai_svc.create_branches(
            title=body.title,
            summary=body.summary,
            branch_count=body.branch_count,
            branch_type=body.branch_type,
            context=body.context,
        )

        # Get parent chapter position to offset branches
        script_svc = ScriptService()
        parent = await script_svc.chapter_repo.get_by_id(body.chapter_id)
        parent_x = parent.get("position_x", 400) if parent else 400
        parent_y = parent.get("position_y", 100) if parent else 100

        BRANCH_X_OFFSET = 350
        BRANCH_Y_OFFSET = 250

        created = []
        for i, branch in enumerate(branches):
            x_offset = (i - len(branches) / 2 + 0.5) * BRANCH_X_OFFSET
            chapter_data = {
                "script_id": body.script_id,
                "parent_chapter_id": body.chapter_id,
                "title": branch["title"],
                "summary": branch["summary"],
                "branch_label": branch["branch_label"],
                "branch_type": body.branch_type,
                "position_x": parent_x + x_offset,
                "position_y": parent_y + BRANCH_Y_OFFSET,
            }
            result = await script_svc.create_chapter(body.script_id, chapter_data)
            created.append(result)

        return {"success": True, "data": {"branches": created}}
    except Exception as exc:
        logger.error("[ScriptAI] create_branches failed: %s", exc)
        raise HTTPException(status_code=500, detail=f"Failed to create branches: {exc}")
```

- [ ] **Step 3: Register router in __init__.py**

Add after the existing script router registrations:

```python
from app.api.script_ai_router import router as script_ai_router
api_router.include_router(router=script_ai_router, tags=["Script AI"])
```

- [ ] **Step 4: Add `get_by_id` to ScriptChapterRepository**

In `backend/app/repositories/script_repository.py`, add to `ScriptChapterRepository`:

```python
async def get_by_id(self, chapter_id: str) -> Optional[Dict[str, Any]]:
    try:
        client = await self._get_client()
        result = (
            await client.table(self.TABLE_NAME)
            .select("*")
            .eq("id", chapter_id)
            .execute()
        )
        return result.data[0] if result.data else None
    except Exception as e:
        logger.error(f"Failed to get chapter {chapter_id}: {e}")
        return None
```

- [ ] **Step 5: Commit**

```bash
git add backend/app/schemas/script.py backend/app/api/script_ai_router.py \
  backend/app/api/__init__.py backend/app/repositories/script_repository.py
git commit -m "feat: script AI router (outline, expand, branch endpoints)"
```

---

### Task 4: Frontend — Script AI Service Functions

**Files:**
- Modify: `frontend/services/scriptService.ts`

- [ ] **Step 1: Add AI endpoint functions**

Append to `frontend/services/scriptService.ts`:

```typescript
// ─── AI Operations ───────────────────────────────────────────────────────────

export async function generateOutline(data: {
  script_id: string;
  premise: string;
  chapter_count: number;
  style_guide?: string;
}): Promise<{ task_id: string }> {
  const headers = await getAuthHeaders();
  const res = await fetch(`${getApiUrl()}/api/v1/scripts/generate-outline`, {
    method: 'POST',
    headers: { ...headers, 'Content-Type': 'application/json' },
    body: JSON.stringify(data),
  });
  return unwrapResponse<{ task_id: string }>(res);
}

export async function expandChapter(data: {
  script_id: string;
  chapter_id: string;
  title: string;
  summary: string;
  context?: string;
}): Promise<{ content: string; chapter: Record<string, unknown> }> {
  const headers = await getAuthHeaders();
  const res = await fetch(`${getApiUrl()}/api/v1/scripts/expand-chapter`, {
    method: 'POST',
    headers: { ...headers, 'Content-Type': 'application/json' },
    body: JSON.stringify(data),
  });
  return unwrapResponse<{ content: string; chapter: Record<string, unknown> }>(res);
}

export async function createBranches(data: {
  script_id: string;
  chapter_id: string;
  title: string;
  summary: string;
  branch_count: number;
  branch_type: 'choice' | 'condition';
  context?: string;
}): Promise<{ branches: ScriptChapter[] }> {
  const headers = await getAuthHeaders();
  const res = await fetch(`${getApiUrl()}/api/v1/scripts/create-branches`, {
    method: 'POST',
    headers: { ...headers, 'Content-Type': 'application/json' },
    body: JSON.stringify(data),
  });
  return unwrapResponse<{ branches: ScriptChapter[] }>(res);
}
```

- [ ] **Step 2: Commit**

```bash
git add frontend/services/scriptService.ts
git commit -m "feat: frontend AI service functions (outline, expand, branch)"
```

---

### Task 5: Frontend — ExpandChapterDialog

**Files:**
- Create: `frontend/features/script/ExpandChapterDialog.tsx`

- [ ] **Step 1: Create the dialog**

```typescript
import { useState, useCallback } from 'react';
import { X, BookOpen, Loader2 } from 'lucide-react';
import { useScriptCanvasStore } from '../../stores/scriptCanvasStore';
import { expandChapter } from '../../services/scriptService';
import { useParams } from 'react-router-dom';

interface Props {
  isOpen: boolean;
  onClose: () => void;
  chapterId: string;
  title: string;
  summary: string;
}

export function ExpandChapterDialog({ isOpen, onClose, chapterId, title, summary }: Props) {
  const { scriptId } = useParams<{ scriptId: string }>();
  const updateNodeData = useScriptCanvasStore((s) => s.updateNodeData);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const handleExpand = useCallback(async () => {
    if (!scriptId) return;
    setLoading(true);
    setError(null);

    try {
      const result = await expandChapter({
        script_id: scriptId,
        chapter_id: chapterId,
        title,
        summary,
      });

      updateNodeData(chapterId, { content: result.content, isExpanded: true });
      onClose();
    } catch (err) {
      const message = err instanceof Error ? err.message : String(err);
      setError(message);
      console.error('[ExpandChapterDialog] Failed:', message);
    } finally {
      setLoading(false);
    }
  }, [scriptId, chapterId, title, summary, updateNodeData, onClose]);

  if (!isOpen) return null;

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/60">
      <div className="w-[480px] bg-zinc-900 rounded-xl border border-zinc-700 shadow-2xl">
        <div className="flex items-center justify-between px-5 py-3 border-b border-zinc-800">
          <div className="flex items-center gap-2">
            <BookOpen size={16} className="text-indigo-400" />
            <h3 className="text-sm font-semibold text-white">Expand Chapter with AI</h3>
          </div>
          <button onClick={onClose} className="text-zinc-500 hover:text-zinc-300">
            <X size={16} />
          </button>
        </div>

        <div className="px-5 py-4 space-y-3">
          <div>
            <p className="text-xs font-medium text-zinc-400 mb-1">Chapter</p>
            <p className="text-sm text-zinc-200 font-medium">{title}</p>
          </div>
          <div>
            <p className="text-xs font-medium text-zinc-400 mb-1">Summary to expand</p>
            <p className="text-sm text-zinc-300 bg-zinc-800 rounded-lg px-3 py-2">{summary || 'No summary provided'}</p>
          </div>
          <p className="text-xs text-zinc-500">
            AI will generate 3-5 paragraphs of prose from this summary.
          </p>
          {error && (
            <p className="text-xs text-red-400 bg-red-900/20 rounded-lg px-3 py-2">{error}</p>
          )}
        </div>

        <div className="flex justify-end gap-2 px-5 py-3 border-t border-zinc-800">
          <button
            onClick={onClose}
            className="px-3 py-1.5 text-xs text-zinc-400 hover:text-zinc-200 rounded-lg hover:bg-zinc-800 transition-colors"
          >
            Cancel
          </button>
          <button
            onClick={handleExpand}
            disabled={loading || !summary}
            className="flex items-center gap-1.5 px-4 py-1.5 text-xs font-medium text-white bg-indigo-600 hover:bg-indigo-500 rounded-lg transition-colors disabled:opacity-50 disabled:cursor-not-allowed"
          >
            {loading ? <Loader2 size={12} className="animate-spin" /> : <BookOpen size={12} />}
            {loading ? 'Expanding...' : 'Expand with AI'}
          </button>
        </div>
      </div>
    </div>
  );
}
```

- [ ] **Step 2: Commit**

```bash
git add frontend/features/script/ExpandChapterDialog.tsx
git commit -m "feat: ExpandChapterDialog for AI chapter expansion"
```

---

### Task 6: Frontend — CreateBranchDialog

**Files:**
- Create: `frontend/features/script/CreateBranchDialog.tsx`

- [ ] **Step 1: Create the dialog**

```typescript
import { useState, useCallback } from 'react';
import { X, GitBranch, Loader2 } from 'lucide-react';
import { useScriptCanvasStore, type ChapterNodeData } from '../../stores/scriptCanvasStore';
import { createBranches } from '../../services/scriptService';
import { useParams } from 'react-router-dom';

interface Props {
  isOpen: boolean;
  onClose: () => void;
  chapterId: string;
  title: string;
  summary: string;
}

export function CreateBranchDialog({ isOpen, onClose, chapterId, title, summary }: Props) {
  const { scriptId } = useParams<{ scriptId: string }>();
  const addChapterNode = useScriptCanvasStore((s) => s.addChapterNode);
  const nodes = useScriptCanvasStore((s) => s.nodes);

  const [branchCount, setBranchCount] = useState(2);
  const [branchType, setBranchType] = useState<'choice' | 'condition'>('choice');
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const handleCreate = useCallback(async () => {
    if (!scriptId) return;
    setLoading(true);
    setError(null);

    try {
      const result = await createBranches({
        script_id: scriptId,
        chapter_id: chapterId,
        title,
        summary,
        branch_count: branchCount,
        branch_type: branchType,
      });

      // Add branch nodes to canvas
      const parentNode = nodes.find((n) => n.id === chapterId);
      const parentX = parentNode?.position.x ?? 400;
      const parentY = parentNode?.position.y ?? 100;
      const BRANCH_X_OFFSET = 350;
      const BRANCH_Y_OFFSET = 250;

      for (let i = 0; i < result.branches.length; i++) {
        const branch = result.branches[i];
        const xOffset = (i - result.branches.length / 2 + 0.5) * BRANCH_X_OFFSET;
        addChapterNode(
          { x: parentX + xOffset, y: parentY + BRANCH_Y_OFFSET },
          {
            title: branch.title ?? `Branch ${i + 1}`,
            summary: branch.summary ?? '',
            chapterNumber: nodes.length + i + 1,
            branchLabel: branch.branch_label,
            branchType: branchType,
          },
        );
      }

      onClose();
    } catch (err) {
      const message = err instanceof Error ? err.message : String(err);
      setError(message);
      console.error('[CreateBranchDialog] Failed:', message);
    } finally {
      setLoading(false);
    }
  }, [scriptId, chapterId, title, summary, branchCount, branchType, nodes, addChapterNode, onClose]);

  if (!isOpen) return null;

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/60">
      <div className="w-[480px] bg-zinc-900 rounded-xl border border-zinc-700 shadow-2xl">
        <div className="flex items-center justify-between px-5 py-3 border-b border-zinc-800">
          <div className="flex items-center gap-2">
            <GitBranch size={16} className="text-amber-400" />
            <h3 className="text-sm font-semibold text-white">Create Story Branches</h3>
          </div>
          <button onClick={onClose} className="text-zinc-500 hover:text-zinc-300">
            <X size={16} />
          </button>
        </div>

        <div className="px-5 py-4 space-y-4">
          <div>
            <p className="text-xs font-medium text-zinc-400 mb-1">Branching from</p>
            <p className="text-sm text-zinc-200 font-medium">{title}</p>
            <p className="text-xs text-zinc-400 mt-1 line-clamp-2">{summary}</p>
          </div>

          <div>
            <label className="block text-xs font-medium text-zinc-400 mb-1.5">Branch Type</label>
            <div className="flex gap-2">
              {(['choice', 'condition'] as const).map((type) => (
                <button
                  key={type}
                  onClick={() => setBranchType(type)}
                  className={`flex-1 px-3 py-2 text-xs rounded-lg border transition-colors ${
                    branchType === type
                      ? 'border-amber-500 bg-amber-900/30 text-amber-300'
                      : 'border-zinc-700 bg-zinc-800 text-zinc-400 hover:border-zinc-600'
                  }`}
                >
                  {type === 'choice' ? 'Character Choice' : 'Condition/Circumstance'}
                </button>
              ))}
            </div>
          </div>

          <div>
            <label className="block text-xs font-medium text-zinc-400 mb-1.5">Number of Branches</label>
            <div className="flex items-center gap-3">
              {[2, 3, 4].map((n) => (
                <button
                  key={n}
                  onClick={() => setBranchCount(n)}
                  className={`w-10 h-10 rounded-lg text-sm font-medium transition-colors ${
                    branchCount === n
                      ? 'bg-amber-600 text-white'
                      : 'bg-zinc-800 text-zinc-400 hover:bg-zinc-700'
                  }`}
                >
                  {n}
                </button>
              ))}
            </div>
          </div>

          {error && (
            <p className="text-xs text-red-400 bg-red-900/20 rounded-lg px-3 py-2">{error}</p>
          )}
        </div>

        <div className="flex justify-end gap-2 px-5 py-3 border-t border-zinc-800">
          <button
            onClick={onClose}
            className="px-3 py-1.5 text-xs text-zinc-400 hover:text-zinc-200 rounded-lg hover:bg-zinc-800 transition-colors"
          >
            Cancel
          </button>
          <button
            onClick={handleCreate}
            disabled={loading || !summary}
            className="flex items-center gap-1.5 px-4 py-1.5 text-xs font-medium text-white bg-amber-600 hover:bg-amber-500 rounded-lg transition-colors disabled:opacity-50 disabled:cursor-not-allowed"
          >
            {loading ? <Loader2 size={12} className="animate-spin" /> : <GitBranch size={12} />}
            {loading ? 'Generating...' : `Create ${branchCount} Branches`}
          </button>
        </div>
      </div>
    </div>
  );
}
```

- [ ] **Step 2: Commit**

```bash
git add frontend/features/script/CreateBranchDialog.tsx
git commit -m "feat: CreateBranchDialog for AI story branching"
```

---

### Task 7: Frontend — Upgrade CreateStoryDialog + Wire ChapterNode Buttons

**Files:**
- Modify: `frontend/features/script/CreateStoryDialog.tsx`
- Modify: `frontend/features/script/nodes/ChapterNode.tsx`
- Modify: `frontend/stores/scriptCanvasStore.ts`
- Modify: `frontend/features/script/ScriptCanvas.tsx`

- [ ] **Step 1: Upgrade CreateStoryDialog to use backend AI**

Replace the local generation logic in `CreateStoryDialog.tsx` with a backend call. The dialog dispatches an async task and closes — the canvas refreshes when the task completes.

In the `handleGenerate` function, replace the local chapter creation with:

```typescript
// Replace the try block content with:
const result = await generateOutline({
  script_id: scriptId!,
  premise: premise.trim(),
  chapter_count: chapterCount,
});
// Task dispatched — dialog closes. User sees progress in TaskManager.
onClose();
```

Add the import: `import { generateOutline } from '../../services/scriptService';`

Remove the `clearCanvas` and `addChapterNode` imports/usage from the dialog (the backend creates chapters now).

Add `scriptId` from `useParams`:
```typescript
const { scriptId } = useParams<{ scriptId: string }>();
```

- [ ] **Step 2: Add dialog state to scriptCanvasStore**

Add to `ScriptCanvasState` interface and initial state:

```typescript
// State
expandDialog: { isOpen: boolean; chapterId: string; title: string; summary: string } | null;
branchDialog: { isOpen: boolean; chapterId: string; title: string; summary: string } | null;

// Actions
openExpandDialog: (chapterId: string, title: string, summary: string) => void;
closeExpandDialog: () => void;
openBranchDialog: (chapterId: string, title: string, summary: string) => void;
closeBranchDialog: () => void;
```

Implementation:

```typescript
expandDialog: null,
branchDialog: null,

openExpandDialog: (chapterId, title, summary) =>
  set({ expandDialog: { isOpen: true, chapterId, title, summary } }),
closeExpandDialog: () => set({ expandDialog: null }),
openBranchDialog: (chapterId, title, summary) =>
  set({ branchDialog: { isOpen: true, chapterId, title, summary } }),
closeBranchDialog: () => set({ branchDialog: null }),
```

- [ ] **Step 3: Enable Expand/Branch buttons in ChapterNode**

In `ChapterNode.tsx`, remove the `disabled` prop from the Expand and Branch buttons and wire them to the store:

```typescript
const openExpandDialog = useScriptCanvasStore((s) => s.openExpandDialog);
const openBranchDialog = useScriptCanvasStore((s) => s.openBranchDialog);

// Expand button:
<button
  onClick={() => openExpandDialog(id, data.title, data.summary)}
  className="flex items-center gap-1 text-[11px] text-zinc-500 hover:text-indigo-400 ..."
  title="Expand with AI"
>

// Branch button:
<button
  onClick={() => openBranchDialog(id, data.title, data.summary)}
  className="flex items-center gap-1 text-[11px] text-zinc-500 hover:text-amber-400 ..."
  title="Create branch"
>
```

- [ ] **Step 4: Add dialogs to ScriptCanvas**

In `ScriptCanvas.tsx` (or `ScriptEditorPage.tsx`), render the dialogs based on store state:

```typescript
import { ExpandChapterDialog } from './ExpandChapterDialog';
import { CreateBranchDialog } from './CreateBranchDialog';

// Inside the component:
const expandDialog = useScriptCanvasStore((s) => s.expandDialog);
const closeExpandDialog = useScriptCanvasStore((s) => s.closeExpandDialog);
const branchDialog = useScriptCanvasStore((s) => s.branchDialog);
const closeBranchDialog = useScriptCanvasStore((s) => s.closeBranchDialog);

// In JSX:
{expandDialog && (
  <ExpandChapterDialog
    isOpen={expandDialog.isOpen}
    onClose={closeExpandDialog}
    chapterId={expandDialog.chapterId}
    title={expandDialog.title}
    summary={expandDialog.summary}
  />
)}
{branchDialog && (
  <CreateBranchDialog
    isOpen={branchDialog.isOpen}
    onClose={closeBranchDialog}
    chapterId={branchDialog.chapterId}
    title={branchDialog.title}
    summary={branchDialog.summary}
  />
)}
```

- [ ] **Step 5: Commit**

```bash
git add frontend/features/script/CreateStoryDialog.tsx \
  frontend/features/script/nodes/ChapterNode.tsx \
  frontend/stores/scriptCanvasStore.ts \
  frontend/pages/ScriptEditor/ScriptEditorPage.tsx
git commit -m "feat: wire AI dialogs to ChapterNode buttons, upgrade outline to backend"
```

---

### Task 8: Build Verification

- [ ] **Step 1: Frontend build**

```bash
cd frontend && npx vite build 2>&1 | tail -5
```

Expected: Build completes (pre-existing PWA warning OK).

- [ ] **Step 2: TypeScript check**

```bash
npx tsc --noEmit 2>&1 | grep -iE "script|chapter|branch|expand" || echo "No errors"
```

Expected: No errors in our new files.

- [ ] **Step 3: Backend import check**

```bash
cd backend && uv run python -c "from app.api.script_ai_router import router; print('script_ai_router OK')"
cd backend && uv run python -c "from app.tasks.script_tasks import generate_script_outline; print('script_tasks OK')"
```

- [ ] **Step 4: Commit fixes if needed**

```bash
git add -A && git commit -m "fix: P3 build verification fixes"
```
