# AI Agent Framework Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build a shared AI Agent framework for MediaHub — configurable agents, session management, AI Chat Panel, token tracking.

**Architecture:** AgentService orchestrates LLM calls with configurable personas/rules. Sessions persist multi-turn conversations. AI Chat Panel provides in-editor chat interface. Token tracking integrates with existing points system.

**Tech Stack:** Python (FastAPI, httpx), PostgreSQL (Supabase), React, TailwindCSS

**Spec:** `docs/superpowers/specs/2026-04-08-ai-agent-framework-design.md`

---

## Phase 1: Database + AgentService Core

### Task 1: Database Migration

**Files:**
- Create: `supabase/migrations/117_ai_agent_framework.sql`

- [ ] **Step 1: Write migration**

```sql
-- 117_ai_agent_framework.sql

-- AI Agents
CREATE TABLE ai_agents (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  name TEXT NOT NULL,
  description TEXT,
  persona TEXT NOT NULL,
  model TEXT DEFAULT 'qwen-max',
  temperature DECIMAL DEFAULT 0.7,
  max_tokens INT DEFAULT 4096,
  config_json JSONB DEFAULT '{}',
  rules JSONB DEFAULT '[]',
  team_id BIGINT REFERENCES teams(id),
  project_id BIGINT,
  created_by UUID,
  enabled BOOLEAN DEFAULT true,
  sort_order INT DEFAULT 0,
  created_at TIMESTAMPTZ DEFAULT now(),
  updated_at TIMESTAMPTZ DEFAULT now()
);

-- AI Sessions
CREATE TABLE ai_sessions (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  user_id UUID NOT NULL,
  team_id BIGINT,
  project_id BIGINT,
  title TEXT DEFAULT 'New Chat',
  context_type TEXT,
  context_id TEXT,
  total_tokens INT DEFAULT 0,
  message_count INT DEFAULT 0,
  status TEXT DEFAULT 'active',
  created_at TIMESTAMPTZ DEFAULT now(),
  updated_at TIMESTAMPTZ DEFAULT now()
);

-- AI Messages
CREATE TABLE ai_messages (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  session_id UUID REFERENCES ai_sessions(id) ON DELETE CASCADE,
  role TEXT NOT NULL CHECK (role IN ('system', 'user', 'assistant')),
  content TEXT NOT NULL,
  agent_id UUID,
  skill_id UUID,
  metadata_json JSONB DEFAULT '{}',
  prompt_tokens INT DEFAULT 0,
  completion_tokens INT DEFAULT 0,
  created_at TIMESTAMPTZ DEFAULT now()
);
CREATE INDEX idx_ai_messages_session ON ai_messages(session_id, created_at);

-- AI Usage Logs
CREATE TABLE ai_usage_logs (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  user_id UUID NOT NULL,
  team_id BIGINT,
  project_id BIGINT,
  session_id UUID,
  agent_id UUID,
  action TEXT,
  model TEXT NOT NULL,
  prompt_tokens INT NOT NULL,
  completion_tokens INT NOT NULL,
  total_tokens INT GENERATED ALWAYS AS (prompt_tokens + completion_tokens) STORED,
  cost_points DECIMAL,
  created_at TIMESTAMPTZ DEFAULT now()
);
CREATE INDEX idx_ai_usage_user ON ai_usage_logs(user_id, created_at);
CREATE INDEX idx_ai_usage_project ON ai_usage_logs(project_id, created_at);

-- Extend skills table
ALTER TABLE skills ADD COLUMN IF NOT EXISTS prompt_template TEXT;
ALTER TABLE skills ADD COLUMN IF NOT EXISTS input_schema JSONB;
ALTER TABLE skills ADD COLUMN IF NOT EXISTS output_format TEXT DEFAULT 'text';
ALTER TABLE skills ADD COLUMN IF NOT EXISTS default_agent_id UUID;

-- Insert preset agents
INSERT INTO ai_agents (name, description, persona, model, temperature, created_by) VALUES
('Writer', 'Professional screenwriter for story creation and chapter expansion', 
 'You are a professional screenwriter. You excel at story structure, vivid scene descriptions, and natural dialogue. Write in the specified format using HTML tags: <h2> for scene headings, <p> for paragraphs, <strong> for character names in dialogue, <hr> for scene separators.',
 'qwen-max', 0.8, NULL),
('Dialogue', 'Dialogue specialist for natural character conversations',
 'You are a dialogue expert. You make characters speak naturally with distinct voices. Each character should have unique speech patterns, vocabulary, and emotional expression.',
 'qwen-plus', 0.85, NULL),
('Editor', 'Senior editor for reviewing and improving content',
 'You are a senior script editor. Review content for consistency, pacing, character development, and plot holes. Provide specific, actionable feedback.',
 'qwen-max', 0.5, NULL),
('Critic', 'Story critic for evaluation and scoring',
 'You are a professional story critic. Analyze narrative structure, character arcs, pacing, themes, and emotional impact. Provide a score (1-10) and detailed breakdown.',
 'qwen-turbo', 0.3, NULL),
('Scene', 'Scene description specialist for visual storytelling',
 'You are a visual storytelling expert. Write detailed, cinematic scene descriptions with attention to lighting, atmosphere, camera angles, and spatial relationships.',
 'qwen-plus', 0.7, NULL);

-- RLS policies
ALTER TABLE ai_agents ENABLE ROW LEVEL SECURITY;
ALTER TABLE ai_sessions ENABLE ROW LEVEL SECURITY;
ALTER TABLE ai_messages ENABLE ROW LEVEL SECURITY;
ALTER TABLE ai_usage_logs ENABLE ROW LEVEL SECURITY;
```

