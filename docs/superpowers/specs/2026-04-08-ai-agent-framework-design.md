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

## 9. Migration Strategy

分阶段迁移，不破坏现有功能：

**Phase 1**：创建数据库表 + AgentService + 预设 Agent
**Phase 2**：AI Chat Panel 前端组件
**Phase 3**：Script Editor 现有 AI 操作迁移到 AgentService
**Phase 4**：Token 追踪 + 积分联动
**Phase 5**：Storyboard 模块接入
**Phase 6**：Usage Dashboard
