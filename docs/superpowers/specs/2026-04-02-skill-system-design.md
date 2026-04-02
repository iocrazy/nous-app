# Skill System — Design Spec

> Date: 2026-04-02
> Status: Revised (post-review)
> Reviews: CEO Review + Eng Review + Design Review (2026-04-02)

## Overview

User-creatable AI Skill system for MediaHub. Skills are Markdown-formatted rule packages stored in the database, injected into AI prompts when creating content (scripts, storyboards, copywriting, etc.).

## Migration Strategy: `style_templates` → `skills`

### Background

`style_templates` (migration 113) already implements ~65% of Skill functionality:

| Existing (`style_templates`) | Skill System adds |
|------------------------------|-------------------|
| name, description, prompt_content | trigger_keywords, input_params, output_format |
| team_id, is_public, created_by | project_id (project-level scope) |
| category | status (active/archived), sort_order |

### Decision: ALTER TABLE + Rename

**Approach A (chosen)**: Extend `style_templates` with new columns, rename to `skills`. This avoids DRY violation and preserves existing data.

Migration plan:
1. `ALTER TABLE style_templates ADD COLUMN trigger_keywords TEXT[] DEFAULT '{}'`
2. `ALTER TABLE style_templates ADD COLUMN project_id BIGINT REFERENCES projects(id)`
3. `ALTER TABLE style_templates ADD COLUMN status VARCHAR(20) DEFAULT 'active'`
4. `ALTER TABLE style_templates ADD COLUMN icon VARCHAR(20) DEFAULT '✨'`
5. `ALTER TABLE style_templates ADD COLUMN output_format TEXT`
6. `ALTER TABLE style_templates RENAME COLUMN prompt_content TO content_md`
7. `ALTER TABLE style_templates RENAME TO skills`
8. Update all existing routes/services to use new name
9. Deprecate `/api/v1/style-templates` (redirect to `/api/v1/skills`)

**Frontend**: `StyleTemplate` type in `types.ts:824` → rename to `Skill`, `styleTemplateService.ts` → merge into `skillService.ts`.

### NOT migrating (v1)

- `input_params JSONB` — UI complexity too high, defer to v2
- `sort_order INT` — defer, use `created_at` ordering for v1

## Core Concepts

- **Skill** = a set of creative rules in Markdown, stored in DB
- **Skill ≠ Model** — Skill defines rules/templates, Model is selected separately by user at execution time
- **Two-layer scope** — global Skills (team-shared) + project-level Skills (can override global)
- **Trigger** — manual selection only (v1); auto-suggestion deferred to v2

## Database Schema

### `skills` table (migrated from `style_templates`)

| Column | Type | Description | Source |
|--------|------|-------------|--------|
| `id` | BIGINT (Snowflake) | Primary key | existing |
| `team_id` | BIGINT | Owner team (NULL = system preset) | existing |
| `project_id` | BIGINT | NULL = global, non-null = project-specific | **new** |
| `created_by` | UUID | Creator user | existing |
| `name` | TEXT | Skill name (unique within scope) | existing |
| `description` | TEXT | Short description for listing/matching | existing |
| `category` | VARCHAR(50) | Category: script, storyboard, copywriting, general | existing |
| `icon` | VARCHAR(20) | Emoji or icon identifier | **new** |
| `content_md` | TEXT | Full rule content in Markdown (max 10,000 chars) | renamed from `prompt_content` |
| `trigger_keywords` | TEXT[] | Keywords for auto-matching (v2) | **new** |
| `output_format` | TEXT | Output format specification in Markdown | **new** |
| `is_public` | BOOLEAN | Visible to other team members | existing |
| `status` | VARCHAR(20) | `active` / `archived` | **new** |
| `created_at` | TIMESTAMPTZ | | existing |
| `updated_at` | TIMESTAMPTZ | | existing |

### Required Indexes

