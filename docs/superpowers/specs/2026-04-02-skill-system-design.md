# Skill System — Design Spec

> Date: 2026-04-02
> Status: Approved

## Overview

User-creatable AI Skill system for MediaHub. Skills are Markdown-formatted rule packages stored in the database, injected into AI prompts when creating content (scripts, storyboards, copywriting, etc.).

## Core Concepts

- **Skill** = a set of creative rules in Markdown, stored in DB
- **Skill ≠ Model** — Skill defines rules/templates, Model is selected separately by user at execution time
- **Two-layer scope** — global Skills (team-shared) + project-level Skills (can override global)
- **Trigger** — manual selection + automatic keyword matching

## Database Schema

### `skills` table

| Column | Type | Description |
|--------|------|-------------|
| `id` | BIGINT (Snowflake) | Primary key |
| `team_id` | BIGINT | Owner team (NULL = system preset) |
| `project_id` | BIGINT | NULL = global, non-null = project-specific |
| `created_by` | UUID | Creator user |
| `name` | VARCHAR(100) | Skill name (unique within scope) |
| `description` | TEXT | Short description for listing/matching |
| `category` | VARCHAR(50) | Category: script, storyboard, copywriting, general, etc. |
| `icon` | VARCHAR(20) | Emoji or icon identifier |
| `content_md` | TEXT | Full rule content in Markdown (system prompt + examples) |
| `trigger_keywords` | TEXT[] | Keywords for auto-matching |
| `input_params` | JSONB | Parameter definitions: `[{name, type, label, default, required}]` |
| `output_format` | TEXT | Output format specification in Markdown |
| `is_public` | BOOLEAN | Visible to other team members |
| `sort_order` | INT | Display order |
| `status` | VARCHAR(20) | `active` / `archived` |
| `created_at` | TIMESTAMPTZ | |
| `updated_at` | TIMESTAMPTZ | |

### Scope Resolution

Priority (highest first):
1. Project-level Skill (same name) → overrides global
2. Global Skill (team-level, `project_id = NULL`)
3. System presets (`team_id = NULL`)

### RLS Policies

- `service_role`: full access
- `authenticated`: SELECT where `team_id` in user's teams, or `is_public = true`
- INSERT/UPDATE/DELETE: only own team's skills

## Trigger Mechanism

### Manual Selection

- ChatPanel header: dropdown "Select Skill" → list of available Skills
- Grouped by category, showing name + short description
- Selected Skill stays active for the conversation until changed

### Auto-Matching

- On user message input, match against `trigger_keywords` of all available Skills
- Show suggestion chip: "Suggested: {skill_name}" — user can accept or ignore
- Matching logic: simple keyword inclusion (case-insensitive)
- Max 2 suggestions shown at a time

## Execution Flow

```
1. User types message in ChatPanel
2. If Skill selected (manual) or matched (auto):
   a. Build prompt:
      - System: Skill.content_md
      - System: "Output format: " + Skill.output_format (if set)
      - System: "Parameters: " + resolved input_params (if any)
      - User: user's message
   b. Send to user's configured model (from AI Settings)
   c. Return response
3. If no Skill:
   - Send user message directly to model (no injection)
```

## Frontend UI

### 1. Project Sidebar — "Skills" nav item

- Added to ProjectNavSidebar between "Tasks" and "Shares"
- Icon: `Wand2` or `Sparkles` from lucide-react
- Badge count: number of Skills in this project + global

### 2. Skills Management Page

- Header: "Skills" + count + "+ New Skill" button
- Filter tabs: All / Scripts / Storyboard / Copywriting / General
- Card list (same style as Storyboard/Script cards):
  - Icon + name
  - Category badge
  - Description snippet
  - Scope badge: "Global" or "Project"
  - Modified / Created dates
- Click card → edit Skill
- Card actions: duplicate, archive, delete

### 3. Skill Editor (modal or full page)

- **Name** — text input
- **Category** — dropdown (script, storyboard, copywriting, general, custom)
- **Description** — text input (short, for listing)
- **Icon** — emoji picker or preset icons
- **Content** — Markdown editor (main rule body, includes examples)
  - Full-width, resizable
  - Preview toggle (rendered Markdown)
- **Output Format** — smaller Markdown editor
- **Trigger Keywords** — tag input (comma-separated)
- **Input Parameters** — repeatable rows:
  - Name, Type (text/number/select/boolean), Label, Default, Required
- **Scope** — radio: Global / This Project Only
- **Visibility** — toggle: Public / Private

### 4. ChatPanel Integration

- Top of ChatPanel: Skill selector dropdown
  - "No Skill" (default)
  - Grouped by category
  - Search filter
  - Shows scope icon (🌐 global / 📁 project)
- When Skill selected: show badge below input "Using: {skill_name}"
- Auto-suggestion: when keywords match, show dismissable chip above input

## Backend API

### Endpoints (under `/api/v1/skills`)

| Method | Path | Description |
|--------|------|-------------|
| GET | `/skills` | List skills (filter by project_id, category, search) |
| GET | `/skills/:id` | Get skill detail |
| POST | `/skills` | Create skill |
| PATCH | `/skills/:id` | Update skill |
| DELETE | `/skills/:id` | Delete (soft) |
| POST | `/skills/match` | Match skills by keywords (for auto-suggestion) |
| GET | `/skills/categories` | List available categories |

### Backend Architecture

```
Router (api/skills_router.py)
  ↓
Service (services/skill_service.py) — scope resolution, matching
  ↓
Repository (repositories/skill_repository.py) — CRUD
  ↓
Schema (schemas/skill.py) — Pydantic models
```

## System Presets

Ship with built-in Skills (team_id = NULL, is_public = true):

| Name | Category | Description |
|------|----------|-------------|
| Short Video Script | script | 30-60s short-form video script structure |
| Film Storyboard | storyboard | Professional storyboard format with shot types |
| Product Copywriting | copywriting | AIDA structure marketing copy |
| Social Media Post | copywriting | Platform-specific social content |
| Script to Storyboard | storyboard | Convert written script to visual storyboard |

## Files to Create

### Backend
- `supabase/migrations/XXX_skills_schema.sql`
- `backend/app/schemas/skill.py`
- `backend/app/repositories/skill_repository.py`
- `backend/app/services/skill_service.py`
- `backend/app/api/skills_router.py`

### Frontend
- `frontend/types.ts` — add Skill interface
- `frontend/services/skillService.ts` — API layer
- `frontend/components/project/ProjectSkillsTab.tsx` — management page
- `frontend/components/project/SkillEditor.tsx` — editor modal
- `frontend/features/storyboard/ui/ChatPanel.tsx` — add Skill selector (modify existing)

### Sidebar
- `frontend/components/project/ProjectNavSidebar.tsx` — add "Skills" nav item

## Out of Scope (v1)

- Skill versioning / history
- Skill marketplace / sharing across teams
- Chained Skills (pipeline of multiple Skills)
- Skill execution analytics
- AI-powered Skill creation wizard