- [ ] **Step 2: Execute migration via Supabase MCP**

- [ ] **Step 3: Commit**

```bash
git add supabase/migrations/117_ai_agent_framework.sql
git commit -m "feat: add AI Agent framework database tables and preset agents"
```

---

### Task 2: Backend — AgentService Core

**Files:**
- Create: `backend/app/services/agent_service.py`
- Create: `backend/app/schemas/ai.py`

- [ ] **Step 1: Create Pydantic schemas**

```python
# backend/app/schemas/ai.py
from pydantic import BaseModel, Field
from typing import Optional, List
from datetime import datetime

class AgentConfig(BaseModel):
    id: str
    name: str
    persona: str
    model: str = "qwen-max"
    temperature: float = 0.7
    max_tokens: int = 4096
    rules: list = []

class ChatRequest(BaseModel):
    message: str = Field(..., max_length=10000)
    agent_id: Optional[str] = None
    context: dict = {}

class AgentCallRequest(BaseModel):
    action: str
    context: dict = {}
    session_id: Optional[str] = None

class AgentResponse(BaseModel):
    content: str
    session_id: str
    agent_id: str
    prompt_tokens: int
    completion_tokens: int

class SessionCreate(BaseModel):
    title: str = "New Chat"
    context_type: Optional[str] = None
    context_id: Optional[str] = None
    project_id: Optional[str] = None
```

- [ ] **Step 2: Create AgentService**

Core service with:
- `load_agent(agent_id)` — fetch from DB, cache in memory (TTL 5 min)
- `call_agent(agent_id, action, context, user_id, session_id, project_id)` — main entry point
- `_build_system_prompt(agent, skill, context)` — persona + rules + skill template
- `_build_messages(agent, session, context)` — context window truncation (28K token budget)
- `_call_llm(messages, config)` — httpx with shared client instance
- `_save_messages(session_id, user_msg, assistant_msg)` — persist + update session counters
- `_log_usage(user_id, session_id, agent_id, usage)` — async token tracking
- `_check_points(user_id, model, max_tokens)` — pre-check balance
- `estimate_tokens(text)` — rough estimate (len/4 for CJK, len/3.5 for English)

Retry logic: 2 retries with exponential backoff for timeouts, 1 retry for 429 with Retry-After.

httpx client as singleton: `self._client = httpx.AsyncClient(timeout=120)`

- [ ] **Step 3: Commit**

```bash
git add backend/app/services/agent_service.py backend/app/schemas/ai.py
git commit -m "feat: add AgentService core with LLM calling, context truncation, and token tracking"
```

---

### Task 3: Backend — Session Service