```sql
CREATE INDEX idx_skills_team_id ON skills(team_id) WHERE status = 'active';
CREATE INDEX idx_skills_project_id ON skills(project_id) WHERE project_id IS NOT NULL;
CREATE INDEX idx_skills_status ON skills(status);
-- For v2 auto-matching:
-- CREATE INDEX idx_skills_trigger_keywords ON skills USING GIN(trigger_keywords);
```

### Content Length Limits

- `content_md`: max 10,000 characters (~2,500 tokens). Enforced both in Pydantic schema (`max_length=10000`) and frontend character counter.
- `description`: max 2,000 characters (existing)
- `name`: max 200 characters (existing)

### Scope Resolution

Priority (highest first):
1. Project-level Skill (same name) → overrides global
2. Global Skill (team-level, `project_id = NULL`)
3. System presets (`team_id = NULL`)

### Authorization (Application-Layer, NOT RLS)

Following existing `style_templates_router.py` pattern (no RLS in codebase):

- **Read**: team members can read their team's skills + system presets (team_id = NULL) + is_public skills
- **Create**: any team member, `team_id` forced to current user's team (server-side)
- **Update/Delete**: only creator (`created_by = current_user`) or team admin
- **System presets protection**: DELETE blocked when `team_id = NULL` (router-level check)

## Skill → LLM Injection Path (CRITICAL)

### Architecture

```
Frontend                          Backend
────────                          ───────
ChatPanel                         sb_ai_router.py
  │ selectedSkillId               ┌──────────────────────┐
  │                               │ ChatRequest          │
  └─► chatWithAI(                 │   project_id: str    │
        projectId,                │   message: str       │
        message,                  │   selected_frame_id? │
        skillId?  ◄── NEW         │   skill_id?: str ◄── NEW
      )                           └──────┬───────────────┘
                                         │
                                  StoryboardAIService.chat()
                                         │
                                  ┌──────▼───────────────┐
                                  │ if skill_id:         │
                                  │   skill = repo.get() │
                                  │   if not skill:      │
                                  │     → ignore, no-skill│
                                  │   validate ownership │
                                  │   prepend to prompt  │
                                  └──────┬───────────────┘
                                         │
                                  system_prompt =
                                    <skill>\n
                                    {skill.content_md}\n
                                    </skill>\n\n
                                    {existing_storyboard_prompt}
                                         │
                                         ▼
                                  _call_llm(messages)
```

### Changes Required

**1. Frontend — `storyboardService.ts`**

```typescript
// chatWithAI signature change
export async function chatWithAI(
  projectId: string,
  message: string,
  frameId?: string,
  skillId?: string   // NEW
): Promise<ChatResponse>
```

**2. Backend — `sb_ai_router.py`**

```python
class ChatRequest(BaseModel):
    project_id: str
    message: str = Field(..., min_length=1, max_length=4000)
    selected_frame_id: Optional[str] = None
    skill_id: Optional[str] = None  # NEW
```

**3. Backend — `storyboard_ai_service.py`**

```python
async def chat(
    self,
    project_id: str,
    message: str,
    selected_frame_id: Optional[str] = None,
    skill_id: Optional[str] = None,  # NEW
) -> Dict[str, Any]:
    # ... existing code ...
    
    # Skill injection (before building system_prompt)
    skill_instruction = ""
    if skill_id:
        skill = await skill_repo.get_by_id(skill_id)
        if skill and skill.get("status") == "active":
            skill_instruction = (
                f"<skill>\n{skill['content_md']}\n</skill>\n\n"
            )
    
    system_prompt = skill_instruction + existing_system_prompt
```

### Prompt Injection Defense

User-authored `content_md` is free-text injected into LLM system prompt. Defenses:

1. **Delimiter isolation**: Wrap in `<skill>...</skill>` tags so the LLM can distinguish user-authored rules from system instructions
2. **Length limit**: `content_md` max 10,000 chars enforced at schema level
3. **No escalation**: Skill content is prepended before (not after) the system role instructions, so system instructions take precedence
4. **Scope**: Skills only affect the current user's own conversations, not other users'

