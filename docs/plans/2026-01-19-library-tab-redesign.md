# Library Tab Redesign - Design Document

> **Date:** 2026-01-19
> **Status:** Ready for Implementation

## Overview

Simplify the sidebar by merging "My Library" and "Team Library" into a single "Library" entry with top tabs for switching between personal and team views.

## Current State

- Sidebar has separate "My Library" and "Team Library" menu items
- Team Library shows collections as flat list in sidebar submenu
- No clear team switching mechanism

## Target State

- Single "Library" sidebar entry with smart collection submenu
- Top tab bar: `My Library | Team Library · [TeamName]`
- Avatar dropdown for team switching
- Team Library shows collections as folder cards

---

## Design Details

### 1. State Changes

```typescript
// Remove
- isTeamLibraryOpen
- isTeamLibraryActive
- isLibraryOpen

// Keep (both tabs support)
viewMode: 'grid' | 'table' | 'feed'           // View toggle
searchQuery: string                            // Search keyword
searchMode: 'smart' | 'ai' | 'keyword'         // Search mode

// Add
+ activeTab: 'my-library' | 'team-library'     // Top tab state
+ selectedTeamId: string | null                // Current team
+ activeCollectionId: string | null            // Current collection (null = folder view)
+ isProfileDropdownOpen: boolean               // Avatar dropdown state
```

### 2. Feature Matrix

| Feature | My Library | Team Library (Folders) | Team Library (Inside Collection) |
|---------|------------|------------------------|----------------------------------|
| View Toggle | ✅ | ❌ | ✅ |
| Search/Filter | ✅ | ❌ | ✅ |
| Refresh | ✅ | ✅ | ✅ |

### 3. View Hierarchy

```
Library (sidebar)
  └── activeTab
        ├── 'my-library' → Show videos (grid/table/feed + search)
        └── 'team-library'
              ├── activeCollectionId = null → Show collection folder cards
              └── activeCollectionId = xxx  → Show collection videos (grid/table/feed + search)
```

---

## UI Components

### 3.1 Simplified Sidebar

```tsx
<SidebarItem
  icon={Library}
  label="Library"
  active={view === 'library'}
  onClick={() => setView('library')}
  hasSubmenu
  isOpen={isLibrarySubmenuOpen}
/>

{isLibrarySubmenuOpen && (
  <SmartCollectionsSidebar isInline />
)}
```

### 3.2 Top Tab Bar Component

```tsx
// New component: LibraryTabs.tsx
<div className="flex items-center gap-1 border-b border-zinc-800">
  <button
    className={activeTab === 'my-library' ? 'active' : ''}
    onClick={() => setActiveTab('my-library')}
  >
    My Library
  </button>

  <button
    className={activeTab === 'team-library' ? 'active' : ''}
    onClick={() => setActiveTab('team-library')}
  >
    <Users size={14} />
    Team Library · {currentTeam?.name || 'Select Team'}
  </button>
</div>
```

### 3.3 Profile Dropdown Component

```tsx
// New component: ProfileDropdown.tsx
<div className="absolute right-0 top-full mt-2 w-64 bg-zinc-900 rounded-lg border border-zinc-800">
  {/* User info */}
  <div className="p-4 border-b border-zinc-800">
    <div className="font-medium">{user.name}</div>
    <div className="text-sm text-zinc-500">{user.email}</div>
  </div>

  {/* Teams list */}
  <div className="p-2">
    <div className="text-xs text-zinc-500 px-2 py-1">JOINED TEAMS</div>
    {teams.map(team => (
      <button
        key={team.id}
        onClick={() => setSelectedTeamId(team.id)}
        className={selectedTeamId === team.id ? 'active' : ''}
      >
        <span>{team.name}</span>
        {selectedTeamId === team.id && <Check size={14} />}
      </button>
    ))}
    <button onClick={openCreateTeamModal}>+ Create Team</button>
  </div>

  {/* Other options */}
  <div className="border-t border-zinc-800 p-2">
    <button>Profile</button>
    <button>Account</button>
  </div>
</div>
```

### 3.4 Collection Folder Card Component

```tsx
// New component: CollectionFolderCard.tsx
<div
  className="group cursor-pointer bg-zinc-900 rounded-xl border border-zinc-800
             hover:border-zinc-600 transition-all p-4 aspect-square
             flex flex-col items-center justify-center gap-3"
  onClick={() => setActiveCollectionId(collection.id)}
>
  <div className="w-16 h-16 bg-zinc-800 rounded-xl flex items-center justify-center
                  group-hover:bg-indigo-500/20 transition-colors">
    <Folder size={32} className="text-zinc-500 group-hover:text-indigo-400" />
  </div>
  <span className="text-sm font-medium text-zinc-300">{collection.name}</span>
  <span className="text-xs text-zinc-500">{collection.video_count} videos</span>
</div>
```