**Files:**
- Create: `backend/app/services/ai_session_service.py`

- [ ] **Step 1: Create AISessionService**

- `create_session(user_id, project_id, title, context_type, context_id)` → Session
- `get_session(session_id, user_id)` → Session (with ownership check)
- `list_sessions(user_id, project_id)` → List[Session] ordered by updated_at desc
- `get_messages(session_id, user_id, limit=50, offset=0)` → List[Message]
- `archive_session(session_id, user_id)` → None
- `delete_session(session_id, user_id)` → None

All methods verify `session.user_id == user_id` for authorization.

- [ ] **Step 2: Commit**

```bash
git add backend/app/services/ai_session_service.py
git commit -m "feat: add AISessionService with session CRUD and authorization"
```

---

### Task 4: Backend — API Routes

**Files:**
- Create: `backend/app/api/ai_router.py`
- Modify: `backend/app/main.py`

- [ ] **Step 1: Create AI router with all endpoints**

```python
# Agents
GET    /api/v1/ai/agents                      — list agents (project + global)
POST   /api/v1/ai/agents                      — create agent
PATCH  /api/v1/ai/agents/{id}                 — update agent
DELETE /api/v1/ai/agents/{id}                 — delete agent

# Agent call
POST   /api/v1/ai/agents/{id}/call            — single call (auto-creates session)

# Sessions
GET    /api/v1/ai/sessions                    — list user's sessions
POST   /api/v1/ai/sessions                    — create session
GET    /api/v1/ai/sessions/{id}               — session detail + paginated messages
DELETE /api/v1/ai/sessions/{id}               — delete session

# Chat (multi-turn)
POST   /api/v1/ai/sessions/{id}/chat          — send message in session
POST   /api/v1/ai/sessions/{id}/chat/stream   — SSE streaming chat

# Usage
GET    /api/v1/ai/usage                       — token consumption stats
```

- [ ] **Step 2: Register router in main.py**

- [ ] **Step 3: Verify server starts**

```bash
cd backend && uv run python -c "from app.main import app; print('OK')"
```

- [ ] **Step 4: Commit**

```bash
git add backend/app/api/ai_router.py backend/app/main.py
git commit -m "feat: add AI Agent API routes — agents, sessions, chat, usage"
```

---

## Phase 2: AI Chat Panel Frontend

### Task 5: Frontend — AI Service

**Files:**
- Create: `frontend/services/aiService.ts`

- [ ] **Step 1: Create API client**

Functions:
- `fetchAgents(projectId)` → Agent[]
- `createSession(data)` → Session
- `fetchSessions(projectId)` → Session[]
- `fetchMessages(sessionId, limit, offset)` → Message[]
- `sendMessage(sessionId, message, agentId?)` → AgentResponse
- `streamMessage(sessionId, message, agentId?, onChunk)` → void (SSE)
- `callAgent(agentId, action, context, projectId)` → AgentResponse
- `fetchUsage(projectId)` → UsageStats
- `deleteSession(sessionId)` → void

- [ ] **Step 2: Commit**

```bash
git add frontend/services/aiService.ts
git commit -m "feat: add aiService with agent, session, chat, and usage API functions"
```

---

### Task 6: Frontend — Shared Chat Components

**Files:**
- Create: `frontend/components/chat/MessageBubble.tsx`
- Create: `frontend/components/chat/TypingIndicator.tsx`
- Create: `frontend/components/chat/ChatInput.tsx`
- Create: `frontend/components/chat/EmptyState.tsx`

- [ ] **Step 1: Create MessageBubble**

Props: `role, content, agentName?, tokens?, onApply?, onCopy?`

- User messages: right-aligned, indigo background
- Assistant messages: left-aligned, zinc-800 background
  - Agent name badge at top (amber tag)
  - Token count (small gray text at bottom)
  - Action buttons: [Apply to Chapter] [Copy]

- [ ] **Step 2: Create TypingIndicator**

Three bouncing dots animation.

- [ ] **Step 3: Create ChatInput**

Input field + Send button + optional attachment button (for chapter reference).

- [ ] **Step 4: Create EmptyState**