### Future: Script AI Integration (v2)

`script_ai_service.py` already has a `style_guide: Optional[str]` parameter in `split_script()`. In v2, this can be connected to Skills by passing `skill.content_md` as `style_guide`.

## Trigger Mechanism (v1: Manual Only)

### Manual Selection

- ChatPanel header: dropdown "Select Skill" → list of available Skills
- Grouped by scope: "Project Skills" / "Global Skills" / "System Presets"
- Selected Skill stays active for the conversation until changed
- Search filter in dropdown

### Auto-Matching (DEFERRED to v2)

~~On user message input, match against `trigger_keywords`~~ — deferred due to:
- Performance risk (API call per keystroke or local matching complexity)
- False-positive risk (simple keyword inclusion produces irrelevant suggestions)
- v1 focuses on manual selection to validate core value first

## Execution Flow

```
1. User types message in ChatPanel
2. If Skill selected (manual):
   a. Frontend sends skill_id in ChatRequest
   b. Backend looks up skill, validates status = 'active'
   c. If skill found and active:
      - Prepend <skill>{content_md}</skill> to system_prompt
      - Append output_format instruction (if set)
   d. If skill not found or archived:
      - Log warning, proceed without skill (graceful degradation)
   e. Send to user's configured model
   f. Return response
3. If no Skill:
   - Send user message directly to model (no injection)
```

## Frontend UI

### 1. Project Sidebar — "Skills" nav item

- Added to `ProjectNavSidebar` NAV_SECTIONS array
- Position: between "Scripts" and "Storyboard" (creative tools cluster, not infra)
- Icon: `Wand2` from lucide-react
- Badge count: project-specific skills only (not global, to be meaningful)

### 2. Skills Management Page (`/team/:teamId/projects/:projectId/skills`)

- **Route**: add to App.tsx router
- **Header**: "Skills" + count + "+ New Skill" button
- **Filter tabs**: All / Scripts / Storyboard / Copywriting / General
- **Grouping**: Two sections with dividers:
  - "Project Skills" — editable, project-specific
  - "Global & System" — global team skills + system presets (read-only for presets)
- **Card design**: Reuse `ProjectScriptsTab` card pattern BUT with visual differentiation:
  - Skill-specific color: violet/purple badge (amber = scripts, indigo = storyboard)
  - "Config" visual language: subtle gear/wand icon overlay to signal "this is a tool, not content"
  - Scope indicator: 🌐 Global / 📁 Project (colored badges, not just text)
- Click card → open Skill Editor (full page)
- Card actions: duplicate, archive (long-press on mobile, hover menu on desktop)

### Interaction States

| Feature | Loading | Empty | Error | Success | Partial |
|---------|---------|-------|-------|---------|---------|
| Skills list | Skeleton cards (reuse ProjectScriptsTab pattern) | Warm empty state: Wand2 icon + "No skills yet" + "Create your first skill to standardize your AI outputs" + CTA button | Retry button + toast | Card grid | N/A |
| Skill save | Button spinner + disabled | N/A | Inline field errors (red border + message) | Toast "Skill saved" + redirect to list | Field validation errors shown per-field |
| Skill delete | Dimmed card + spinner | N/A | Toast error | Card removed with animation | N/A |

### 3. Skill Editor (Full Page Route)

**Decision: Full page route** (not modal) — because:
- 9+ fields including a large Markdown editor don't fit in a modal
- Mobile must work (modal on 375px = unusable)
- Deep-linkable URL: `/team/:teamId/projects/:projectId/skills/:skillId/edit`

**Layout: Two-column on desktop, single-column on mobile**

