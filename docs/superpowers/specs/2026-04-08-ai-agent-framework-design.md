# AI Agent Framework Design Spec

> MediaHub 项目级 AI Agent 框架 — 基于远程 LLM API 的多 Agent 创作系统

## 1. Overview

构建一个通用的 AI Agent 框架，让 MediaHub 的任何模块（Script Editor、Storyboard、Skills 等）都能通过配置化的 Agent 调用远程 LLM API 进行内容创作。

参考 Forge 项目的配置文件体系，但不依赖 Claude Agent SDK，而是直接调用 OpenAI 兼容 API（通义千问 qwen 等）。

### 1.1 Goals

- Agent 配置化：persona（角色设定）+ rules（创作规则）+ config（模型参数）
- Session 管理：多轮对话、消息持久化、用户隔离
- AI Chat Panel：Script Editor 内嵌聊天界面
- Token 追踪：消费记录、积分联动、统计面板
- Skill 调用：可复用的操作模板
- Pipeline：Agent 链式调用

### 1.2 Non-Goals

- 不使用 Claude Agent SDK（纯 HTTP API 调用）
- 不做模型训练/微调
- 不做实时协作（多用户同时编辑同一 Session）

## 2. Architecture

```
前端（任意模块）
    │
    ├── AI Chat Panel（多轮对话）
    ├── 一键操作（扩写/审阅/生成大纲）
    │
    ▼
POST /api/v1/ai/agents/{agent_id}/call
    │
    ▼
AgentService
    ├── 1. 加载 Agent 配置（persona + rules）
    ├── 2. 加载 Skill 模板（如果是技能调用）
    ├── 3. 构建 Session 上下文（历史消息）
    ├── 4. 拼接 system_prompt + user_prompt
    ├── 5. 调用远程 LLM API（OpenAI 兼容格式）
    ├── 6. 保存消息到 Session
    ├── 7. 记录 Token 消费
    └── 8. 返回结果
```

## 3. Data Model

### 3.1 ai_agents — Agent 定义

```sql
CREATE TABLE ai_agents (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  name TEXT NOT NULL,
  description TEXT,
  persona TEXT NOT NULL,              -- 角色设定（markdown）
  model TEXT DEFAULT 'qwen-max',      -- 默认模型
  temperature DECIMAL DEFAULT 0.7,
  max_tokens INT DEFAULT 4096,
  config_json JSONB DEFAULT '{}',     -- 额外配置
  team_id BIGINT REFERENCES teams(id),
  project_id BIGINT,                  -- NULL = 全局 Agent
  created_by UUID,
  enabled BOOLEAN DEFAULT true,
  sort_order INT DEFAULT 0,
  created_at TIMESTAMPTZ DEFAULT now(),
  updated_at TIMESTAMPTZ DEFAULT now()
);
```

预设 Agent 示例：

| name | persona 摘要 | model | 用途 |
|------|-------------|-------|------|
| Writer | 专业编剧，擅长剧情构思和场景描写 | qwen-max | 大纲生成、章节扩写 |
| Dialogue | 对话专家，让角色说话自然有个性 | qwen-plus | 对话润色 |
| Editor | 资深编辑，负责审稿和一致性检查 | qwen-max | 内容审阅 |
| Critic | 剧本评论家，分析节奏和结构 | qwen-turbo | 评估打分 |
| Scene | 场景描写师，擅长视觉化叙事 | qwen-plus | 场景扩写 |

### 3.2 ai_agent_rules — Agent 规则

```sql
CREATE TABLE ai_agent_rules (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  agent_id UUID REFERENCES ai_agents(id) ON DELETE CASCADE,
  name TEXT NOT NULL,
  content TEXT NOT NULL,               -- rule markdown 内容
  sort_order INT DEFAULT 0,
  created_at TIMESTAMPTZ DEFAULT now()
);
```

规则示例：
- "scene_format.md" — 场景标题格式：`场景N：场景名 – 时间 – 内/外景`
- "dialogue_format.md" — 对话格式：`角色名：（动作描述）台词`
- "html_output.md" — 输出 HTML 标签白名单
- "genre_sci-fi.md" — 科幻类型特有规则

### 3.3 ai_skills — Skill 操作模板

扩展现有 `skills` 表，增加 AI 调用相关字段：

