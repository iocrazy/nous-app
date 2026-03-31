# Project Page Redesign — Spec

> Date: 2026-03-31
> Status: Approved

## Overview

Redesign the Projects page with context-aware sidebar and card-based project display.

## Two Views

### View 1: Project List

**Left sidebar** — Filter panel (NOT a project list):
- **View section**: All | Starred | Recent | Active | Archived — each is a filter
- **Folders section**: User-defined folders — each filters right-side cards
- No project items displayed in sidebar
- No search in sidebar (search is on right side)

**Right content** — Card grid:
- Header: title (changes based on active filter, e.g. "⭐ Starred Projects"), count, search box, "+ New Project" button
- Toolbar: sort dropdown, grid/list toggle
- Cards grouped by folder (collapsible), each card shows: cover gradient + emoji, name, status dot, file count, time
- Starred cards show ⭐ badge on cover

### View 2: Inside Project (D+ Rich Dropdown)

**Left sidebar** — Project navigation:
- **Header row**: ← back button + project avatar + name + meta + ▾ chevron
  - Click ← → back to project list
  - Click header row → open dropdown
- **Dropdown** (floating, appears on click):
  - Search input "Switch project..."
  - Starred section with project items
  - Recent section with project items
  - Footer: "📋 All Projects" link
  - Click project → switch to that project (sidebar stays in nav mode)
  - Click "All Projects" → back to list view
- **Nav menu**: Files | Scripts | Storyboard | Output | Tasks | — | Shares | Trash | — | Settings
  - Each item has icon + label + optional badge count
  - Active item: indigo left border + bg highlight
- Tabs removed from top bar — sidebar nav replaces them

**Right content** — Pure content area:
- Header: section title (e.g. "Files") + subtitle (e.g. "12 items · 3.2 GB") + search + action buttons
- Toolbar: filter chips + sort + view toggle
- Content: section-specific content (file grid, storyboard list, etc.)

## Animations

- Sidebar view switch (list → project): slide transition
- Dropdown open: slideDown 150ms
- Project card hover: translateY(-1px) + shadow
- Nav item hover: bg fade 120ms
- Status dot on active projects: subtle pulse

## Files to Modify

- `frontend/pages/ProjectsPage.tsx` — main page with both views
- `frontend/components/ProjectsSidebar.tsx` → rename to `ProjectFilterSidebar.tsx` (filter panel)
- New: `frontend/components/project/ProjectNavSidebar.tsx` (project navigation + D+ dropdown)
- `frontend/components/ProjectsListView.tsx` — adapt card grid for folder grouping
- `frontend/types.ts` — add `ProjectViewFilter` type if needed

## Out of Scope

- Folder CRUD (use existing project_group field)
- Project cover image upload
- Drag-and-drop reorder