```
┌─────────────────────────────────────────────────────┐
│ [← Back to Skills]              [Save] [Cancel]     │
├────────────────────────┬────────────────────────────┤
│  LEFT COLUMN (meta)    │  RIGHT COLUMN (content)    │
│                        │                            │
│  Name ___________      │  Content (Markdown)        │
│  Category [▼ dropdown] │  ┌────────────────────┐    │
│  Description ____      │  │                    │    │
│  Icon [emoji picker]   │  │  Full-width editor │    │
│                        │  │  with preview       │    │
│  ── Scope ──           │  │  toggle            │    │
│  ○ Global              │  │                    │    │
│  ○ This Project Only   │  │  {char_count}/10000│    │
│                        │  └────────────────────┘    │
│  ── Visibility ──      │                            │
│  [Toggle] Public       │  Output Format (optional)  │
│                        │  ┌────────────────────┐    │
│  ── Keywords (v2) ──   │  │ Smaller MD editor  │    │
│  [tag1] [tag2] [+]     │  └────────────────────┘    │
│  (disabled in v1)      │                            │
├────────────────────────┴────────────────────────────┤
│  Unsaved changes warning on navigate-away           │
└─────────────────────────────────────────────────────┘
```

**Dirty state**: Track form changes, show "Unsaved changes" warning on browser back/navigate-away.

**Markdown editor**: Use `@uiw/react-md-editor` (lightweight, already supports preview toggle). New dependency.

### 4. ChatPanel Integration

```
ChatPanel (w-80)
┌─────────────────────────┐
│ AI Assistant    [Skill▼] │  ← Compact: skill selector in header row
├─────────────────────────┤
│                         │
│   [Messages area]        │
│   (flex-1, overflow)    │
│                         │
├─────────────────────────┤
│ Using: Script Master [×] │  ← Only shown when skill active
├─────────────────────────┤
│ [Input + Send btn]      │
└─────────────────────────┘
```

- **Skill selector**: Compact button in header row (not a separate row), opens dropdown overlay
- **Dropdown design**: Grouped by scope (🌐 Global / 📁 Project / ⚙️ System), with search
- **Active badge**: Below messages area, above input. Dismissable (click × to deselect)
- **No auto-suggestion in v1** — removes layout reflow risk

### Responsive Behavior

| Viewport | Skills Page | Skill Editor | ChatPanel |
|----------|------------|--------------|-----------|
| Desktop (>1024px) | Card grid (flex-wrap) | Two-column layout | w-80 sidebar |
| Tablet (768-1024) | 2-column cards | Single column, stacked | Full-width overlay |
| Mobile (<768px) | Single column cards | Single column, full screen | Full-width overlay |

### Accessibility

- Skill selector dropdown: `role="combobox"`, `aria-expanded`, `aria-activedescendant`, keyboard nav (↑↓ Enter Escape)
- Active skill badge: `role="status"`, `aria-live="polite"`
- Skill cards: keyboard focusable, Enter to open, action menu via keyboard
- Card actions on touch: long-press menu (no hover dependency)
- Contrast: all text meets WCAG AA (4.5:1 ratio)
- Touch targets: minimum 44px

## Backend API

### Endpoints (under `/api/v1/skills`)

| Method | Path | Description |
|--------|------|-------------|
| GET | `/skills` | List skills (filter by project_id, category, search). Returns **summary** (no content_md) |
| GET | `/skills/:id` | Get skill **detail** (includes content_md, output_format) |
| POST | `/skills` | Create skill |
| PATCH | `/skills/:id` | Update skill |
| DELETE | `/skills/:id` | Soft delete (set status = 'archived') |
| GET | `/skills/categories` | List available categories (hardcoded enum) |

**Removed**: ~~`POST /skills/match`~~ — keyword matching deferred to v2, will be frontend-local.

**Deprecated**: `/api/v1/style-templates` — redirect to `/api/v1/skills` with 301.

### Response Schemas