```sql
ALTER TABLE skills ADD COLUMN IF NOT EXISTS prompt_template TEXT;
ALTER TABLE skills ADD COLUMN IF NOT EXISTS input_schema JSONB;
ALTER TABLE skills ADD COLUMN IF NOT EXISTS output_format TEXT DEFAULT 'text';
ALTER TABLE skills ADD COLUMN IF NOT EXISTS default_agent_id UUID;
```

Skill 示例：

| name | prompt_template 摘要 | input | output_format |
|------|---------------------|-------|---------------|
| expand_chapter | "根据以下摘要扩写为完整剧本内容..." | {title, summary, expansion_request} | html |
| generate_outline | "根据故事概要生成章节大纲..." | {premise, chapter_count, genre} | json |
| review_script | "审阅以下剧本内容，给出修改建议..." | {content, focus_areas} | markdown |
| polish_dialogue | "优化以下对话，让角色更有个性..." | {dialogue_content, character_info} | html |
| evaluate_story | "评估故事结构，打分并给出建议..." | {full_content, genre} | json |

### 3.4 ai_sessions — 对话线程

```sql
CREATE TABLE ai_sessions (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  user_id UUID NOT NULL,
  team_id BIGINT,
  project_id BIGINT,
  title TEXT DEFAULT 'New Chat',
  context_type TEXT,                   -- 'script' / 'storyboard' / 'general'
  context_id TEXT,                     -- script_id / storyboard_id
  status TEXT DEFAULT 'active',        -- active / archived
  created_at TIMESTAMPTZ DEFAULT now(),
  updated_at TIMESTAMPTZ DEFAULT now()
);
```

Session 与 Agent 解耦 — 同一个 Session 内可以切换 Agent。

### 3.5 ai_messages — 对话消息

```sql
CREATE TABLE ai_messages (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  session_id UUID REFERENCES ai_sessions(id) ON DELETE CASCADE,
  role TEXT NOT NULL CHECK (role IN ('system', 'user', 'assistant')),
  content TEXT NOT NULL,
  agent_id UUID,                       -- 哪个 Agent 生成的（assistant 消息）
  skill_id UUID,                       -- 通过哪个 Skill 触发的
  metadata_json JSONB DEFAULT '{}',    -- 额外信息（引用的章节 ID 等）
  prompt_tokens INT,
  completion_tokens INT,
  created_at TIMESTAMPTZ DEFAULT now()
);

CREATE INDEX idx_ai_messages_session ON ai_messages(session_id, created_at);
```

### 3.6 ai_usage_logs — Token 消费记录

```sql
CREATE TABLE ai_usage_logs (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  user_id UUID NOT NULL,
  team_id BIGINT,
  project_id BIGINT,
  session_id UUID,
  agent_id UUID,
  skill_id UUID,
  action TEXT,                         -- 'expand' / 'outline' / 'chat' / 'review'
  model TEXT NOT NULL,
  prompt_tokens INT NOT NULL,
  completion_tokens INT NOT NULL,
  total_tokens INT GENERATED ALWAYS AS (prompt_tokens + completion_tokens) STORED,
  cost_points DECIMAL,                 -- 积分消耗
  created_at TIMESTAMPTZ DEFAULT now()
);

CREATE INDEX idx_ai_usage_user ON ai_usage_logs(user_id, created_at);
CREATE INDEX idx_ai_usage_project ON ai_usage_logs(project_id, created_at);
```

## 4. Backend — AgentService

### 4.1 Core Service

```
backend/app/services/agent_service.py

class AgentService:
    load_agent(agent_id) → AgentConfig
    call_agent(agent_id, action, context, user_id, session_id, project_id) → AgentResponse
    run_pipeline(context, pipeline, user_id, session_id, project_id) → AgentResponse
    _build_messages(agent, session, skill, context) → List[Message]
    _call_llm(messages, config) → LLMResponse
    _save_messages(session_id, messages) → None
    _log_usage(user_id, session_id, agent_id, usage) → None
```

### 4.2 Session Service

```
backend/app/services/ai_session_service.py

class AISessionService:
    create_session(user_id, project_id, title, context_type, context_id) → Session
    get_session(session_id) → Session
    list_sessions(user_id, project_id) → List[Session]
    get_messages(session_id, limit, offset) → List[Message]
    archive_session(session_id) → None
    delete_session(session_id) → None
```

### 4.3 API Routes

