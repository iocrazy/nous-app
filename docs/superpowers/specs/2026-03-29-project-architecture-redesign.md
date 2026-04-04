# Project Architecture Redesign

**Date:** 2026-03-29
**Branch:** `project-arch`
**Status:** Approved

## Overview

Restructure MediaHub's project system from isolated modules (Projects + Storyboard Workbench) into a unified project container architecture. Each project becomes a creative workspace containing Files, Scripts, Storyboard, and Output as sub-modules. Add a new Script Editor module with AI-powered story generation, chapter editing, branching, and storyboard linkage.

## Key Decisions

| Decision | Choice | Rationale |
|----------|--------|-----------|
| Architecture | Project as top-level container with sub-modules | Unified creative workspace |
| Entry point | Merge "Storyboard Workbench" into "Projects" | Single entry, no user confusion |
| Existing Projects UI | Reuse and optimize, add Tabs for sub-modules | Lowest cost, existing UI is solid |
| Tab structure | Main: Files / Scripts / Storyboard / Output; Secondary: Tasks / Share / Recycle | Focus on creative flow |
| Script Editor | ReactFlow node canvas, chapter tree structure | Same tech as Storyboard, consistent UX |
| Script-to-Storyboard | Linked — chapters can be converted to storyboard frames | Core creative workflow |
| Project:Script:Storyboard | 1:N:N — multiple scripts and storyboards per project | Flexible versioning |
| ID system | Snowflake BIGINT (DB) + display_code (UI) | Consistency + readability |
| Display code format | `{TYPE}-YYYYMM{SEQ}`, monthly reset per team | Time-aware, type-distinguishable |
| AI settings storage | localStorage now, backend migration later | Ship UI first |
| Physical storage | `teams/{team_id}/projects/{project_id}/{files,scripts,storyboard,output}/` | Follows existing convention |

## Display Code Format

```
P-202603001          Project (monthly seq per team)
S-202603001-001      Script (seq within project)
B-202603001-001      Storyboard (seq within project)
O-202603001-001      Output (seq within project)
```

Generated via `display_code_counters` table with `(team_id, year_month, prefix)` composite key.

## Architecture

### Before
```
Sidebar:
  ├── Projects       → project list (files only)
  ├── Storyboard     → separate storyboard project list
  └── ...
```

### After
```
Sidebar:
  ├── Projects       → unified project list
  └── ...            (Storyboard entry removed)

Project Detail:
  ├── Files          → asset management (existing, optimized)
  ├── Scripts        → script list → script canvas editor
  ├── Storyboard     → storyboard list → storyboard canvas editor
  └── Output         → export management

  Secondary (settings/more):
  ├── Tasks
  ├── Share
  └── Recycle Bin
```

### Routing

```
/team/:teamId/projects                                    → Project list
/team/:teamId/projects/:projectId                         → Project detail (Files tab)
/team/:teamId/projects/:projectId/files                   → Files
/team/:teamId/projects/:projectId/scripts                 → Script list
/team/:teamId/projects/:projectId/scripts/:scriptId       → Script canvas editor
/team/:teamId/projects/:projectId/storyboard              → Storyboard list
/team/:teamId/projects/:projectId/storyboard/:storyboardId → Storyboard canvas editor
/team/:teamId/projects/:projectId/output                  → Output list
```

## Database Schema

### Modified Tables

```sql
-- projects: add display_code and modules
ALTER TABLE projects
  ADD COLUMN display_code text,
  ADD COLUMN modules_enabled text[] DEFAULT '{files,scripts,storyboard,output}';

-- storyboard_projects: link to parent project
ALTER TABLE storyboard_projects
  ADD COLUMN project_id bigint REFERENCES projects(id),
  ADD COLUMN display_code text;
```

### New Tables