### 3.5 New Collection Card

```tsx
// NewCollectionCard.tsx
<div
  className="cursor-pointer bg-zinc-900/50 rounded-xl border border-dashed border-zinc-700
             hover:border-indigo-500 transition-all p-4 aspect-square
             flex flex-col items-center justify-center gap-3"
  onClick={openCreateCollectionModal}
>
  <div className="w-16 h-16 bg-zinc-800/50 rounded-xl flex items-center justify-center">
    <Plus size={32} className="text-zinc-600" />
  </div>
  <span className="text-sm text-zinc-500">New Collection</span>
</div>
```

### 3.6 Team Library Folder Grid

```tsx
{activeTab === 'team-library' && !activeCollectionId && (
  <div className="grid grid-cols-2 md:grid-cols-4 lg:grid-cols-5 xl:grid-cols-6 gap-4 p-6">
    <NewCollectionCard onClick={() => setIsCreateCollectionModalOpen(true)} />
    {teamCollections.map(collection => (
      <CollectionFolderCard
        key={collection.id}
        collection={collection}
        onClick={() => setActiveCollectionId(collection.id)}
      />
    ))}
  </div>
)}
```

### 3.7 Breadcrumb Navigation

```tsx
{activeCollectionId && (
  <div className="flex items-center gap-2 text-sm text-zinc-400 mb-4">
    <button onClick={() => setActiveCollectionId(null)} className="hover:text-white">
      Team Library
    </button>
    <ChevronRight size={14} />
    <span className="text-white">{currentCollection?.name}</span>
  </div>
)}
```

---

## Interaction Flows

### 4.1 Initialization

```
User login
  ↓
Load Teams list
  ↓
Read localStorage preferences
  ↓
selectedTeamId = saved.selectedTeamId || teams[0]?.id || null
activeTab = saved.activeTab || 'my-library'
```

### 4.2 Tab Switching

```
Click "My Library" Tab
  → activeTab = 'my-library'
  → activeCollectionId = null
  → Show all personal videos

Click "Team Library" Tab
  → activeTab = 'team-library'
  → if (!selectedTeamId) prompt to select team
  → else show collection folders
```

### 4.3 Team Switching

```
Click avatar → Open dropdown
  ↓
Click a Team
  ↓
selectedTeamId = team.id
activeCollectionId = null
  ↓
Refresh collection folder list (if on team-library tab)
  ↓
Close dropdown
```

### 4.4 Collection Navigation

```
Team Library Tab (folder view)
  ↓
Click collection folder
  ↓
activeCollectionId = collection.id
  ↓
Show collection videos (with view toggle/search)
  ↓
Click back / breadcrumb "Team Library"
  ↓
activeCollectionId = null
  ↓
Return to folder view
```

---

## State Persistence

```typescript
// localStorage - All UI preferences
const STORAGE_KEY = 'mediahub_library_preferences';

// Save
localStorage.setItem(STORAGE_KEY, JSON.stringify({
  selectedTeamId,
  activeTab,
  viewMode,
}));

// Load (on init)
const saved = JSON.parse(localStorage.getItem(STORAGE_KEY) || '{}');
const [selectedTeamId, setSelectedTeamId] = useState(saved.selectedTeamId || null);
const [activeTab, setActiveTab] = useState(saved.activeTab || 'my-library');
const [viewMode, setViewMode] = useState(saved.viewMode || 'grid');
```

---

## Files to Modify/Create

### New Components
- `frontend/components/LibraryTabs.tsx`
- `frontend/components/ProfileDropdown.tsx`
- `frontend/components/CollectionFolderCard.tsx`
- `frontend/components/NewCollectionCard.tsx`
- `frontend/components/TeamLibraryView.tsx`

### Modify
- `frontend/App.tsx` - Sidebar simplification, state changes, tab integration
- `frontend/components/SmartCollectionsSidebar.tsx` - Remove Team Library specific logic

---

## Implementation Order

1. Create new components (LibraryTabs, ProfileDropdown, CollectionFolderCard)
2. Modify App.tsx state management
3. Update sidebar structure
4. Implement tab switching logic
5. Implement team switching in dropdown
6. Implement collection folder view
7. Add localStorage persistence
8. Test all flows