```
backend/app/api/ai_router.py

# Agent CRUD
GET    /api/v1/ai/agents                     — 列出项目的 Agent
POST   /api/v1/ai/agents                     — 创建 Agent
GET    /api/v1/ai/agents/{id}                — Agent 详情
PATCH  /api/v1/ai/agents/{id}                — 更新 Agent
DELETE /api/v1/ai/agents/{id}                — 删除 Agent

# Agent Rules
GET    /api/v1/ai/agents/{id}/rules          — Agent 的规则列表
POST   /api/v1/ai/agents/{id}/rules          — 添加规则
PATCH  /api/v1/ai/rules/{rule_id}            — 更新规则
DELETE /api/v1/ai/rules/{rule_id}            — 删除规则

# Agent 调用
POST   /api/v1/ai/agents/{id}/call           — 调用 Agent（单次或多轮）
POST   /api/v1/ai/pipeline                   — Pipeline 链式调用

# Session
GET    /api/v1/ai/sessions                   — 用户的 Session 列表
POST   /api/v1/ai/sessions                   — 创建 Session
GET    /api/v1/ai/sessions/{id}              — Session 详情 + 消息
DELETE /api/v1/ai/sessions/{id}              — 删除 Session

# Chat（多轮对话快捷接口）
POST   /api/v1/ai/sessions/{id}/chat         — 在 Session 内发消息

# Token 统计
GET    /api/v1/ai/usage                      — 用户/项目的 Token 消费统计
```

### 4.4 LLM 调用封装

```python
async def _call_llm(self, messages, config):
    """Call OpenAI-compatible API (qwen/通义千问)."""
    payload = {
        "model": config.model,         # qwen-max / qwen-plus / qwen-turbo
        "messages": messages,
        "temperature": config.temperature,
        "max_tokens": config.max_tokens,
    }
    resp = await httpx.AsyncClient().post(
        f"{settings.LLM_API_URL}/chat/completions",
        json=payload,
        headers={"Authorization": f"Bearer {settings.LLM_API_KEY}"},
    )
    data = resp.json()
    return LLMResponse(
        content=data["choices"][0]["message"]["content"],
        usage=TokenUsage(
            prompt_tokens=data["usage"]["prompt_tokens"],
            completion_tokens=data["usage"]["completion_tokens"],
        ),
    )
```

### 4.5 System Prompt 构建

```python
def _build_system_prompt(self, agent, skill, context):
    """Build system prompt from agent persona + rules + skill template."""
    parts = []

    # 1. Agent persona
    parts.append(agent.persona)

    # 2. Agent rules
    for rule in agent.rules:
        parts.append(f"## Rule: {rule.name}\n{rule.content}")

    # 3. Skill-specific instructions (if skill call)
    if skill:
        parts.append(f"## Current Task\n{skill.prompt_template}")

    # 4. Context info (current chapter, project genre, etc.)
    if context.get("genre"):
        parts.append(f"Story genre: {context['genre']}")
    if context.get("format_preset"):
        parts.append(f"## Output Format\n{context['format_preset']}")

    return "\n\n".join(parts)
```

## 5. Frontend — AI Chat Panel

### 5.1 Component

```
frontend/components/AIChatPanel.tsx — 通用 AI 聊天面板

Props:
  projectId: string
  contextType: 'script' | 'storyboard'
  contextId?: string            — script_id / storyboard_id
  onApplyContent?: (content: string, target: string) => void  — "应用到章节"回调
```

### 5.2 UI Structure

```
┌─────────────────────────────────┐
│ AI Chat          [Agent ▼] [+]  │  ← Agent 切换器 + 新建 Session
├─────────────────────────────────┤
│ Sessions:                       │  ← Session 列表（可折叠）
│  • Story Planning  (active)     │
│  • Chapter 1 Writing            │
│  • Review                       │
├─────────────────────────────────┤
│                                 │
│  User: 帮我把第2章对话改紧张      │
│                                 │
│  AI (Writer): 好的，以下是修改后   │
│  的对话内容：                     │
│  ...                            │
│  [Apply to Chapter] [Copy]      │  ← 操作按钮
│                                 │
│  User: 再加一些动作描写           │
│                                 │
│  AI (Writer): ...               │
│                                 │
├─────────────────────────────────┤
│ [📎 引用章节] [输入消息...]  [➤]  │  ← 输入栏
└─────────────────────────────────┘
```

### 5.3 Features

- **Agent 切换器**：下拉选择 Writer/Editor/Critic 等，切换后续回复的 Agent
- **Session 列表**：当前项目的所有 Session，点击切换，"+" 新建
- **引用章节**：点击 📎 选择当前 Script 的某个章节，作为上下文注入
- **应用到章节**：AI 回复的内容可以一键替换到指定章节的 TipTap 编辑器
- **Token 显示**：每条 AI 回复下方显示 token 消耗
- **Streaming**：支持流式输出（如果 LLM API 支持 SSE）