```python
# List response (GET /skills) — no content_md
class SkillSummary(BaseModel):
    id: str  # SnowflakeId
    name: str
    description: Optional[str]
    category: Optional[str]
    icon: str
    is_public: bool
    status: str
    team_id: Optional[str]
    project_id: Optional[str]
    created_by: Optional[str]
    created_at: str
    updated_at: str

# Detail response (GET /skills/:id) — full content
class SkillDetail(SkillSummary):
    content_md: str
    output_format: Optional[str]
    trigger_keywords: List[str]
```

### Backend Architecture

```
Router (api/skills_router.py)
  ↓
Repository (repositories/skill_repository.py) — extends BaseRepository
  ↓                                              inherits create/update/get_by_id/soft_delete
  ↓                                              adds list_skills(team_id, project_id, category)
Schema (schemas/skill.py) — SkillCreate, SkillUpdate, SkillSummary, SkillDetail
```

Note: No separate Service layer for v1 — scope resolution logic is simple enough for the router/repository. If scope resolution grows complex (v2 auto-matching), extract to `SkillService`.

### Error Handling

| Method | Failure | Exception | User Sees | HTTP |
|--------|---------|-----------|-----------|------|
| POST /skills | Name conflict (same scope) | Postgres 23505 | "Skill name already exists" | 409 |
| POST /skills | content_md > 10000 chars | Pydantic ValidationError | "Content too long (max 10,000 chars)" | 422 |
| PATCH /skills/:id | Not owner | OwnershipError | "Access denied" | 403 |
| DELETE /skills/:id | System preset (team_id=NULL) | Blocked in router | "Cannot delete system presets" | 403 |
| GET /skills/:id | Not found / archived | None result | "Skill not found" | 404 |
| chat() with skill_id | Skill archived/deleted | None result | Graceful degradation (no skill injected), log warning | 200 (no error) |
| chat() with skill_id | content_md causes LLM overflow | LLM API 400 | "AI request failed, try a shorter skill" | 500 |

## System Presets

Initialized via migration SQL (`INSERT ... ON CONFLICT DO NOTHING`):

| Name | Category | Description |
|------|----------|-------------|
| Short Video Script | script | 30-60s short-form video script structure |
| Film Storyboard | storyboard | Professional storyboard format with shot types |
| Product Copywriting | copywriting | AIDA structure marketing copy |
| Social Media Post | copywriting | Platform-specific social content |
| Script to Storyboard | storyboard | Convert written script to visual storyboard |

## Files to Create/Modify

### Database
- `supabase/migrations/XXX_skills_migrate_from_style_templates.sql` — ALTER TABLE + rename + indexes + seed presets

### Backend (modify existing + create new)
- `backend/app/schemas/skill.py` — **new** Pydantic models
- `backend/app/repositories/skill_repository.py` — **new** (extends BaseRepository, replaces StyleTemplateRepository)
- `backend/app/api/skills_router.py` — **new** (replaces style_templates_router.py)
- `backend/app/api/sb_ai_router.py` — **modify** ChatRequest + chat endpoint
- `backend/app/services/storyboard_ai_service.py` — **modify** chat() to accept skill_id
- `backend/app/main.py` — **modify** register skills_router, deprecate style_templates_router

### Frontend (modify existing + create new)
- `frontend/types.ts` — **modify** rename StyleTemplate → Skill, add new fields
- `frontend/services/skillService.ts` — **new** (replaces styleTemplateService.ts)
- `frontend/services/storyboardService.ts` — **modify** chatWithAI signature
- `frontend/components/project/ProjectSkillsTab.tsx` — **new** management page
- `frontend/components/project/SkillEditor.tsx` — **new** editor page
- `frontend/features/storyboard/ui/ChatPanel.tsx` — **modify** add SkillSelector
- `frontend/components/project/ProjectNavSidebar.tsx` — **modify** add Skills nav item
- `frontend/App.tsx` — **modify** add skills routes
- `frontend/public/locales/en.json` — **modify** add skill i18n keys
- `frontend/public/locales/zh.json` — **modify** add skill i18n keys