```sql
-- Script projects (1:N with projects)
CREATE TABLE script_projects (
  id bigint PRIMARY KEY,
  project_id bigint NOT NULL REFERENCES projects(id),
  display_code text,
  name text NOT NULL,
  description text,
  settings_json jsonb DEFAULT '{}',
  viewport_json jsonb,
  status text DEFAULT 'active',
  created_by uuid REFERENCES auth.users(id),
  created_at timestamptz DEFAULT now(),
  updated_at timestamptz DEFAULT now()
);

-- Script chapters (tree structure via parent_chapter_id)
CREATE TABLE script_chapters (
  id bigint PRIMARY KEY,
  script_id bigint NOT NULL REFERENCES script_projects(id),
  parent_chapter_id bigint REFERENCES script_chapters(id),
  chapter_number int,
  title text,
  summary text,
  content text,
  branch_label text,
  branch_type text,
  position_x float DEFAULT 0,
  position_y float DEFAULT 0,
  width float,
  height float,
  data_json jsonb DEFAULT '{}',
  sort_order int DEFAULT 0,
  created_at timestamptz DEFAULT now(),
  updated_at timestamptz DEFAULT now()
);

-- Script assets (worldview, characters, locations, props, plot points)
CREATE TABLE script_assets (
  id bigint PRIMARY KEY,
  script_id bigint NOT NULL REFERENCES script_projects(id),
  asset_type text NOT NULL,
  name text NOT NULL,
  content text,
  data_json jsonb DEFAULT '{}',
  sort_order int DEFAULT 0,
  created_at timestamptz DEFAULT now(),
  updated_at timestamptz DEFAULT now()
);

-- Script-to-Storyboard linkage
CREATE TABLE script_storyboard_links (
  id bigint PRIMARY KEY,
  chapter_id bigint NOT NULL REFERENCES script_chapters(id),
  storyboard_node_id bigint NOT NULL REFERENCES storyboard_nodes(id),
  created_at timestamptz DEFAULT now()
);

-- Style templates (prompt library, team-scoped)
CREATE TABLE style_templates (
  id bigint PRIMARY KEY,
  team_id bigint REFERENCES teams(id),
  name text NOT NULL,
  description text,
  prompt_content text NOT NULL,
  category text,
  is_public boolean DEFAULT false,
  created_by uuid REFERENCES auth.users(id),
  created_at timestamptz DEFAULT now(),
  updated_at timestamptz DEFAULT now()
);

-- Display code sequence generator
CREATE TABLE display_code_counters (
  team_id bigint NOT NULL REFERENCES teams(id),
  year_month text NOT NULL,
  prefix text NOT NULL,
  current_seq int DEFAULT 0,
  PRIMARY KEY (team_id, year_month, prefix)
);
```

### Entity Relationships

```
projects (1)
├── script_projects (N)
│   ├── script_chapters (N, tree via parent_chapter_id)
│   ├── script_assets (N)
│   └── script_storyboard_links → storyboard_nodes
├── storyboard_projects (N, via new project_id FK)
│   ├── storyboard_nodes (N)
│   ├── storyboard_edges (N)
│   └── storyboard_frames (N)
├── projects_files (N, existing)
└── style_templates (N, team-level shared)
```

## Physical Storage

```
{DOWNLOAD_PATH}/teams/{team_id}/projects/{project_id}/
├── files/
│   ├── uploads/              user-uploaded assets
│   └── collected/            references from resource library
├── scripts/
│   └── {script_id}/
│       ├── chapters/         chapter content (JSON/HTML)
│       └── assets/           script attachments (character images, etc.)
├── storyboard/
│   └── {storyboard_id}/
│       ├── generated/        AI-generated images
│       ├── uploads/          user-uploaded canvas images
│       └── thumbnails/       preview thumbnails
└── output/
    └── {output_id}/          exported files (PDF, ZIP, MP4)
```

## Script Editor Components

```
ScriptEditorPage
├── ScriptAssetsSidebar        left panel: plot summary, worldview, characters,
│                               locations, props, plot points
├── ScriptCanvas (ReactFlow)   center: chapter node tree
│   ├── ChapterNode            title + rich text + summary + action buttons
│   └── BranchEdge             branch connections
├── CreateStoryDialog          story premise → AI outline generation
├── ExpandChapterDialog        AI expansion from summary
└── CreateBranchDialog         AI branch generation (2/3/4 branches)
```

### Chapter Node Features
- Title with chapter number badge
- Heading levels: H1, H2, H3, H4, Body
- Rich text editor (bold, italic)
- AI summary display
- "Expand" button → AI generates full chapter from summary
- "Create Branch" button → AI generates alternative story paths
- Branch types: condition-type, choice-type

### Script-to-Storyboard Flow
```
Chapter → "Convert to Storyboard" →
  AI splits chapter into scene descriptions →
  Creates storyboard frame nodes in linked storyboard →
  Records link in script_storyboard_links
```

## Implementation Phases

| Phase | Scope | Key Files |
|-------|-------|-----------|
| **P1** | Project architecture integration — merge entries, routing, sidebar, storyboard migration, project detail tabs | Sidebar, router, ProjectDetailPage, migration |
| **P2** | Script editor foundation — canvas, chapter nodes, rich text, AI outline generation | ScriptEditorPage, ChapterNode, CreateStoryDialog |
| **P3** | AI expansion + branching system | ExpandChapterDialog, CreateBranchDialog, AI endpoints |
| **P4** | Script assets panel + script-to-storyboard linkage | ScriptAssetsSidebar, conversion API |
| **P5** | Output module + style template library | OutputPage, StyleTemplateDialog |

## AI Settings Integration

The existing AI Settings page (already implemented) supports:
- **Text/LLM tab**: OpenAI, DeepSeek, Qwen, etc. → used for script generation/expansion
- **Image Generation tab**: KIE, PPIO, fal, GRSAI, etc. → used for storyboard
- **Task Assignment → Storyboard tab**: multi-select image providers, script/prompt LLM selector

No additional AI settings changes needed for this spec.