### 5.4 Integration in Script Editor

```
ScriptEditorPage:
┌──────────┬───────────────────────┬──────────┐
│ Assets   │       Canvas          │ AI Chat  │
│ Sidebar  │                       │ Panel    │
│          │                       │ (可折叠)  │
└──────────┴───────────────────────┴──────────┘
```

通过 EditorTopBar 或工具栏的 AI 图标按钮切换 Chat Panel 的显示/隐藏。

## 6. Script Editor Integration

现有的 AI 操作迁移到 Agent 框架：

| 现有功能 | 原实现 | 迁移后 |
|---------|-------|-------|
| generate_outline | script_ai_service.py 硬编码 prompt | AgentService.call_agent("writer", skill="generate_outline") |
| expand_chapter | script_ai_service.py 硬编码 prompt | AgentService.call_agent("writer", skill="expand_chapter") |
| create_branches | script_ai_service.py 硬编码 prompt | AgentService.call_agent("writer", skill="create_branches") |
| AI Chat | 无 | AgentService via ai_sessions/{id}/chat |
| AI Review | 无（新功能） | AgentService.call_agent("editor", skill="review_script") |
| AI Evaluate | 无（新功能） | AgentService.call_agent("critic", skill="evaluate_story") |

迁移后 `script_ai_service.py` 变为 AgentService 的薄封装，不再硬编码 prompt。

## 7. Token Tracking & Points

### 7.1 Cost Calculation

```python
# 按模型计算积分消耗
COST_PER_1K_TOKENS = {
    "qwen-max": {"input": 2, "output": 6},       # 积分/千 token
    "qwen-plus": {"input": 0.8, "output": 2},
    "qwen-turbo": {"input": 0.3, "output": 0.6},
}

def calculate_cost(model, prompt_tokens, completion_tokens):
    rates = COST_PER_1K_TOKENS.get(model, {"input": 1, "output": 3})
    return (prompt_tokens / 1000 * rates["input"] +
            completion_tokens / 1000 * rates["output"])
```

### 7.2 Usage Dashboard

前端统计面板（可在项目 Settings 或 Dashboard 中查看）：
- 今日/本周/本月 Token 消耗
- 按 Agent 分布（饼图）
- 按操作分布（柱状图）
- 消费趋势（折线图）

## 8. File Structure

### Backend (new)

```
backend/app/
├── services/
│   ├── agent_service.py           # AgentService 核心
│   └── ai_session_service.py      # Session 管理
├── api/
│   ├── ai_router.py               # Agent/Session/Chat API
│   └── ai_usage_router.py         # Token 统计 API
├── repositories/
│   ├── ai_agent_repository.py     # Agent CRUD
│   ├── ai_session_repository.py   # Session CRUD
│   └── ai_usage_repository.py     # 消费记录
└── schemas/
    └── ai.py                      # Pydantic 模型
```

### Backend (modified)

```
backend/app/services/script_ai_service.py  # 重构为 AgentService 的薄封装
backend/app/api/script_ai_router.py        # 迁移调用到 AgentService
```

### Frontend (new)

```
frontend/components/
├── AIChatPanel.tsx                # 通用 AI 聊天面板
├── AgentSelector.tsx              # Agent 切换器
└── SessionList.tsx                # Session 列表

frontend/services/
└── aiService.ts                   # AI API 调用封装
```

### Frontend (modified)

```
frontend/pages/ScriptEditor/ScriptEditorPage.tsx  # 集成 AIChatPanel
frontend/features/script/ExpandChapterDialog.tsx   # 迁移到 AgentService API
frontend/features/script/CreateStoryDialog.tsx     # 迁移到 AgentService API
```

### Database

```
supabase/migrations/117_ai_agent_framework.sql
```

## 9. Context Window Management

### 9.1 Truncation Strategy

qwen-max context window = 32K tokens。长对话必然超出，必须截断。

构建 messages 数组的策略：

```
1. system_prompt（Agent persona + rules + skill）     ← 始终保留
2. 最近 N 条消息（倒序取，保留最新的对话）               ← 动态截断
3. 可选：首条用户消息（保留任务初始上下文）               ← 如果空间允许
```