Centered sparkles icon + "Start a conversation" + suggested action buttons.

- [ ] **Step 5: Commit**

```bash
git add frontend/components/chat/
git commit -m "feat: add shared chat components — MessageBubble, TypingIndicator, ChatInput, EmptyState"
```

---

### Task 7: Frontend — AIChatPanel

**Files:**
- Create: `frontend/components/AIChatPanel.tsx`
- Create: `frontend/components/AgentSelector.tsx`
- Create: `frontend/components/SessionList.tsx`

- [ ] **Step 1: Create AgentSelector**

Dropdown to switch between agents (Writer/Dialogue/Editor/Critic/Scene). Shows agent name + description. Currently selected agent highlighted.

- [ ] **Step 2: Create SessionList**

Collapsible list of sessions for current project. Each item: title, relative time ("2h ago"), message count. Active session highlighted. "+" button to create new session.

- [ ] **Step 3: Create AIChatPanel**

Main panel component:
- Header: "AI Chat" title + AgentSelector + "+" new session button
- SessionList (collapsible)
- Message area (scrollable, loads from aiService)
- ChatInput at bottom with chapter reference button

Props:
```typescript
interface AIChatPanelProps {
  projectId: string;
  contextType: 'script' | 'storyboard';
  contextId?: string;
  onApplyContent?: (content: string) => void;
  chapters?: Array<{ id: string; title: string; chapterNumber: number }>;
}
```

States: loading, empty, streaming, error, token-insufficient.

- [ ] **Step 4: Verify build**

```bash
cd frontend && npm run build
```

- [ ] **Step 5: Commit**

```bash
git add frontend/components/AIChatPanel.tsx frontend/components/AgentSelector.tsx frontend/components/SessionList.tsx
git commit -m "feat: add AIChatPanel with AgentSelector and SessionList"
```

---

### Task 8: Frontend — Integrate AIChatPanel into Script Editor

**Files:**
- Modify: `frontend/pages/ScriptEditor/ScriptEditorPage.tsx`

- [ ] **Step 1: Add Chat Panel toggle**

Add a chat icon button in EditorTopBar or as a floating button. Clicking toggles the right-side AIChatPanel.

Layout changes:
```
┌──────────┬───────────────────┬──────────┐
│ Assets   │     Canvas        │ AI Chat  │
│ Sidebar  │                   │ (toggle) │
└──────────┴───────────────────┴──────────┘
```

State: `const [showChat, setShowChat] = useState(false);`

Wire `onApplyContent` to inject content into the currently selected chapter's TipTap editor.

- [ ] **Step 2: Commit**

```bash
git add frontend/pages/ScriptEditor/ScriptEditorPage.tsx
git commit -m "feat: integrate AIChatPanel into Script Editor with chapter apply support"
```

---

## Phase 3: Script Editor Migration + Streaming

### Task 9: Backend — Migrate Script AI to AgentService

**Files:**
- Modify: `backend/app/services/script_ai_service.py`
- Modify: `backend/app/api/script_ai_router.py`

- [ ] **Step 1: Refactor script_ai_service to use AgentService**

`ScriptAIService` becomes a thin wrapper:
- `generate_outline()` → `agent_service.call_agent("writer", "generate_outline", ...)`
- `expand_chapter()` → `agent_service.call_agent("writer", "expand_chapter", ...)`
- `create_branches()` → `agent_service.call_agent("writer", "create_branches", ...)`

Keep the existing API contract (same request/response shapes) for backward compatibility. Internally delegate to AgentService.

- [ ] **Step 2: Commit**

```bash
git add backend/app/services/script_ai_service.py backend/app/api/script_ai_router.py
git commit -m "refactor: migrate script AI service to use AgentService internally"
```

---

### Task 10: Backend — Streaming Endpoint

**Files:**
- Modify: `backend/app/api/ai_router.py`

- [ ] **Step 1: Add SSE streaming endpoint**

`POST /api/v1/ai/sessions/{id}/chat/stream` — returns `text/event-stream`.

Uses `httpx.AsyncClient.stream()` with `"stream": true` in LLM payload. Each SSE chunk: `data: {"content": "text chunk"}\n\n`. Final chunk: `data: {"done": true, "usage": {...}}\n\n`.

