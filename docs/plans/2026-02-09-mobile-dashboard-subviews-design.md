# Mobile Dashboard Sub-views Design

## Overview
Add sub-view switching to the mobile Dashboard tab, matching the Library button's popup menu pattern. Users can switch between 4 views: Overview, Tasks, Logs, and Monitor.

## Sub-views

| Icon | Name | Component | Description |
|------|------|-----------|-------------|
| BarChart3 | Overview | Existing stats + StatsChart | Current dashboard content |
| ListTodo | Tasks | TasksPanel | Download tasks + AI tasks |
| ScrollText | Logs | LogsPanel | Activity logs with filters |
| Activity | Monitor | SystemMonitorPanel | Queue, storage, workers |

## Interaction
- First click: Enter Dashboard → show Overview (default)
- Click again while on Dashboard: Show popup menu with 4 icons
- Select icon: Switch to that sub-view, close menu

## Implementation
1. Add `dashboardSubView` state: `'overview' | 'tasks' | 'logs' | 'monitor'`
2. Mirror `handleMobileLibraryClick` logic for Dashboard button
3. Render popup menu with 4 icon buttons (same style as Library popup)
4. Switch rendered component based on `dashboardSubView`

## Files to Modify
- `frontend/App.tsx` — state, click handler, popup menu, view rendering