```python
MAX_CONTEXT_TOKENS = 28000  # 留 4K 给 completion

def _build_messages(self, agent, session, skill, context):
    system_prompt = self._build_system_prompt(agent, skill, context)
    system_tokens = estimate_tokens(system_prompt)

    remaining = MAX_CONTEXT_TOKENS - system_tokens
    messages = [{"role": "system", "content": system_prompt}]

    # 从最新到最旧加载消息，直到填满 token 预算
    history = await self._load_history(session.id, limit=50)
    history.reverse()  # 最旧在前

    selected = []
    token_sum = 0
    for msg in reversed(history):  # 从最新开始选
        msg_tokens = estimate_tokens(msg.content)
        if token_sum + msg_tokens > remaining:
            break
        selected.insert(0, msg)
        token_sum += msg_tokens

    messages.extend([{"role": m.role, "content": m.content} for m in selected])
    return messages
```

### 9.2 Session Token Counter

`ai_sessions` 表增加缓存字段，避免每次聚合查询：

```sql
ALTER TABLE ai_sessions ADD COLUMN total_tokens INT DEFAULT 0;
ALTER TABLE ai_sessions ADD COLUMN message_count INT DEFAULT 0;
```

每次消息写入时同步更新（单条 UPDATE，不需要额外查询）。

## 10. Security

### 10.1 Agent Persona Protection

Agent 分为两类：
- **预设 Agent**（系统创建）：`created_by = NULL`，persona 只读，普通用户不可修改
- **用户自定义 Agent**：`created_by = user_id`，仅创建者和团队管理员可修改

Persona 内容白名单校验（后端写入时检查）：
- 禁止包含 `"ignore"`, `"disregard"`, `"forget"` 等 prompt injection 关键词
- 最大长度限制：5000 字符
- 不允许包含 `<script>`, `javascript:` 等代码注入

### 10.2 Session Authorization

所有 Session 操作必须校验归属：

```python
async def _verify_session_access(self, session_id: str, user_id: str):
    session = await self.session_repo.get(session_id)
    if not session:
        raise HTTPException(404, "Session not found")
    if session.user_id != user_id:
        raise HTTPException(403, "Access denied")
    return session
```

### 10.3 Points Pre-check

调用 LLM 前先检查积分余额：

```python
async def call_agent(self, ...):
    # 预估消耗（按 max_tokens 上限估算）
    estimated_cost = calculate_cost(agent.model, 1000, agent.max_tokens)
    user_points = await self.points_service.get_balance(user_id)
    if user_points < estimated_cost:
        raise HTTPException(402, "Insufficient points")

    # 调用 LLM
    result = await self._call_llm(messages, agent.config)

    # 按实际消耗扣除
    actual_cost = calculate_cost(agent.model, result.usage.prompt_tokens, result.usage.completion_tokens)
    await self.points_service.deduct(user_id, actual_cost)
```

### 10.4 Input Validation

- 用户消息最大长度：10000 字符
- config_json schema 校验：只允许 `model`, `temperature`, `max_tokens` 字段
- Agent Rules 最大条目数：20 条/Agent

## 11. Streaming Support

### 11.1 Backend SSE Endpoint

```python
@router.post("/ai/sessions/{session_id}/chat/stream")
async def chat_stream(session_id: str, body: ChatRequest, user: AuthDep):
    """Server-Sent Events streaming endpoint."""

    async def event_generator():
        async with httpx.AsyncClient() as client:
            async with client.stream(
                "POST",
                f"{settings.LLM_API_URL}/chat/completions",
                json={**payload, "stream": True},
                headers=headers,
            ) as resp:
                full_content = ""
                async for line in resp.aiter_lines():
                    if line.startswith("data: "):
                        chunk = json.loads(line[6:])
                        if chunk["choices"][0].get("delta", {}).get("content"):
                            text = chunk["choices"][0]["delta"]["content"]
                            full_content += text
                            yield f"data: {json.dumps({'content': text})}\n\n"

                # 流结束后保存完整消息 + 记录 token
                await save_message(session_id, "assistant", full_content)
                await log_usage(...)
                yield f"data: {json.dumps({'done': True})}\n\n"

    return StreamingResponse(event_generator(), media_type="text/event-stream")
```

### 11.2 Frontend EventSource

```typescript
function streamChat(sessionId: string, message: string, onChunk: (text: string) => void) {
  const eventSource = new EventSource(`${API_BASE}/api/v1/ai/sessions/${sessionId}/chat/stream`);
  // 或使用 fetch + ReadableStream 处理 POST 请求的 SSE
}
```

### 11.3 Non-streaming Fallback