### Deprecated (to remove after migration)
- `backend/app/api/style_templates_router.py` — redirect to skills_router
- `backend/app/repositories/style_template_repository.py` — replaced
- `backend/app/schemas/style_template.py` — replaced
- `frontend/services/styleTemplateService.ts` — replaced

## Test Plan

### Backend Tests

```
skills_router tests:
  POST /skills
    ├── [UNIT] Create skill with valid data → 201
    ├── [UNIT] Create with duplicate name (same scope) → 409
    ├── [UNIT] Create with content_md > 10000 chars → 422
    ├── [UNIT] Create without auth → 401
    └── [UNIT] Create with cross-team project_id → 403

  GET /skills
    ├── [UNIT] List returns summary (no content_md)
    ├── [UNIT] Filter by project_id returns project + global + system
    ├── [UNIT] Filter by category
    └── [UNIT] Archived skills excluded

  GET /skills/:id
    ├── [UNIT] Returns full detail (with content_md)
    └── [UNIT] Not found → 404

  PATCH /skills/:id
    ├── [UNIT] Update by owner → 200
    ├── [UNIT] Update by non-owner → 403
    └── [UNIT] Update system preset → 403

  DELETE /skills/:id
    ├── [UNIT] Soft delete by owner → 200
    ├── [UNIT] Delete system preset (team_id=NULL) → 403
    └── [UNIT] Delete by non-owner → 403

skill injection (integration):
  POST /storyboard/{projectId}/chat
    ├── [INTEGRATION] Chat with valid skill_id → skill content in system_prompt
    ├── [INTEGRATION] Chat with archived skill_id → graceful degradation
    ├── [INTEGRATION] Chat with invalid skill_id → graceful degradation
    └── [INTEGRATION] Chat without skill_id → normal behavior
```

### Frontend Tests

```
SkillSelector (ChatPanel):
  ├── [UNIT] Renders dropdown with grouped skills
  ├── [UNIT] Selecting skill shows active badge
  ├── [UNIT] Dismissing badge clears selection
  ├── [UNIT] Keyboard navigation (↑↓ Enter Escape)
  └── [UNIT] skillId passed to chatWithAI on send

SkillEditor:
  ├── [UNIT] Form validation (empty name, content_md too long)
  ├── [UNIT] Dirty state warning on navigate-away
  ├── [UNIT] Save success → redirect to list
  └── [UNIT] Save error → inline error display

ProjectSkillsTab:
  ├── [UNIT] Loading skeleton display
  ├── [UNIT] Empty state with CTA
  ├── [UNIT] Card grouping (project / global+system)
  └── [UNIT] Archive action
```

## Worktree Parallelization

| Lane | Worktree | Tasks | Dependencies |
|------|----------|-------|-------------|
| A | `agent-skill-backend` | Migration + Schema + Repository + Router + AI injection | None |
| B | `agent-skill-ui` | ProjectSkillsTab + SkillEditor + NavSidebar + Routes | Depends on `Skill` type (write types.ts first) |
| C | `agent-skill-chat` | ChatPanel SkillSelector + storyboardService change | Depends on `Skill` type, parallel with B |

Lane B and C can start in parallel once `Skill` interface is defined in `types.ts`.

## Out of Scope (v1)

- Skill versioning / history
- Skill marketplace / sharing across teams
- Chained Skills (pipeline of multiple Skills)
- Skill execution analytics (but log basic usage for future analysis)
- AI-powered Skill creation wizard
- Auto-matching / trigger_keywords UI (fields exist in DB, disabled in frontend)
- `input_params` JSONB parameter forms (field NOT added to DB in v1)
- Script AI integration (`script_ai_service.py` style_guide connection)
- Skill content → canvas/script "apply" action (copy from chat is acceptable for v1)