After stream completes: save full message to DB, log usage, update session counters.

- [ ] **Step 2: Commit**

```bash
git add backend/app/api/ai_router.py
git commit -m "feat: add SSE streaming endpoint for AI chat"
```

---

### Task 11: Frontend — Streaming Support

**Files:**
- Modify: `frontend/services/aiService.ts`
- Modify: `frontend/components/AIChatPanel.tsx`

- [ ] **Step 1: Add streamMessage function**

Use `fetch` with `ReadableStream` to handle SSE from POST endpoint.

- [ ] **Step 2: Update AIChatPanel for streaming**

When streaming, append text chunks to the latest assistant message in real-time. Show "Stop generating" button during stream.

- [ ] **Step 3: Commit**

```bash
git add frontend/services/aiService.ts frontend/components/AIChatPanel.tsx
git commit -m "feat: add streaming chat support with real-time text display"
```

---

## Phase 4: Token Tracking + Points

### Task 12: Backend — Points Integration

**Files:**
- Modify: `backend/app/services/agent_service.py`

- [ ] **Step 1: Add points pre-check and deduction**

Before LLM call: estimate cost, check balance, reject if insufficient.
After LLM call: calculate actual cost, deduct points, log to ai_usage_logs.

Cost rates per model defined in config.

- [ ] **Step 2: Commit**

```bash
git add backend/app/services/agent_service.py
git commit -m "feat: integrate points system — pre-check balance, deduct on usage"
```

---

### Task 13: Backend — Usage Stats API

**Files:**
- Modify: `backend/app/api/ai_router.py`

- [ ] **Step 1: Implement GET /api/v1/ai/usage**

Query `ai_usage_logs` with filters: user_id, project_id, date range.
Return: total_tokens, total_cost, breakdown by agent, breakdown by action, daily trend.

- [ ] **Step 2: Commit**

```bash
git add backend/app/api/ai_router.py
git commit -m "feat: add AI usage statistics API endpoint"
```

---

## Phase 5: Storyboard Integration

### Task 14: Integrate AIChatPanel into Storyboard

**Files:**
- Modify: `frontend/pages/StoryboardWorkbench/CanvasEditorPage.tsx`

- [ ] **Step 1: Replace or augment existing ChatPanel with AIChatPanel**

The Storyboard already has a ChatPanel. Options:
- Replace with AIChatPanel (unified experience)
- Or keep existing ChatPanel and add AgentService backend support

Preferred: Replace with AIChatPanel, passing `contextType="storyboard"`.

- [ ] **Step 2: Commit**

```bash
git add frontend/pages/StoryboardWorkbench/CanvasEditorPage.tsx
git commit -m "feat: integrate AIChatPanel into Storyboard editor"
```

---

## Phase 6: Usage Dashboard

### Task 15: Frontend — Usage Dashboard Component

**Files:**
- Create: `frontend/components/AIUsageDashboard.tsx`

- [ ] **Step 1: Create dashboard component**

Display in project Settings tab:
- Total tokens this month (big number)
- Cost in points (big number)
- By Agent breakdown (horizontal bar chart or simple table)
- By Action breakdown (table)
- Daily trend (simple sparkline or list)

Use existing Recharts library (already in project).

- [ ] **Step 2: Integrate into project settings or dashboard page**

- [ ] **Step 3: Commit**

```bash
git add frontend/components/AIUsageDashboard.tsx
git commit -m "feat: add AI usage dashboard with token consumption stats"
```

---

## Summary

| Phase | Tasks | Key Deliverable |
|-------|-------|-----------------|
| 1: Core | 1-4 | DB tables + AgentService + Session service + API routes |
| 2: Chat UI | 5-8 | aiService + shared chat components + AIChatPanel + Script integration |
| 3: Migration | 9-11 | Script AI → AgentService + Streaming SSE |
| 4: Points | 12-13 | Points integration + Usage stats API |
| 5: Storyboard | 14 | Storyboard AIChatPanel integration |
| 6: Dashboard | 15 | Usage dashboard with charts |

Total: 15 tasks across 6 phases. Each phase independently testable.