Streaming 为可选功能。默认走非 streaming 的 `POST /chat` 端点。前端根据用户偏好或模型能力选择。

## 12. Error Handling

### 12.1 LLM 调用失败

| 错误 | 处理 | 用户看到 |
|------|------|---------|
| 网络超时 | 重试 2 次（指数退避） | "AI service is slow, retrying..." |
| 429 Rate Limit | 等待 Retry-After + 重试 | "AI service is busy, please wait..." |
| 500 Server Error | 不重试，返回错误 | "AI service unavailable" |
| 无效 JSON 响应 | 不重试，记录日志 | "AI returned invalid response" |
| 内容过滤 | 不重试 | "Content was filtered by safety policy" |

### 12.2 Pipeline 错误

链式调用中间失败时：
- 已消耗的 token 正常记录（不回滚积分）
- 返回到失败步骤之前的最后有效结果
- 前端显示"Pipeline partially completed: step N failed"

### 12.3 消息保存失败

LLM 已返回但数据库写入失败时：
- 返回结果给用户（不阻塞）
- 异步重试消息保存（3 次）
- 失败后记录到 application_logs

## 13. AI Chat Panel — Interaction States

| 状态 | UI |
|------|-----|
| Empty (新 Session) | 居中 sparkles 图标 + "Start a conversation" + 建议操作按钮 |
| Loading (等待 AI) | 消息区底部 TypingIndicator 动画 |
| Streaming | 消息逐字显示，底部有 "Stop generating" 按钮 |
| Error (发送失败) | 消息旁红色提示 + "Retry" 按钮 |
| Session loading | 消息区骨架屏 |
| Token 不足 | 输入框禁用 + 提示 "Insufficient points" |
| Apply success | Toast "Content applied to Chapter N" + Undo 按钮（5秒内） |
| Apply fail | Toast error |

### 13.1 Message Bubble 设计

每条 AI 消息显示：
- **Agent 名称标签**（如 "Writer", "Editor"）在消息气泡顶部
- 消息内容
- Token 消耗（小字灰色）
- 操作按钮：[Apply to Chapter] [Copy]

### 13.2 Apply to Chapter 流程

1. 用户点击 "Apply to Chapter"
2. 弹出章节选择 Popover（列出当前 Script 的所有章节）
3. 默认选中当前正在编辑的章节（如果有）
4. 点击章节 → 内容替换到 TipTap 编辑器
5. Toast 提示 "Applied to Chapter N" + Undo 按钮（5 秒）
6. Undo 恢复原内容

### 13.3 Session List Item

每个 Session 显示：
- 标题（可编辑）
- 最后消息时间（relative: "2h ago"）
- 消息条数
- 当前 Session 高亮

### 13.4 Shared Chat Components

从现有 Storyboard ChatPanel 提取共用组件：

```
frontend/components/chat/
├── MessageBubble.tsx         — 消息气泡（复用）
├── TypingIndicator.tsx       — 打字指示器（复用）
├── ChatInput.tsx             — 输入框 + 发送按钮（复用）
└── EmptyState.tsx            — 空 Session 状态（复用）
```

AIChatPanel 和 Storyboard ChatPanel 都引用这些共享组件。

## 14. Review Findings (已整合)

- [x] 上下文截断策略 (Section 9)
- [x] Persona 注入防护 + Agent 权限 (Section 10.1)
- [x] Session 归属鉴权 (Section 10.2)
- [x] 积分预检 (Section 10.3)
- [x] Streaming 实现方案 (Section 11)
- [x] 输入长度限制 + config schema 校验 (Section 10.4)
- [x] LLM 调用重试策略 (Section 12.1)
- [x] Pipeline 错误处理 (Section 12.2)
- [x] 交互状态覆盖 (Section 13)
- [x] Message Bubble 显示 Agent 名称 (Section 13.1)
- [x] Apply to Chapter 流程 + Undo (Section 13.2)
- [x] Session 列表信息补充 (Section 13.3)
- [x] 共享 Chat 组件 (Section 13.4)
- [x] ai_sessions 增加 total_tokens + message_count (Section 9.2)

## 15. Migration Strategy

分阶段迁移，不破坏现有功能：

**Phase 1**：创建数据库表 + AgentService + 预设 Agent
**Phase 2**：AI Chat Panel 前端组件
**Phase 3**：Script Editor 现有 AI 操作迁移到 AgentService
**Phase 4**：Token 追踪 + 积分联动
**Phase 5**：Storyboard 模块接入
**Phase 6**：Usage Dashboard
